import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_application_pipeline  # noqa: E402


class PipelinePathTests(unittest.TestCase):
    def test_candidate_slug_comes_from_private_answer_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "application_answers.json"
            profile.write_text(
                json.dumps(
                    {
                        "standard_fields": {
                            "first_name": "Test",
                            "last_name": "Candidate",
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                run_application_pipeline.candidate_name_slug(profile),
                "Test_Candidate",
            )

    def test_resume_filename_accepts_portable_candidate_slug(self):
        paths = run_application_pipeline.build_paths(
            "Example Company",
            "Data Engineer",
            candidate_slug="Test Candidate",
        )
        self.assertEqual(
            paths["resume_name"],
            "Test_Candidate_Example_Company_Data_Engineer_Resume.docx",
        )


if __name__ == "__main__":
    unittest.main()
