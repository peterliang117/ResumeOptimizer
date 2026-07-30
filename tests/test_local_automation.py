import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import local_automation  # noqa: E402


class LocalAutomationTests(unittest.TestCase):
    def test_config_file_and_environment_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "automation.json"
            config_path.write_text(json.dumps({"interval_minutes": 120, "prepare_packets": False}), encoding="utf-8")
            with patch.dict(os.environ, {"RESUME_AUTOMATION_PREPARE_PACKETS": "true"}, clear=False):
                config = local_automation.load_config(config_path)
            self.assertEqual(config.interval_minutes, 120)
            self.assertTrue(config.prepare_packets)

    def test_lock_rejects_overlapping_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "run.lock"
            with local_automation.run_lock(lock, stale_minutes=1):
                with self.assertRaises(RuntimeError):
                    with local_automation.run_lock(lock, stale_minutes=1):
                        pass
            self.assertFalse(lock.exists())

    def test_dry_run_never_launches_subprocess(self):
        config = local_automation.AutomationConfig()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(local_automation, "DEFAULT_LOG", Path(tmp) / "automation.log"):
                with patch.object(local_automation, "repo_path", return_value=Path(tmp) / "missing-events.json"):
                    with patch("local_automation.subprocess.run") as run:
                        logger = local_automation.configure_logging()
                        result = local_automation.run_workflow(config, dry_run=True, logger=logger)
                        for handler in logger.handlers:
                            handler.close()
                            logger.removeHandler(handler)
        run.assert_not_called()
        self.assertFalse(result["failed_steps"])
        commands = [step.get("command", []) for step in result["steps"]]
        joined = " ".join(" ".join(command) for command in commands)
        self.assertNotIn("ats_adapter.py", joined)
        self.assertNotIn("submit", joined.lower())


if __name__ == "__main__":
    unittest.main()
