import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import private_state  # noqa: E402


class PrivateStateTests(unittest.TestCase):
    def test_encrypted_round_trip_restores_files_and_database(self):
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as target_tmp:
            source = Path(source_tmp)
            target = Path(target_tmp)
            facts = source / "profile" / "facts.md"
            facts.parent.mkdir(parents=True)
            facts.write_text("# Private facts\n", encoding="utf-8")

            database = source / private_state.SQLITE_PATH
            database.parent.mkdir(parents=True)
            conn = sqlite3.connect(database)
            try:
                conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
                conn.execute("INSERT INTO sample VALUES (?)", ("verified",))
                conn.commit()
            finally:
                conn.close()

            payload = private_state.build_payload(source)
            encrypted = private_state.encrypt_payload(payload, "correct horse battery staple")
            verification = private_state.inspect_payload(
                private_state.decrypt_payload(encrypted, "correct horse battery staple")
            )
            restored = private_state.restore_payload(
                private_state.decrypt_payload(encrypted, "correct horse battery staple"),
                target,
            )

            self.assertEqual(verification["verified_files"], 2)
            self.assertEqual(restored["restored_files"], 2)
            self.assertEqual(
                (target / "profile" / "facts.md").read_text(encoding="utf-8"),
                "# Private facts\n",
            )
            conn = sqlite3.connect(target / private_state.SQLITE_PATH)
            try:
                value = conn.execute("SELECT value FROM sample").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(value, "verified")

    def test_wrong_passphrase_is_rejected(self):
        encrypted = private_state.encrypt_payload(b"payload", "correct horse battery staple")
        with self.assertRaisesRegex(private_state.PrivateStateError, "authentication failed"):
            private_state.decrypt_payload(encrypted, "incorrect horse battery staple")

    def test_restore_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as target_tmp:
            source = Path(source_tmp)
            target = Path(target_tmp)
            source_file = source / "profile" / "facts.md"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("new", encoding="utf-8")
            target_file = target / "profile" / "facts.md"
            target_file.parent.mkdir(parents=True)
            target_file.write_text("existing", encoding="utf-8")

            payload = private_state.build_payload(source)
            with self.assertRaisesRegex(private_state.PrivateStateError, "Refusing to overwrite"):
                private_state.restore_payload(payload, target)
            self.assertEqual(target_file.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
