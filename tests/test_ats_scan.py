import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "ats_scan.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("ats_scan", SCRIPT)
ats_scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = ats_scan
SPEC.loader.exec_module(ats_scan)


def response(payload):
    result = Mock()
    result.json.return_value = payload
    result.raise_for_status.return_value = None
    return result


class AtsScanTests(unittest.TestCase):
    @patch.object(ats_scan.requests, "get")
    def test_fetch_ashby(self, mock_get):
        mock_get.return_value = response(
            {"jobs": [{"title": "Data Engineer", "location": "New York, NY", "jobUrl": "https://jobs.ashbyhq.com/acme/1"}]}
        )
        jobs = ats_scan.fetch_ashby(ats_scan.CompanyConfig("Acme", "ashby", board="acme"))
        self.assertEqual(
            jobs,
            [
                {
                    "company": "Acme",
                    "role": "Data Engineer",
                    "source": "Ashby",
                    "url": "https://jobs.ashbyhq.com/acme/1",
                    "location": "New York, NY",
                }
            ],
        )

    @patch.object(ats_scan.requests, "get")
    def test_fetch_lever(self, mock_get):
        mock_get.return_value = response(
            [{"text": "Analytics Engineer", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {"location": "Remote - US"}}]
        )
        jobs = ats_scan.fetch_lever(ats_scan.CompanyConfig("Acme", "lever", site="acme"))
        self.assertEqual(jobs[0]["source"], "Lever")
        self.assertEqual(jobs[0]["location"], "Remote - US")

    @patch.object(ats_scan.requests, "post")
    def test_fetch_workday_paginates(self, mock_post):
        mock_post.side_effect = [
            response(
                {
                    "total": 2,
                    "jobPostings": [
                        {"title": "Data Engineer", "externalPath": "/job/New-York/Data-Engineer_R1", "locationsText": "New York, NY"}
                    ],
                }
            ),
            response(
                {
                    "total": 2,
                    "jobPostings": [
                        {"title": "Risk Analytics Engineer", "externalPath": "/job/Jersey-City/Risk_R2", "locationsText": "Jersey City, NJ"}
                    ],
                }
            ),
        ]
        company = ats_scan.CompanyConfig(
            "Acme",
            "workday",
            api="https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/jobs",
            site="External",
            search="data engineer",
        )
        jobs = ats_scan.fetch_workday(company)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(
            jobs[0]["url"],
            "https://acme.wd1.myworkdayjobs.com/en-US/External/job/New-York/Data-Engineer_R1",
        )
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_post.call_args_list[0].kwargs["json"]["searchText"], "data engineer")

    def test_rejects_untrusted_provider_urls(self):
        company = ats_scan.CompanyConfig("Acme", "ashby", api="https://example.com/jobs")
        with self.assertRaisesRegex(SystemExit, "Untrusted Ashby URL"):
            ats_scan.ashby_api_url(company)


if __name__ == "__main__":
    unittest.main()
