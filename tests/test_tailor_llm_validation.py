import sys
import unittest
from unittest.mock import patch

from scripts.tailor import (
    LLMUnavailableError,
    ResumeParagraph,
    analyze_resume_tailoring,
    parse_args,
    validate_suggestion_payload,
)


class TailorLLMValidationTests(unittest.TestCase):
    def test_codex_provider_is_the_cli_default(self):
        with patch.object(
            sys,
            "argv",
            ["tailor.py", "--resume", "resume.docx", "--job", "Job description", "--out", "tailored.docx"],
        ):
            self.assertEqual(parse_args().llm_provider, "codex")

    def test_codex_provider_never_calls_external_llms(self):
        with (
            patch("scripts.tailor.analyze_with_azure_openai") as azure,
            patch("scripts.tailor.analyze_with_local_llm") as local,
        ):
            payload = analyze_resume_tailoring(
                [ResumeParagraph(paragraph_id="p1", text="Built SQL data pipelines.")],
                "Build SQL data pipelines.",
                "",
                provider="codex",
                model=None,
            )

        azure.assert_not_called()
        local.assert_not_called()
        self.assertIn("No external LLM request was made", payload["job_summary"])

    def test_rejects_missing_suggestion_schema_keys(self):
        with self.assertRaises(LLMUnavailableError):
            validate_suggestion_payload({"error": {"message": "bad request"}}, source="local LLM")

    def test_accepts_minimal_valid_payload(self):
        payload = {
            "job_summary": "Summary.",
            "must_have_skills": [],
            "nice_to_have_skills": [],
            "matched_evidence": [],
            "suggested_edits": [],
            "confirmation_questions": [],
        }

        self.assertEqual(validate_suggestion_payload(payload, source="local LLM"), payload)


if __name__ == "__main__":
    unittest.main()
