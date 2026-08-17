import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("verify_discovery_snapshot", SCRIPTS / "verify_discovery_snapshot.py")
verify = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = verify
SPEC.loader.exec_module(verify)
from match_score import extract_pay_ranges  # noqa: E402
from job_store import connection, initialize  # noqa: E402


class VerifyDiscoverySnapshotTests(unittest.TestCase):
    def test_compensation_parser_accepts_typographic_dash(self):
        ranges = extract_pay_ranges("Annual base salary: $207,485 — $244,100 USD")

        self.assertEqual([(item.low, item.high) for item in ranges], [(207485, 244100)])

    def test_compensation_parser_accepts_k_and_unseparated_amounts(self):
        k_range = extract_pay_ranges("Base salary $160k-$190k annually")
        numeric_range = extract_pay_ranges("Base salary $160000-$190000 annually")

        self.assertEqual([(item.low, item.high) for item in k_range], [(160000, 190000)])
        self.assertEqual([(item.low, item.high) for item in numeric_range], [(160000, 190000)])

    def criteria(self, root: Path) -> Path:
        path = root / "criteria.md"
        path.write_text(
            "\n".join(
                [
                    "- Preferred freshness: 72 hours",
                    "- Maximum freshness: 7 days",
                    "- Target pay: at least `$160,000` base",
                    "- Secondary pay floor: `$145,000` base",
                    "- Secondary pay minimum score: 82/100",
                    "- Minimum target match score: 75/100 after hard filters",
                    "- Maximum active applications per employer: 2",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def run_verify(self, root: Path, payload: dict, *, queue: bool = False) -> dict:
        return verify.verify_snapshot(
            payload,
            resume_text="Senior data engineer with Python, SQL, ETL, warehouse, and data pipeline experience.",
            profile_text="Verified work includes Python, SQL, ETL, data modeling, and data quality.",
            criteria_path=self.criteria(root),
            db_path=root / "workflow.db",
            source_dir=root / "source",
            candidates_dir=root / "candidates",
            reviews_dir=root / "reviews",
            report_path=root / "report.json",
            queue_path=root / "queue.csv",
            tracker_path=root / "tracker.csv",
            queue=queue,
            now=datetime.now(timezone.utc),
        )

    def test_metadata_only_snapshot_records_explicit_evidence_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "schema_version": 1,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "ok",
                "jobs": [{
                    "company": "Acme",
                    "role": "Data Engineer",
                    "source": "Greenhouse",
                    "url": "https://example.test/jobs/1",
                    "location": "Remote, United States",
                }],
            }

            report = self.run_verify(root, payload)

            self.assertEqual(report["summary"], {"candidates": 1, "eligible": 0, "queued": 0, "rejected": 1})
            self.assertIn("missing_exact_job_description", report["decisions"][0]["failures"])
            self.assertIn("missing_live_posted_at", report["decisions"][0]["failures"])
            self.assertTrue((root / "report.json").exists())

    def test_enriched_snapshot_is_scored_saved_and_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_text = """
            Senior Data Engineer. Build Python and SQL data pipelines, ETL systems, warehouse models,
            and data quality controls. Own production data modeling and analytics engineering.
            The base salary range for this full-time role is $165,000-$195,000 annually.
            This role is remote in the United States.
            """
            payload = {
                "schema_version": 1,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "ok",
                "jobs": [{
                    "company": "Acme",
                    "role": "Senior Data Engineer",
                    "source": "Greenhouse",
                    "url": "https://example.test/jobs/2",
                    "location": "Remote, United States",
                    "work_mode": "Remote",
                    "employment_type": "Full-time",
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                    "freshness_source": "first_published",
                    "direct_employer": True,
                    "job_description": job_text,
                }],
            }

            report = self.run_verify(root, payload, queue=True)

            self.assertEqual(report["summary"]["queued"], 1)
            self.assertGreaterEqual(report["decisions"][0]["calibrated_match_score"], 75)
            self.assertTrue(list((root / "source").glob("*.txt")))
            self.assertTrue(list((root / "reviews").glob("*.json")))
            self.assertIn("Acme", (root / "queue.csv").read_text(encoding="utf-8"))

            repeated = self.run_verify(root, payload, queue=True)
            self.assertEqual(repeated["summary"]["queued"], 1)

    def test_snapshot_does_not_requeue_a_submitted_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "schema_version": 1,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "ok",
                "jobs": [{
                    "company": "Acme",
                    "role": "Senior Data Engineer",
                    "source": "Greenhouse",
                    "url": "https://example.test/jobs/terminal",
                    "location": "Remote, United States",
                    "work_mode": "Remote",
                    "employment_type": "Full-time",
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                    "freshness_source": "first_published",
                    "direct_employer": True,
                    "job_description": (
                        "Senior Data Engineer. Build Python and SQL data pipelines, ETL systems, "
                        "warehouse models, and data quality controls. Own production data modeling "
                        "and analytics engineering. Base salary $165,000-$195,000. "
                        "This role is remote in the United States."
                    ),
                }],
            }

            first = self.run_verify(root, payload, queue=True)
            self.assertEqual(first["summary"]["queued"], 1)
            db = root / "workflow.db"
            initialize(db)
            with connection(db) as conn:
                conn.execute("UPDATE jobs SET status='submitted'")

            repeated = self.run_verify(root, payload, queue=True)

            self.assertEqual(repeated["summary"]["queued"], 0)
            self.assertEqual(repeated["decisions"][0]["existing_status"], "submitted")
            with connection(db) as conn:
                self.assertEqual(conn.execute("SELECT status FROM jobs").fetchone()["status"], "submitted")

    def test_stale_snapshot_is_a_channel_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "schema_version": 1,
                "generated_at_utc": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
                "status": "ok",
                "jobs": [],
            }

            report = self.run_verify(root, payload)

            self.assertEqual(report["status"], "channel_failure")
            self.assertEqual(report["channel_failure"], "stale_snapshot")

    def test_long_candidate_title_produces_bounded_unique_slug(self):
        candidate = {
            "company": "Acme",
            "role": "Institutional Portfolio Risk Analytics Vice President " * 8,
            "url": "https://example.test/jobs/very-long-role",
        }

        slug = verify.candidate_slug(candidate)

        self.assertLessEqual(len(slug), 111)
        self.assertRegex(slug, r"_[0-9a-f]{10}$")


if __name__ == "__main__":
    unittest.main()
