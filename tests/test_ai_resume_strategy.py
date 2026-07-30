import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from scripts.ai_resume_strategy import (
    build_selection_report,
    canonical_text,
    phrase_present,
    validate_edit_against_strategy,
)
from scripts.match_score import keyword_score


class AIResumeStrategyTests(unittest.TestCase):
    def test_phrase_matching_normalizes_plurals(self):
        self.assertTrue(phrase_present("Built production data pipelines", "data pipeline"))
        self.assertEqual(canonical_text("Data pipelines"), "data pipeline")

    def test_match_score_recognizes_plural_pipeline_evidence(self):
        score, matched, missing, transferable = keyword_score(
            "Build production data pipelines.",
            "Built and operated 70 production data pipelines.",
            ["data pipeline"],
        )
        self.assertEqual(score, 100)
        self.assertEqual(matched, ["data pipeline"])
        self.assertEqual(missing, [])
        self.assertEqual(transferable, [])

    def test_report_separates_supported_transferable_and_unsupported(self):
        job = """
Requirements
- Build data pipelines with SQL and Python.
- Hands-on Snowflake and Kafka experience required.
Preferred
- Tableau
"""
        resume = [
            SimpleNamespace(text="Built 70 ETL pipelines using SQL Server and Python."),
            SimpleNamespace(text="Created Tableau dashboards and governed data quality."),
        ]
        profile = "Snowflake is a transferable workflow; do not claim hands-on Snowflake experience."
        report = build_selection_report(job, resume, profile)
        by_name = {item["criterion"]: item for item in report["criteria"]}
        self.assertEqual(by_name["data pipelines"]["status"], "supported")
        self.assertEqual(by_name["sql"]["status"], "supported")
        self.assertEqual(by_name["snowflake"]["status"], "transferable")
        self.assertEqual(by_name["kafka"]["status"], "unsupported")
        self.assertEqual(by_name["tableau"]["priority"], "nice")

    def test_edit_cannot_turn_transferable_tool_into_claim(self):
        report = {
            "criteria": [
                {
                    "criterion": "snowflake",
                    "status": "transferable",
                    "job_terms": ["snowflake"],
                }
            ]
        }
        edit = {"original": "Built SQL pipelines.", "suggested": "Built Snowflake SQL pipelines."}
        self.assertTrue(validate_edit_against_strategy(edit, report))


if __name__ == "__main__":
    unittest.main()
