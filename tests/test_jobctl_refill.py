import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import jobctl  # noqa: E402
from jobctl import approved_candidates, existing_refill_report_keys  # noqa: E402


class JobctlRefillTests(unittest.TestCase):
    def test_approved_candidates_accepts_partial_batch(self):
        report = {"candidates": [{"approved": True}]}

        self.assertEqual(len(approved_candidates(report, 10)), 1)

    def test_approved_candidates_blocks_empty_and_over_target(self):
        with self.assertRaises(SystemExit):
            approved_candidates({"candidates": []}, 10)

        with self.assertRaises(SystemExit):
            approved_candidates({"candidates": [{"approved": True} for _ in range(11)]}, 10)

    def test_existing_refill_report_keys_reads_prior_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "refill_candidates_2026-06-20-01.json"
            report.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "company": "Example Co",
                                "role": "Data Scientist",
                                "url": "https://example.test/job/123",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            keys = existing_refill_report_keys(Path(tmp))

        self.assertIn(("url", "https://example.test/job/123"), keys)
        self.assertIn(("company_role", "example co::data scientist"), keys)

    def test_default_refill_report_path_reuses_empty_report(self):
        original_root = jobctl.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = root / "outputs"
            outputs.mkdir()
            report = outputs / "refill_candidates_2026-06-20-02.json"
            report.write_text(json.dumps({"candidates": []}), encoding="utf-8")
            jobctl.ROOT = root
            try:
                selected = jobctl.default_refill_report_path("2026-06-20-02")
            finally:
                jobctl.ROOT = original_root

        self.assertEqual(selected.name, "refill_candidates_2026-06-20-02.json")


if __name__ == "__main__":
    unittest.main()
