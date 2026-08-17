import importlib.util
import json
import sys
import tempfile
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
    def test_classifies_windows_socket_policy_failure(self):
        exc = ats_scan.requests.ConnectionError(
            "[WinError 10013] An attempt was made to access a socket in a way forbidden by its access permissions"
        )

        self.assertEqual(ats_scan.classify_request_error(exc), "network_access_denied")

    def test_writes_snapshot_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ats_snapshot.json"
            ats_scan.write_snapshot(path, {"schema_version": 1, "status": "ok"})

            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "ok")
            self.assertFalse(path.with_name(f"{path.name}.tmp").exists())

    @patch.object(ats_scan.requests, "get")
    def test_fetch_ashby(self, mock_get):
        mock_get.return_value = response(
            {"jobs": [{
                "title": "Data Engineer",
                "location": "New York, NY",
                "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                "publishedAt": "2026-08-11T12:00:00Z",
                "descriptionPlain": "Build data pipelines.",
                "employmentType": "FullTime",
            }]}
        )
        jobs = ats_scan.fetch_ashby(ats_scan.CompanyConfig("Acme", "ashby", board="acme"))
        self.assertEqual(jobs[0]["posted_at"], "2026-08-11T12:00:00Z")
        self.assertEqual(jobs[0]["job_description"], "Build data pipelines.")
        self.assertTrue(jobs[0]["direct_employer"])

    @patch.object(ats_scan.requests, "get")
    def test_fetch_lever(self, mock_get):
        mock_get.return_value = response(
            [{
                "text": "Analytics Engineer",
                "hostedUrl": "https://jobs.lever.co/acme/1",
                "categories": {"location": "Remote - US"},
                "createdAt": 1786449600000,
                "descriptionPlain": "Own SQL data models.",
            }]
        )
        jobs = ats_scan.fetch_lever(ats_scan.CompanyConfig("Acme", "lever", site="acme"))
        self.assertEqual(jobs[0]["source"], "Lever")
        self.assertEqual(jobs[0]["location"], "Remote - US")
        self.assertTrue(jobs[0]["posted_at"].startswith("2026-08-11"))
        self.assertEqual(jobs[0]["job_description"], "Own SQL data models.")

    @patch.object(ats_scan.requests, "get")
    def test_greenhouse_uses_publication_date_not_update_date(self, mock_get):
        mock_get.return_value = response({"jobs": [{
            "title": "Senior Data Engineer",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
            "location": {"name": "Remote, United States"},
            "first_published": "2026-08-10T12:00:00Z",
            "updated_at": "2026-08-12T12:00:00Z",
            "content": "&lt;p&gt;Build SQL pipelines. Salary: &lt;span&gt;$160,000&lt;/span&gt;-&lt;span&gt;$190,000&lt;/span&gt;.&lt;/p&gt;",
        }]})

        jobs = ats_scan.fetch_greenhouse(ats_scan.CompanyConfig("Acme", "greenhouse", board="acme"))

        self.assertEqual(jobs[0]["posted_at"], "2026-08-10T12:00:00Z")
        self.assertEqual(jobs[0]["updated_at"], "2026-08-12T12:00:00Z")
        self.assertEqual(jobs[0]["job_description"], "Build SQL pipelines. Salary: $160,000 - $190,000.")

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
