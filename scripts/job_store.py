"""SQLite-backed source of truth for the local job-search workflow.

The database stays local and exports the legacy CSV files for compatibility with
the existing dashboard and scripts.  No personal profile, resume, or email body
is stored by this module beyond the fields already present in the local tracker.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "resume_optimizer.db"
DEFAULT_QUEUE = ROOT / "jobs" / "queue.csv"
DEFAULT_TRACKER = ROOT / "tracker" / "applications.csv"

QUEUE_FIELDS = [
    "company",
    "role",
    "source",
    "url",
    "status",
    "priority",
    "batch_id",
    "match_score",
    "notes",
]
TRACKER_FIELDS = [
    "date",
    "company",
    "role",
    "source",
    "url",
    "status",
    "resume_file",
    "application_folder",
    "submitted",
    "follow_up_date",
    "stage",
    "stage_date",
    "next_action",
    "contact_name",
    "last_contact_date",
    "email_status",
    "email_subject",
    "email_url",
    "email_last_checked",
    "notes",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def normalized_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/")


def identity_key(company: str, role: str, url: str = "", source: str = "") -> str:
    url_key = normalized_url(url)
    if url_key:
        return f"url:{url_key.lower()}"
    return "identity:" + "::".join(
        [normalize_text(company), normalize_text(role), normalize_text(source)]
    )


def default_db_path() -> Path:
    return Path(DEFAULT_DB)


def database_enabled(path: Path | None = None) -> bool:
    return (path or default_db_path()).exists()


@contextmanager
def connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db_path = path or default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(path: Path | None = None) -> Path:
    db_path = path or default_db_path()
    with connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                identity_key TEXT NOT NULL UNIQUE,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                work_mode TEXT NOT NULL DEFAULT '',
                employment_type TEXT NOT NULL DEFAULT '',
                posted_at TEXT NOT NULL DEFAULT '',
                discovered_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                compensation_low INTEGER,
                compensation_high INTEGER,
                direct_employer INTEGER,
                sponsorship_status TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                priority TEXT NOT NULL DEFAULT 'medium',
                batch_id TEXT NOT NULL DEFAULT '',
                role_family TEXT NOT NULL DEFAULT '',
                base_match_score INTEGER,
                calibrated_match_score INTEGER,
                eligibility_json TEXT NOT NULL DEFAULT '{}',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status_priority
                ON jobs(status, priority, calibrated_match_score DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_company_role ON jobs(company, role);

            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY,
                identity_key TEXT NOT NULL UNIQUE,
                job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
                date TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                resume_file TEXT NOT NULL DEFAULT '',
                application_folder TEXT NOT NULL DEFAULT '',
                submitted TEXT NOT NULL DEFAULT '',
                follow_up_date TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT '',
                stage_date TEXT NOT NULL DEFAULT '',
                next_action TEXT NOT NULL DEFAULT '',
                contact_name TEXT NOT NULL DEFAULT '',
                last_contact_date TEXT NOT NULL DEFAULT '',
                email_status TEXT NOT NULL DEFAULT '',
                email_subject TEXT NOT NULL DEFAULT '',
                email_url TEXT NOT NULL DEFAULT '',
                email_last_checked TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
            CREATE INDEX IF NOT EXISTS idx_applications_follow_up ON applications(follow_up_date);

            CREATE TABLE IF NOT EXISTS application_events (
                id INTEGER PRIMARY KEY,
                application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
                job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_events_application ON application_events(application_id, occurred_at);

            CREATE TABLE IF NOT EXISTS resume_variants (
                id INTEGER PRIMARY KEY,
                role_family TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                resume_file TEXT NOT NULL,
                evidence_path TEXT NOT NULL DEFAULT '',
                claims_json TEXT NOT NULL DEFAULT '[]',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ats_field_mappings (
                id INTEGER PRIMARY KEY,
                ats TEXT NOT NULL,
                normalized_field TEXT NOT NULL,
                answer_key TEXT NOT NULL,
                match_patterns_json TEXT NOT NULL DEFAULT '[]',
                requires_exact_wording INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                UNIQUE(ats, normalized_field)
            );

            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                task_name TEXT PRIMARY KEY,
                interval_minutes INTEGER NOT NULL,
                last_run_at TEXT NOT NULL DEFAULT '',
                next_run_at TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS remote_approvals (
                id INTEGER PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                url TEXT NOT NULL,
                scope TEXT NOT NULL,
                question TEXT NOT NULL DEFAULT '',
                proposed_answer TEXT NOT NULL DEFAULT '',
                answer_value TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                expires_at TEXT NOT NULL,
                request_email_url TEXT NOT NULL DEFAULT '',
                decision_email_url TEXT NOT NULL DEFAULT '',
                decision_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_remote_approvals_lookup
                ON remote_approvals(company, role, url, scope, status, expires_at);

            CREATE TABLE IF NOT EXISTS workflow_attempts (
                id INTEGER PRIMARY KEY,
                source_ref TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT 'in_progress',
                barrier TEXT NOT NULL DEFAULT '',
                action_taken TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                duration_seconds REAL,
                interaction_count INTEGER,
                token_estimate INTEGER,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workflow_attempts_stage_platform
                ON workflow_attempts(stage, platform, started_at);
            CREATE INDEX IF NOT EXISTS idx_workflow_attempts_outcome
                ON workflow_attempts(outcome, started_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_attempts_source_ref
                ON workflow_attempts(source_ref) WHERE source_ref <> '';

            CREATE TABLE IF NOT EXISTS workflow_learned_rules (
                signature TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT '',
                barrier TEXT NOT NULL,
                decision TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                observation_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                avg_duration_seconds REAL,
                avg_interaction_count REAL,
                token_estimate_total INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workflow_rules_stage_platform
                ON workflow_learned_rules(stage, platform, expires_at);
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (1, utc_now()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (2, utc_now()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (3, utc_now()),
        )
        approval_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(remote_approvals)")
        }
        for column in ("question", "proposed_answer", "answer_value"):
            if column not in approval_columns:
                conn.execute(
                    f"ALTER TABLE remote_approvals ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (4, utc_now()),
        )
    return db_path


def _row_values(row: sqlite3.Row) -> dict[str, str]:
    return {key: "" if row[key] is None else str(row[key]) for key in row.keys()}


def _read_csv(path: Path, fields: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{field: row.get(field) or "" for field in fields} for row in csv.DictReader(handle)]


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _job_id(conn: sqlite3.Connection, company: str, role: str, url: str) -> int | None:
    key = identity_key(company, role, url)
    row = conn.execute("SELECT id FROM jobs WHERE identity_key = ?", (key,)).fetchone()
    if row:
        return int(row["id"])
    row = conn.execute(
        "SELECT id FROM jobs WHERE lower(company) = ? AND lower(role) = ? ORDER BY id DESC LIMIT 1",
        (normalize_text(company), normalize_text(role)),
    ).fetchone()
    return int(row["id"]) if row else None


def _legacy_identity_match(
    conn: sqlite3.Connection,
    table: str,
    company: str,
    role: str,
    url: str,
) -> sqlite3.Row | None:
    """Find one pre-URL-key row without merging distinct reposts."""
    if table not in {"jobs", "applications"}:
        raise ValueError(f"Unsupported identity table: {table}")
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE lower(company) = ? AND lower(role) = ? ORDER BY id",
        (normalize_text(company), normalize_text(role)),
    ).fetchall()
    if url:
        url_matches = [row for row in rows if normalized_url(row["url"]) == normalized_url(url)]
        if len(url_matches) == 1:
            return url_matches[0]
        active_statuses = {
            "analyzed",
            "application_started",
            "blocked_needs_user_input",
            "manual_apply_needed",
            "pending_remote_approval",
            "queued",
            "resume_ready",
        }
        if len(rows) == 1 and normalize_text(rows[0]["status"]) in active_statuses:
            return rows[0]
        return None
    return rows[0] if len(rows) == 1 else None


def consolidate_company_role(
    company: str,
    role: str,
    preferred_url: str,
    *,
    path: Path | None = None,
) -> dict[str, int]:
    """Merge URL-wrapper duplicates while preserving application event history."""
    if not company.strip() or not role.strip() or not preferred_url.strip():
        raise ValueError("company, role, and preferred_url are required")
    initialize(path)
    key = identity_key(company, role, preferred_url)
    params = (normalize_text(company), normalize_text(role))
    with connection(path) as conn:
        jobs = conn.execute(
            "SELECT * FROM jobs WHERE lower(company)=? AND lower(role)=? ORDER BY id",
            params,
        ).fetchall()
        applications = conn.execute(
            "SELECT * FROM applications WHERE lower(company)=? AND lower(role)=? ORDER BY id",
            params,
        ).fetchall()
        keeper_job = next((row for row in jobs if row["identity_key"] == key), None)
        keeper_application = next(
            (row for row in applications if row["identity_key"] == key), None
        )
        if keeper_job is None and jobs:
            keeper_job = max(jobs, key=lambda row: row["updated_at"])
        if keeper_application is None and applications:
            keeper_application = max(applications, key=lambda row: row["updated_at"])

        removed_jobs = 0
        removed_applications = 0
        if keeper_job is not None:
            merged_job = dict(keeper_job)
            for row in jobs:
                for column in row.keys():
                    if column in {"id", "identity_key", "url", "status", "notes", "updated_at"}:
                        continue
                    if merged_job.get(column) in (None, "") and row[column] not in (None, ""):
                        merged_job[column] = row[column]
            merged_job.update({
                "identity_key": key,
                "url": preferred_url.strip(),
                "updated_at": utc_now(),
            })

            if keeper_application is not None:
                merged_application = dict(keeper_application)
                for row in applications:
                    for column in row.keys():
                        if column in {
                            "id", "identity_key", "job_id", "url", "status", "notes",
                            "stage", "next_action", "updated_at",
                        }:
                            continue
                        if merged_application.get(column) in (None, "") and row[column] not in (None, ""):
                            merged_application[column] = row[column]
                merged_application.update({
                    "identity_key": key,
                    "job_id": keeper_job["id"],
                    "url": preferred_url.strip(),
                    "updated_at": utc_now(),
                })
                duplicate_application_ids = [
                    row["id"] for row in applications if row["id"] != keeper_application["id"]
                ]
                for duplicate_id in duplicate_application_ids:
                    conn.execute(
                        "UPDATE application_events SET application_id=? WHERE application_id=?",
                        (keeper_application["id"], duplicate_id),
                    )
                    conn.execute("DELETE FROM applications WHERE id=?", (duplicate_id,))
                removed_applications = len(duplicate_application_ids)
                columns = [column for column in merged_application if column != "id"]
                conn.execute(
                    f"UPDATE applications SET {', '.join(f'{column}=?' for column in columns)} WHERE id=?",
                    [merged_application[column] for column in columns] + [keeper_application["id"]],
                )

            duplicate_job_ids = [row["id"] for row in jobs if row["id"] != keeper_job["id"]]
            for duplicate_id in duplicate_job_ids:
                conn.execute(
                    "UPDATE application_events SET job_id=? WHERE job_id=?",
                    (keeper_job["id"], duplicate_id),
                )
                conn.execute(
                    "UPDATE applications SET job_id=? WHERE job_id=?",
                    (keeper_job["id"], duplicate_id),
                )
                conn.execute("DELETE FROM jobs WHERE id=?", (duplicate_id,))
            removed_jobs = len(duplicate_job_ids)
            columns = [column for column in merged_job if column != "id"]
            conn.execute(
                f"UPDATE jobs SET {', '.join(f'{column}=?' for column in columns)} WHERE id=?",
                [merged_job[column] for column in columns] + [keeper_job["id"]],
            )
        return {
            "removed_jobs": removed_jobs,
            "removed_applications": removed_applications,
        }


def upsert_job(
    values: dict[str, Any], *, path: Path | None = None, conn: sqlite3.Connection | None = None
) -> int:
    if conn is None:
        initialize(path)
        with connection(path) as managed:
            return upsert_job(values, path=path, conn=managed)
    else:
        company = str(values.get("company") or "").strip()
        role = str(values.get("role") or "").strip()
        if not company or not role:
            raise ValueError("company and role are required")
        url = str(values.get("url") or "").strip()
        source = str(values.get("source") or "")
        key = identity_key(company, role, url, source)
        now = utc_now()
        current = conn.execute("SELECT * FROM jobs WHERE identity_key = ?", (key,)).fetchone()
        if current is None:
            current = _legacy_identity_match(conn, "jobs", company, role, url)
        payload = {
            "company": company,
            "role": role,
            "source": source,
            "url": url,
            "location": str(values.get("location") or ""),
            "work_mode": str(values.get("work_mode") or ""),
            "employment_type": str(values.get("employment_type") or ""),
            "posted_at": str(values.get("posted_at") or ""),
            "discovered_at": str(values.get("discovered_at") or now),
            "expires_at": str(values.get("expires_at") or ""),
            "compensation_low": values.get("compensation_low"),
            "compensation_high": values.get("compensation_high"),
            "direct_employer": values.get("direct_employer"),
            "sponsorship_status": str(values.get("sponsorship_status") or ""),
            "status": str(values.get("status") or "queued"),
            "priority": str(values.get("priority") or "medium"),
            "batch_id": str(values.get("batch_id") or ""),
            "role_family": str(values.get("role_family") or ""),
            "base_match_score": values.get("base_match_score", values.get("match_score")),
            "calibrated_match_score": values.get("calibrated_match_score", values.get("match_score")),
            "eligibility_json": json.dumps(values.get("eligibility", values.get("eligibility_json", {})), ensure_ascii=False)
            if not isinstance(values.get("eligibility_json"), str)
            else str(values.get("eligibility_json")),
            "evidence_json": json.dumps(values.get("evidence", values.get("evidence_json", {})), ensure_ascii=False)
            if not isinstance(values.get("evidence_json"), str)
            else str(values.get("evidence_json")),
            "notes": str(values.get("notes") or ""),
            "updated_at": now,
        }
        if current:
            merged = {name: current[name] for name in current.keys()}
            merged["identity_key"] = key
            for name, value in payload.items():
                if value not in (None, ""):
                    merged[name] = value
            update_fields = ["identity_key", *payload]
            assignments = ", ".join(f"{name} = ?" for name in update_fields)
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                [merged[name] for name in update_fields] + [current["id"]],
            )
            job_id = int(current["id"])
        else:
            columns = ["identity_key", *payload.keys(), "created_at"]
            conn.execute(
                f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [key, *[payload[name] for name in payload], now],
            )
            job_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        return job_id


def upsert_application(
    values: dict[str, Any], *, path: Path | None = None, conn: sqlite3.Connection | None = None,
    event_type: str = "state_updated",
) -> int:
    if conn is None:
        initialize(path)
        with connection(path) as managed:
            return upsert_application(values, path=path, conn=managed, event_type=event_type)
    else:
        company = str(values.get("company") or "").strip()
        role = str(values.get("role") or "").strip()
        if not company or not role:
            raise ValueError("company and role are required")
        url = str(values.get("url") or "").strip()
        source = str(values.get("source") or "")
        key = identity_key(company, role, url, source)
        now = utc_now()
        current = conn.execute("SELECT * FROM applications WHERE identity_key = ?", (key,)).fetchone()
        if current is None:
            current = _legacy_identity_match(conn, "applications", company, role, url)
        job_id = _job_id(conn, company, role, url)
        payload = {field: str(values.get(field) or "") for field in TRACKER_FIELDS}
        payload["company"] = company
        payload["role"] = role
        payload["source"] = source or payload["source"]
        payload["url"] = url or payload["url"]
        payload["date"] = payload["date"] or date.today().isoformat()
        payload["updated_at"] = now
        if current:
            merged = {name: current[name] for name in current.keys()}
            merged["identity_key"] = key
            for name, value in payload.items():
                if value:
                    merged[name] = value
            if job_id is not None:
                merged["job_id"] = job_id
            update_fields = ["identity_key", *TRACKER_FIELDS, "job_id", "updated_at"]
            assignments = ", ".join(f"{name} = ?" for name in update_fields)
            conn.execute(
                f"UPDATE applications SET {assignments} WHERE id = ?",
                [merged[name] for name in update_fields] + [current["id"]],
            )
            application_id = int(current["id"])
        else:
            columns = ["identity_key", "job_id", *TRACKER_FIELDS, "created_at", "updated_at"]
            conn.execute(
                f"INSERT INTO applications ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [key, job_id, *[payload[field] for field in TRACKER_FIELDS], now, now],
            )
            application_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        record_event(
            conn,
            application_id=application_id,
            job_id=job_id,
            event_type=event_type,
            source=source,
            summary=payload.get("notes", ""),
            metadata={"status": payload.get("status", ""), "stage": payload.get("stage", "")},
        )
        return application_id


def record_event(
    conn: sqlite3.Connection,
    *,
    application_id: int | None,
    job_id: int | None,
    event_type: str,
    source: str = "",
    summary: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """INSERT INTO application_events
           (application_id, job_id, event_type, occurred_at, source, summary, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            application_id,
            job_id,
            event_type,
            utc_now(),
            source,
            summary,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )


def transition_job_status(
    url: str,
    status: str,
    *,
    notes: str | None = None,
    stage: str | None = None,
    stage_date: str | None = None,
    next_action: str | None = None,
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
    event_type: str = "job_status_updated",
) -> dict[str, int]:
    """Update one existing job and any linked tracker rows in one transaction."""

    if conn is None:
        initialize(path)
        with connection(path) as managed:
            return transition_job_status(
                url,
                status,
                notes=notes,
                stage=stage,
                stage_date=stage_date,
                next_action=next_action,
                path=path,
                conn=managed,
                event_type=event_type,
            )
    normalized = normalized_url(url)
    matches = [
        row
        for row in conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        if normalized_url(row["url"]) == normalized
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one queue job for URL, found {len(matches)}: {url}")

    job = matches[0]
    now = utc_now()
    job_updates: dict[str, str] = {"status": status, "updated_at": now}
    if notes is not None:
        job_updates["notes"] = notes
    conn.execute(
        f"UPDATE jobs SET {', '.join(f'{field}=?' for field in job_updates)} WHERE id=?",
        [*job_updates.values(), job["id"]],
    )

    applications = conn.execute(
        "SELECT * FROM applications WHERE job_id=? OR identity_key=? ORDER BY id",
        (job["id"], identity_key(job["company"], job["role"], job["url"])),
    ).fetchall()
    for application in applications:
        app_updates: dict[str, str] = {"status": status, "updated_at": now}
        if notes is not None:
            app_updates["notes"] = notes
        if stage is not None:
            app_updates["stage"] = stage
        if stage_date is not None:
            app_updates["stage_date"] = stage_date
        if next_action is not None:
            app_updates["next_action"] = next_action
        conn.execute(
            f"UPDATE applications SET {', '.join(f'{field}=?' for field in app_updates)} WHERE id=?",
            [*app_updates.values(), application["id"]],
        )
        record_event(
            conn,
            application_id=int(application["id"]),
            job_id=int(job["id"]),
            event_type="state_updated",
            source=str(job["source"] or ""),
            summary=notes or "",
            metadata={"status": status, "stage": stage or ""},
        )

    record_event(
        conn,
        application_id=None,
        job_id=int(job["id"]),
        event_type=event_type,
        source=str(job["source"] or ""),
        summary=notes or "",
        metadata={"status": status},
    )
    return {"jobs": 1, "applications": len(applications)}


def record_application_state(
    job_values: dict[str, Any], application_values: dict[str, Any], *, path: Path | None = None
) -> None:
    initialize(path)
    with connection(path) as conn:
        job_id = upsert_job(job_values, conn=conn)
        application_values = dict(application_values)
        application_values.setdefault("company", job_values.get("company", ""))
        application_values.setdefault("role", job_values.get("role", ""))
        application_values.setdefault("source", job_values.get("source", ""))
        application_values.setdefault("url", job_values.get("url", ""))
        upsert_application(application_values, conn=conn)
        record_event(
            conn,
            application_id=None,
            job_id=job_id,
            event_type="job_state_updated",
            source=str(job_values.get("source") or ""),
            summary=str(job_values.get("notes") or ""),
            metadata={"status": job_values.get("status", "")},
        )


def import_legacy_csv(
    *,
    queue_path: Path = DEFAULT_QUEUE,
    tracker_path: Path = DEFAULT_TRACKER,
    path: Path | None = None,
) -> dict[str, int]:
    initialize(path)
    queue_rows = _read_csv(queue_path, QUEUE_FIELDS)
    tracker_rows = _read_csv(tracker_path, TRACKER_FIELDS)
    with connection(path) as conn:
        for row in queue_rows:
            score = row.get("match_score", "")
            try:
                parsed_score: int | None = int(score) if score else None
            except ValueError:
                parsed_score = None
            upsert_job({**row, "match_score": parsed_score}, conn=conn)
        for row in tracker_rows:
            upsert_application(row, conn=conn, event_type="legacy_csv_import")
    return {"jobs": len(queue_rows), "applications": len(tracker_rows)}


def queue_rows(path: Path | None = None) -> list[dict[str, str]]:
    initialize(path)
    with connection(path) as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    output: list[dict[str, str]] = []
    for row in rows:
        values = _row_values(row)
        output.append(
            {
                "company": values["company"],
                "role": values["role"],
                "source": values["source"],
                "url": values["url"],
                "status": values["status"],
                "priority": values["priority"],
                "batch_id": values["batch_id"],
                "match_score": values["calibrated_match_score"] or values["base_match_score"],
                "notes": values["notes"],
            }
        )
    return output


def tracker_rows(path: Path | None = None) -> list[dict[str, str]]:
    initialize(path)
    with connection(path) as conn:
        rows = conn.execute("SELECT * FROM applications ORDER BY id").fetchall()
    return [{field: _row_values(row).get(field, "") for field in TRACKER_FIELDS} for row in rows]


def export_legacy_csv(
    *,
    queue_path: Path = DEFAULT_QUEUE,
    tracker_path: Path = DEFAULT_TRACKER,
    path: Path | None = None,
) -> None:
    _write_csv(queue_path, QUEUE_FIELDS, queue_rows(path))
    _write_csv(tracker_path, TRACKER_FIELDS, tracker_rows(path))


def sync_queue_csv(path: Path = DEFAULT_QUEUE, *, db_path: Path | None = None) -> None:
    if not database_enabled(db_path):
        return
    rows = _read_csv(path, QUEUE_FIELDS)
    with connection(db_path) as conn:
        for row in rows:
            score = row.get("match_score", "")
            try:
                parsed_score: int | None = int(score) if score else None
            except ValueError:
                parsed_score = None
            upsert_job({**row, "match_score": parsed_score}, conn=conn)


def sync_tracker_csv(path: Path = DEFAULT_TRACKER, *, db_path: Path | None = None) -> None:
    if not database_enabled(db_path):
        return
    rows = _read_csv(path, TRACKER_FIELDS)
    with connection(db_path) as conn:
        for row in rows:
            upsert_application(row, conn=conn, event_type="legacy_csv_sync")


def upsert_resume_variant(
    role_family: str,
    *,
    name: str,
    resume_file: str,
    evidence_path: str,
    claims: list[str],
    path: Path | None = None,
) -> None:
    initialize(path)
    now = utc_now()
    with connection(path) as conn:
        conn.execute(
            """INSERT INTO resume_variants
               (role_family, name, resume_file, evidence_path, claims_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(role_family) DO UPDATE SET
                 name=excluded.name, resume_file=excluded.resume_file,
                 evidence_path=excluded.evidence_path, claims_json=excluded.claims_json,
                 active=1, updated_at=excluded.updated_at""",
            (role_family, name, resume_file, evidence_path, json.dumps(claims), now, now),
        )


def resume_variant(role_family: str, *, path: Path | None = None) -> dict[str, Any] | None:
    initialize(path)
    with connection(path) as conn:
        row = conn.execute(
            "SELECT * FROM resume_variants WHERE role_family = ? AND active = 1", (role_family,)
        ).fetchone()
    if not row:
        return None
    values = dict(row)
    values["claims"] = json.loads(values.pop("claims_json") or "[]")
    return values


def seed_ats_mappings(path: Path | None = None) -> int:
    mappings = [
        ("greenhouse", "first_name", "standard_fields.first_name", ["first name"]),
        ("greenhouse", "last_name", "standard_fields.last_name", ["last name"]),
        ("greenhouse", "email", "standard_fields.email", ["email"]),
        ("greenhouse", "phone", "standard_fields.phone", ["phone"]),
        ("lever", "first_name", "standard_fields.first_name", ["first name"]),
        ("lever", "last_name", "standard_fields.last_name", ["last name"]),
        ("lever", "email", "standard_fields.email", ["email"]),
        ("ashby", "email", "standard_fields.email", ["email"]),
        ("workday", "email", "standard_fields.email", ["email"]),
    ]
    for ats in ("greenhouse", "lever", "ashby", "workday", "linkedin"):
        mappings.extend(
            [
                (
                    ats,
                    "authorized_to_work_us",
                    "work_authorization.authorized_to_work_us",
                    [
                        "are you legally authorized to work in the united states",
                        "are you authorized to work in the united states",
                    ],
                ),
                (
                    ats,
                    "requires_sponsorship_now",
                    "work_authorization.requires_sponsorship_now",
                    ["do you currently require sponsorship to work in the united states"],
                ),
                (
                    ats,
                    "requires_sponsorship_future",
                    "work_authorization.requires_sponsorship_future",
                    [
                        "will you now or in the future require sponsorship to work in the united states",
                        "will you require sponsorship now or in the future",
                    ],
                ),
            ]
        )
    initialize(path)
    now = utc_now()
    with connection(path) as conn:
        for ats, field, answer_key, patterns in mappings:
            conn.execute(
                """INSERT INTO ats_field_mappings
                   (ats, normalized_field, answer_key, match_patterns_json, requires_exact_wording, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?)
                   ON CONFLICT(ats, normalized_field) DO UPDATE SET
                     answer_key=excluded.answer_key, match_patterns_json=excluded.match_patterns_json,
                     requires_exact_wording=excluded.requires_exact_wording, updated_at=excluded.updated_at""",
                (ats, field, answer_key, json.dumps(patterns), now),
            )
    return len(mappings)


def configure_schedule(
    task_name: str,
    interval_minutes: int,
    *,
    notes: str = "",
    path: Path | None = None,
) -> None:
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    initialize(path)
    with connection(path) as conn:
        conn.execute(
            """INSERT INTO scheduled_tasks(task_name, interval_minutes, notes)
               VALUES (?, ?, ?)
               ON CONFLICT(task_name) DO UPDATE SET interval_minutes=excluded.interval_minutes,
                 notes=excluded.notes, enabled=1""",
            (task_name, interval_minutes, notes),
        )
        row = conn.execute(
            "SELECT last_run_at FROM scheduled_tasks WHERE task_name = ?",
            (task_name,),
        ).fetchone()
        last_run_at = str(row["last_run_at"] or "") if row else ""
        if last_run_at:
            parsed = datetime.fromisoformat(last_run_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            next_run = parsed.astimezone(timezone.utc) + timedelta(minutes=interval_minutes)
            conn.execute(
                "UPDATE scheduled_tasks SET next_run_at = ? WHERE task_name = ?",
                (next_run.replace(microsecond=0).isoformat(), task_name),
            )


def mark_schedule_run(task_name: str, *, path: Path | None = None) -> None:
    initialize(path)
    now = utc_now()
    with connection(path) as conn:
        row = conn.execute(
            "SELECT interval_minutes FROM scheduled_tasks WHERE task_name = ?", (task_name,)
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown scheduled task: {task_name}")
        next_run = datetime.fromisoformat(now).timestamp() + int(row["interval_minutes"]) * 60
        conn.execute(
            "UPDATE scheduled_tasks SET last_run_at = ?, next_run_at = ? WHERE task_name = ?",
            (now, datetime.fromtimestamp(next_run, timezone.utc).replace(microsecond=0).isoformat(), task_name),
        )


def outcome_metrics(*, path: Path | None = None, min_observations: int = 5) -> dict[str, Any]:
    initialize(path)
    with connection(path) as conn:
        rows = conn.execute(
            """SELECT a.status, j.source, j.role_family, j.calibrated_match_score
               FROM applications a LEFT JOIN jobs j ON j.id = a.job_id"""
        ).fetchall()
    groups: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        source = str(row["source"] or "unknown")
        family = str(row["role_family"] or "unclassified")
        bucket = groups.setdefault((source, family), {"applications": 0, "interviews": 0, "offers": 0, "rejections": 0})
        bucket["applications"] += 1
        status = str(row["status"] or "")
        if status in {"interview", "offer"}:
            bucket["interviews"] += 1
        if status == "offer":
            bucket["offers"] += 1
        if status in {"rejected", "rejected_low_match", "expired", "outdated"}:
            bucket["rejections"] += 1
    total = sum(value["applications"] for value in groups.values())
    total_interviews = sum(value["interviews"] for value in groups.values())
    baseline = total_interviews / total if total else 0.0
    breakdown = []
    for (source, family), value in sorted(groups.items()):
        rate = value["interviews"] / value["applications"] if value["applications"] else 0.0
        adjustment = 0
        if value["applications"] >= min_observations:
            adjustment = max(-5, min(5, round((rate - baseline) * 25)))
        breakdown.append({
            "source": source,
            "role_family": family,
            **value,
            "interview_rate": round(rate, 3),
            "score_adjustment": adjustment,
            "calibrated": value["applications"] >= min_observations,
        })
    return {"applications": total, "baseline_interview_rate": round(baseline, 3), "groups": breakdown}


def score_adjustment(source: str, role_family: str, *, path: Path | None = None) -> int:
    metrics = outcome_metrics(path=path)
    for group in metrics["groups"]:
        if group["source"] == source and group["role_family"] == role_family:
            return int(group["score_adjustment"])
    return 0
