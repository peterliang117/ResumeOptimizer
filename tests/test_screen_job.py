"""Unit tests for the deterministic job-description screening contract."""

import unittest

from scripts.screen_job import evaluate_job


class ScreenJobTests(unittest.TestCase):
    def assert_screen_result_shape(self, result):
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result["eligible"], bool)
        self.assertIsInstance(result["decision_reason"], str)
        self.assertIsInstance(result["sponsorship_status"], str)
        self.assertIsInstance(result["role_core_summary"], str)
        self.assertIsInstance(result["hard_filter_failures"], list)
        self.assertIsInstance(result["evidence"], dict)

    def failure_text(self, result):
        return " ".join(
            [result["decision_reason"], *map(str, result["hard_filter_failures"])]
        ).lower()

    def test_data_engineer_sql_python_pipeline_description_passes(self):
        job_text = """
        Build reliable batch and streaming data pipelines with Python and SQL.
        Develop data models and warehouse transformations, monitor pipeline
        quality, and partner with analysts to publish trusted datasets.
        """
        result = evaluate_job(job_text, role="Data Engineer")

        self.assert_screen_result_shape(result)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["hard_filter_failures"], [])
        self.assertEqual(result["sponsorship_status"], "not_mentioned_or_possible")

    def test_grc_analyst_role_focused_on_governance_does_not_pass(self):
        job_text = """
        The GRC Analyst owns audit readiness and evidence collection, maintains
        policies, performs access reviews and attestations, tracks POA&M items,
        maps controls to security frameworks, and coordinates with auditors.
        """
        result = evaluate_job(job_text, role="GRC Analyst")

        self.assert_screen_result_shape(result)
        self.assertFalse(result["eligible"])
        self.assertTrue(result["hard_filter_failures"])
        self.assertEqual(result["sponsorship_status"], "not_mentioned_or_possible")
        self.assertTrue(any(
            marker in self.failure_text(result)
            for marker in ("role", "core", "grc", "governance", "mismatch")
        ))

    def test_explicit_h1b_no_sponsorship_requirement_fails(self):
        job_text = """
        Data Engineer responsible for Python and SQL data pipelines and warehouse
        data modeling. Applicants must be authorized to work in the United States
        without current or future H-1B sponsorship; the company will not sponsor.
        """
        result = evaluate_job(job_text, role="Data Engineer")

        self.assert_screen_result_shape(result)
        self.assertFalse(result["eligible"])
        self.assertTrue(result["hard_filter_failures"])
        self.assertEqual(result["sponsorship_status"], "explicit_no_sponsorship")
        self.assertTrue(any(
            marker in self.failure_text(result)
            for marker in ("sponsor", "sponsorship", "h-1b", "authorization", "work")
        ))

    def test_not_eligible_for_visa_sponsorship_fails(self):
        job_text = """
        Staff Data Analyst responsible for SQL and Python data analysis and data
        modeling. This position is not eligible for Visa Sponsorship. Applicants
        must be authorized to work in the United States without the need for
        Visa Sponsorship by the start date of employment.
        """
        result = evaluate_job(job_text, role="Staff Data Analyst")

        self.assertFalse(result["eligible"])
        self.assertEqual(result["sponsorship_status"], "explicit_no_sponsorship")
        self.assertIn(
            "not eligible for visa sponsorship",
            result["evidence"]["no_sponsorship_signals"],
        )

    def test_without_need_for_current_or_future_employer_sponsorship_fails(self):
        job_text = """
        Senior Analytics Engineer responsible for SQL data modeling and data
        infrastructure. Applicants must be authorized to work in the U.S.
        without the need for current or future employer sponsorship.
        """
        result = evaluate_job(job_text, role="Senior Analytics Engineer")

        self.assertFalse(result["eligible"])
        self.assertEqual(result["sponsorship_status"], "explicit_no_sponsorship")
        self.assertIn(
            "without need for sponsorship",
            result["evidence"]["no_sponsorship_signals"],
        )

    def test_staffing_and_recruiting_post_fails(self):
        job_text = """
        We are hiring a technical recruiter to source candidates, manage the
        recruiting pipeline, schedule interviews, and support staffing clients.
        Experience with applicant tracking systems and agency recruiting required.
        """
        result = evaluate_job(job_text, role="Technical Recruiter")

        self.assert_screen_result_shape(result)
        self.assertFalse(result["eligible"])
        self.assertTrue(result["hard_filter_failures"])
        self.assertTrue(any(
            marker in self.failure_text(result)
            for marker in ("staff", "recruit", "role", "core")
        ))

    def test_named_employer_role_is_not_rejected_for_external_recruiter_language(self):
        job_text = """
        An external recruiting firm introduced this Senior Data Engineer role
        at Named Employer. Build Python and SQL data pipelines, own data quality,
        and develop warehouse data models. This is a direct full-time position
        with Named Employer.
        """
        result = evaluate_job(job_text, role="Senior Data Engineer")

        self.assert_screen_result_shape(result)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["evidence"]["staffing_signals"], [])

    def test_generic_analytics_keywords_without_role_core_do_not_pass(self):
        job_text = """
        Join a fast-paced team and use analytics, dashboards, reporting, insights,
        and metrics to support strategic decisions. Strong communication and
        problem-solving skills are required.
        """
        result = evaluate_job(job_text, role="Data Engineer")

        self.assert_screen_result_shape(result)
        self.assertFalse(result["eligible"])
        self.assertTrue(result["hard_filter_failures"])
        self.assertTrue(any(
            marker in self.failure_text(result)
            for marker in ("core", "insufficient", "specific", "match", "pipeline")
        ))


if __name__ == "__main__":
    unittest.main()
