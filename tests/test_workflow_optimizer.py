import tempfile
import unittest
from pathlib import Path

from scripts.workflow_optimizer import (
    advise,
    finish_attempt,
    record_attempt,
    report_data,
    start_attempt,
)


class WorkflowOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "workflow.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_repeated_ats_failure_changes_retry_to_handoff(self):
        for index in range(2):
            record_attempt(
                path=self.db,
                stage="ats_fill",
                platform="greenhouse",
                outcome="failure",
                barrier="ats_widget_failure",
                duration_seconds=400,
                interaction_count=18,
                source_ref=f"failure-{index}",
            )

        guidance = advise(
            path=self.db,
            stage="ats_fill",
            platform="greenhouse",
            barrier="ats_widget_failure",
        )

        self.assertEqual(guidance["decision"], "handoff")
        self.assertEqual(guidance["action"], "manual_handoff")
        self.assertEqual(guidance["learned_rule"]["failure_count"], 2)

    def test_azure_failure_applies_before_another_attempt(self):
        record_attempt(
            path=self.db,
            stage="resume_tailoring",
            platform="azure",
            outcome="failure",
            barrier="azure_bad_json",
            source_ref="azure-failure",
        )

        guidance = advise(path=self.db, stage="resume_tailoring", platform="azure")

        self.assertEqual(guidance["decision"], "avoid")
        self.assertEqual(guidance["action"], "use_codex")

    def test_timed_attempt_records_elapsed_effort(self):
        started = start_attempt(path=self.db, stage="ats_upload", platform="ashby")
        self.assertTrue(started["started"])

        finished = finish_attempt(
            path=self.db,
            attempt_id=started["attempt_id"],
            outcome="success",
            duration_seconds=20,
            interaction_count=2,
        )
        report = report_data(path=self.db)

        self.assertTrue(finished["updated"])
        self.assertEqual(finished["duration_seconds"], 20)
        self.assertEqual(report["outcomes"], {"success": 1})

    def test_source_reference_makes_history_import_idempotent(self):
        first = record_attempt(
            path=self.db,
            stage="posting_verify",
            platform="employer_site",
            outcome="skipped",
            barrier="expired_post",
            source_ref="historical-expired-role",
        )
        second = record_attempt(
            path=self.db,
            stage="posting_verify",
            platform="employer_site",
            outcome="skipped",
            barrier="expired_post",
            source_ref="historical-expired-role",
        )

        self.assertTrue(first[1])
        self.assertFalse(second[1])
        self.assertEqual(first[0], second[0])


if __name__ == "__main__":
    unittest.main()
