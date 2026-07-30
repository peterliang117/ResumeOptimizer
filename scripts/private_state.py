#!/usr/bin/env python3
"""Export and import an encrypted, portable ResumeOptimizer state bundle."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from pipeline_lock import acquire as acquire_pipeline_lock
from pipeline_lock import release as release_pipeline_lock


ROOT = Path(__file__).resolve().parents[1]
MAGIC = b"RESUME_OPTIMIZER_STATE_V1\n"
ARCHIVE_VERSION = 1
KDF_N = 2**15
KDF_R = 8
KDF_P = 1

PRIVATE_FILES = (
    Path("profile/facts.md"),
    Path("profile/application_answers.json"),
    Path("profile/open_ended_answer_bank.md"),
    Path("profile/search_criteria.md"),
    Path("profile/portals.yml"),
    Path("profile/local_automation.json"),
    Path("profile/evidence_map.private.json"),
    Path("profile/resume_variants.private.json"),
)
PRIVATE_GLOBS = (
    "resumes/*.docx",
    "resumes/*.pdf",
)
OPTIONAL_TREES = (
    Path("applications"),
    Path("tailored_resumes"),
)
SQLITE_PATH = Path("data/resume_optimizer.db")


class PrivateStateError(RuntimeError):
    """Raised when a private-state archive cannot be handled safely."""


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < 12:
        raise PrivateStateError("Passphrase must contain at least 12 characters.")
    kdf = Scrypt(salt=salt, length=32, n=KDF_N, r=KDF_R, p=KDF_P)
    return kdf.derive(passphrase.encode("utf-8"))


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _collect_files(root: Path, *, include_packets: bool) -> list[Path]:
    files: set[Path] = set()
    for relative in PRIVATE_FILES:
        candidate = root / relative
        if candidate.is_file():
            files.add(relative)
    for pattern in PRIVATE_GLOBS:
        for candidate in root.glob(pattern):
            if candidate.is_file():
                files.add(candidate.relative_to(root))
    if include_packets:
        for relative_tree in OPTIONAL_TREES:
            tree = root / relative_tree
            if not tree.exists():
                continue
            for candidate in tree.rglob("*"):
                if candidate.is_file():
                    files.add(candidate.relative_to(root))
    for relative in files:
        candidate = root / relative
        if candidate.is_symlink() or not _is_within_root(candidate, root):
            raise PrivateStateError(f"Refusing unsafe source path: {relative}")
    return sorted(files, key=lambda value: value.as_posix().lower())


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
        result = destination_conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        destination_conn.close()
        source_conn.close()
    if result != "ok":
        raise PrivateStateError(f"SQLite backup integrity check failed: {result}")


def build_payload(root: Path, *, include_packets: bool = False) -> bytes:
    root = root.resolve()
    files = _collect_files(root, include_packets=include_packets)
    manifest_entries: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory(prefix="resume-optimizer-state-") as temp_dir:
        overrides: dict[Path, Path] = {}
        sqlite_source = root / SQLITE_PATH
        if sqlite_source.is_file():
            sqlite_snapshot = Path(temp_dir) / SQLITE_PATH
            _sqlite_backup(sqlite_source, sqlite_snapshot)
            overrides[SQLITE_PATH] = sqlite_snapshot
            files.append(SQLITE_PATH)

        if not files:
            raise PrivateStateError("No private ResumeOptimizer state was found to export.")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative in sorted(set(files), key=lambda value: value.as_posix().lower()):
                source = overrides.get(relative, root / relative)
                data = source.read_bytes()
                archive.writestr(relative.as_posix(), data)
                manifest_entries.append(
                    {
                        "path": relative.as_posix(),
                        "size": len(data),
                        "sha256": _sha256(data),
                    }
                )
            manifest = {
                "archive_version": ARCHIVE_VERSION,
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "includes_application_packets": include_packets,
                "files": manifest_entries,
                "excluded": [
                    "browser sessions",
                    "OAuth tokens",
                    "credentials and API keys",
                    "OTP and authentication codes",
                    "virtual environments",
                    "logs and temporary files",
                ],
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
        return buffer.getvalue()


def encrypt_payload(payload: bytes, passphrase: str) -> bytes:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    metadata = {
        "archive_version": ARCHIVE_VERSION,
        "cipher": "AES-256-GCM",
        "kdf": "scrypt",
        "n": KDF_N,
        "r": KDF_R,
        "p": KDF_P,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
    }
    metadata_bytes = json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode("ascii")
    associated_data = MAGIC + metadata_bytes + b"\n"
    ciphertext = AESGCM(_derive_key(passphrase, salt)).encrypt(nonce, payload, associated_data)
    return associated_data + ciphertext


def decrypt_payload(encrypted: bytes, passphrase: str) -> bytes:
    if not encrypted.startswith(MAGIC):
        raise PrivateStateError("Not a supported ResumeOptimizer state archive.")
    metadata_end = encrypted.find(b"\n", len(MAGIC))
    if metadata_end < 0:
        raise PrivateStateError("Archive metadata is incomplete.")
    metadata_bytes = encrypted[len(MAGIC) : metadata_end]
    associated_data = encrypted[: metadata_end + 1]
    try:
        metadata = json.loads(metadata_bytes.decode("ascii"))
        salt = bytes.fromhex(metadata["salt"])
        nonce = bytes.fromhex(metadata["nonce"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise PrivateStateError("Archive metadata is invalid.") from exc
    if metadata.get("archive_version") != ARCHIVE_VERSION:
        raise PrivateStateError("Archive version is not supported by this checkout.")
    try:
        return AESGCM(_derive_key(passphrase, salt)).decrypt(
            nonce,
            encrypted[metadata_end + 1 :],
            associated_data,
        )
    except InvalidTag as exc:
        raise PrivateStateError("Archive authentication failed; the passphrase or file is incorrect.") from exc


def _validated_archive_entries(payload: bytes) -> tuple[dict[str, bytes], dict]:
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            members = archive.namelist()
            if "manifest.json" not in members:
                raise PrivateStateError("Archive manifest is missing.")
            manifest = json.loads(archive.read("manifest.json"))
            expected = {entry["path"]: entry for entry in manifest.get("files", [])}
            if set(members) != set(expected) | {"manifest.json"}:
                raise PrivateStateError("Archive contents do not match the encrypted manifest.")
            for name, entry in expected.items():
                relative = PurePosixPath(name)
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise PrivateStateError(f"Archive contains an unsafe path: {name}")
                data = archive.read(name)
                if len(data) != entry.get("size") or _sha256(data) != entry.get("sha256"):
                    raise PrivateStateError(f"Archive checksum validation failed: {name}")
                files[name] = data
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, TypeError) as exc:
        if isinstance(exc, PrivateStateError):
            raise
        raise PrivateStateError("Encrypted payload is not a valid state bundle.") from exc
    return files, manifest


def restore_payload(payload: bytes, root: Path, *, force: bool = False) -> dict:
    root = root.resolve()
    files, manifest = _validated_archive_entries(payload)
    destinations = {name: root.joinpath(*PurePosixPath(name).parts) for name in files}
    for name, destination in destinations.items():
        if not _is_within_root(destination, root):
            raise PrivateStateError(f"Archive path escapes the repository: {name}")
    conflicts = [name for name, destination in destinations.items() if destination.exists()]
    if conflicts and not force:
        raise PrivateStateError(
            "Refusing to overwrite existing private state. Export the current state first, "
            "then rerun import with --force."
        )
    for name, destination in destinations.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(files[name])
    restored_db = destinations.get(SQLITE_PATH.as_posix())
    if restored_db:
        conn = sqlite3.connect(restored_db)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if result != "ok":
            raise PrivateStateError(f"Restored SQLite integrity check failed: {result}")
    return {
        "restored_files": len(files),
        "created_at": manifest.get("created_at"),
        "included_application_packets": bool(manifest.get("includes_application_packets")),
    }


def inspect_payload(payload: bytes) -> dict:
    files, manifest = _validated_archive_entries(payload)
    return {
        "verified_files": len(files),
        "created_at": manifest.get("created_at"),
        "included_application_packets": bool(manifest.get("includes_application_packets")),
    }


def _prompt_export_passphrase() -> str:
    first = getpass.getpass("New archive passphrase: ")
    second = getpass.getpass("Confirm archive passphrase: ")
    if first != second:
        raise PrivateStateError("Passphrases do not match.")
    return first


def _with_pipeline_lock(action):
    result = acquire_pipeline_lock(stale_minutes=180)
    if not result.get("acquired"):
        raise PrivateStateError("Another pipeline run is active; retry after it completes.")
    token = str(result["token"])
    try:
        return action()
    finally:
        release_pipeline_lock(token)


def export_archive(args: argparse.Namespace) -> dict:
    destination = args.out or ROOT / "backups" / f"resume_optimizer_state_{_utc_timestamp()}.rostate"
    destination = destination.resolve()
    if destination.exists() and not args.force:
        raise PrivateStateError(f"Archive already exists: {destination}")

    def action() -> dict:
        payload = build_payload(ROOT, include_packets=args.include_packets)
        passphrase = _prompt_export_passphrase()
        encrypted = encrypt_payload(payload, passphrase)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            temporary.write_bytes(encrypted)
            verification = inspect_payload(decrypt_payload(temporary.read_bytes(), passphrase))
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return {
            "archive": str(destination),
            "bytes": destination.stat().st_size,
            **verification,
        }

    return _with_pipeline_lock(action)


def import_archive(args: argparse.Namespace) -> dict:
    source = args.archive.resolve()
    if not source.is_file():
        raise PrivateStateError(f"Archive does not exist: {source}")

    def action() -> dict:
        payload = decrypt_payload(source.read_bytes(), getpass.getpass("Archive passphrase: "))
        return restore_payload(payload, ROOT, force=args.force)

    return _with_pipeline_lock(action)


def verify_archive(args: argparse.Namespace) -> dict:
    source = args.archive.resolve()
    if not source.is_file():
        raise PrivateStateError(f"Archive does not exist: {source}")
    payload = decrypt_payload(source.read_bytes(), getpass.getpass("Archive passphrase: "))
    return {"archive": str(source), **inspect_payload(payload)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export or import encrypted ResumeOptimizer private state."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export", help="Create an encrypted private-state archive.")
    export.add_argument("--out", type=Path)
    export.add_argument("--include-packets", action="store_true")
    export.add_argument("--force", action="store_true")

    restore = commands.add_parser("import", help="Restore an encrypted private-state archive.")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--force", action="store_true")

    verify = commands.add_parser(
        "verify",
        help="Check the passphrase, authentication tag, manifest, and checksums without restoring.",
    )
    verify.add_argument("archive", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        actions = {
            "export": export_archive,
            "import": import_archive,
            "verify": verify_archive,
        }
        result = actions[args.command](args)
    except (OSError, sqlite3.Error, PrivateStateError) as exc:
        print(f"Private-state operation failed: {exc}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
