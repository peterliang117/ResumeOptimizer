import unittest

from scripts.job_queue import batch_progress


class BatchProgressTests(unittest.TestCase):
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
