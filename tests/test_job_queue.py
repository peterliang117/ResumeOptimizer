import unittest

from scripts.job_queue import batch_progress, sorted_application_rows


class BatchProgressTests(unittest.TestCase):
    def test_application_rows_include_ready_and_started_jobs(self):
        rows = [
            {
                "company": "Ready Example",
                "role": "Data Engineer",
                "status": "resume_ready",
                "priority": "high",
                "match_score": "90",
                "url": "https://example.test/ready",
            },
            {
                "company": "Started Example",
                "role": "Analytics Engineer",
                "status": "application_started",
                "priority": "medium",
                "match_score": "91",
                "url": "https://example.test/started",
            },
            {
                "company": "Queued Example",
                "role": "BI Engineer",
                "status": "queued",
                "priority": "high",
                "match_score": "99",
                "url": "https://example.test/queued",
            },
        ]

        applications = sorted_application_rows(rows)

        self.assertEqual(
            [row["company"] for row in applications],
            ["Started Example", "Ready Example"],
        )

    def test_partial_exhausted_batch_is_refill_ready(self):
        rows = [
            {
                "company": "Example",
                "role": "Data Engineer",
                "batch_id": "batch-1",
                "status": "skipped",
            }
        ]

        terminal, open_rows, slots_remaining, refill_ready = batch_progress(
            rows, "batch-1", 10
        )

        self.assertEqual(len(terminal), 1)
        self.assertEqual(open_rows, [])
        self.assertEqual(slots_remaining, 9)
        self.assertTrue(refill_ready)

    def test_partial_batch_with_open_job_is_not_refill_ready(self):
        rows = [
            {
                "company": "Example",
                "role": "Data Engineer",
                "batch_id": "batch-1",
                "status": "queued",
            }
        ]

        terminal, open_rows, slots_remaining, refill_ready = batch_progress(
            rows, "batch-1", 10
        )

        self.assertEqual(terminal, [])
        self.assertEqual(len(open_rows), 1)
        self.assertEqual(slots_remaining, 9)
        self.assertFalse(refill_ready)


if __name__ == "__main__":
    unittest.main()
