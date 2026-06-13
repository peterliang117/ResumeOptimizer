from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from linkedin_search import build_linkedin_jobs_url, validate_linkedin_jobs_url


class LinkedInSearchUrlTests(unittest.TestCase):
    def test_query_fields_remain_separate(self) -> None:
        url = build_linkedin_jobs_url(
            keywords="Data Engineer",
            location="New York City, NY",
            date_posted="day",
            min_salary=120000,
            easy_apply=False,
        )
        params = parse_qs(urlparse(url).query)

        self.assertEqual(params["keywords"], ["Data Engineer"])
        self.assertEqual(params["location"], ["New York City, NY"])
        self.assertEqual(params["f_TPR"], ["r86400"])
        self.assertEqual(params["salary"], ["120000"])

    def test_rejects_encoded_query_separators_inside_keywords(self) -> None:
        broken = (
            "https://www.linkedin.com/jobs/search/"
            "?keywords=Data+Engineer%26location%3DNew+York+City%2C+NY"
            "%26f_TPR%3Dr86400%26salary%3D120000"
        )

        with self.assertRaisesRegex(ValueError, "missing query parameters"):
            validate_linkedin_jobs_url(broken)


if __name__ == "__main__":
    unittest.main()
