import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pipeline_lock  # noqa: E402


class PipelineLockTests(unittest.TestCase):
    def test_lock_is_single_owner_and_token_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pipeline.lock"
            first = pipeline_lock.acquire(path)
            self.assertTrue(first["acquired"])
            self.assertFalse(pipeline_lock.acquire(path)["acquired"])
            with self.assertRaises(PermissionError):
                pipeline_lock.release("wrong", path)
            self.assertTrue(pipeline_lock.release(first["token"], path)["released"])
            self.assertFalse(pipeline_lock.status(path)["active"])


if __name__ == "__main__":
    unittest.main()
