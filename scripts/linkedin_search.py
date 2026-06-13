#!/usr/bin/env python3
"""Build LinkedIn job-search URLs for browser-assisted discovery."""

from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from search_criteria import read_search_criteria


DATE_FILTERS = {
    "day": "r86400",
    "week": "r604800",
    "month": "r2592000",
}


def build_linkedin_jobs_url(
    keywords: str,
    location: str,
    date_posted: str,
    min_salary: int | None,
    easy_apply: bool,
) -> str:
    params = {
        "keywords": keywords,
        "location": location,
        "f_TPR": DATE_FILTERS[date_posted],
    }
    if min_salary:
        params["salary"] = str(min_salary)
    if easy_apply:
        params["f_AL"] = "true"
    url = f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"
    validate_linkedin_jobs_url(url)
    return url


def validate_linkedin_jobs_url(url: str) -> None:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    required = {"keywords", "location", "f_TPR"}
    missing = sorted(required - params.keys())
    if parsed.netloc != "www.linkedin.com" or parsed.path != "/jobs/search/":
        raise ValueError(f"Unexpected LinkedIn search URL: {url}")
    if missing:
        raise ValueError(f"LinkedIn URL is missing query parameters: {', '.join(missing)}")

    keywords = params["keywords"][0]
    embedded_parameters = ("&location=", "&f_TPR=", "&salary=", "&f_AL=")
    if any(parameter in keywords for parameter in embedded_parameters):
        raise ValueError(
            "LinkedIn query parameters were encoded into the keywords value. "
            "Use the generated URL directly; do not replace '&' separators with '%26' or '&amp;'."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print a LinkedIn jobs search URL.")
    parser.add_argument("--criteria", type=Path, default=Path("profile/search_criteria.md"))
    parser.add_argument("--keywords")
    parser.add_argument("--location", action="append", help="Can be passed more than once.")
    parser.add_argument("--date-posted", choices=DATE_FILTERS.keys())
    parser.add_argument("--min-salary", type=int)
    parser.add_argument("--easy-apply", action="store_true")
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open each generated URL directly in the default browser.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print generated searches as JSON instead of plain URLs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    criteria = read_search_criteria(args.criteria)
    keyword_terms = [args.keywords] if args.keywords else criteria.keyword_terms or [criteria.keywords]
    keyword_terms = [item for item in keyword_terms if item]
    locations = args.location or criteria.locations
    date_posted = args.date_posted or criteria.date_posted
    min_salary = args.min_salary if args.min_salary is not None else criteria.target_pay

    missing = []
    if not keyword_terms:
        missing.append("--keywords")
    if not locations:
        missing.append("--location")
    if not date_posted:
        missing.append("--date-posted")
    if missing:
        raise SystemExit(
            "Missing search criteria: "
            + ", ".join(missing)
            + f". Pass CLI values or fill {args.criteria} from profile/search_criteria.example.md."
        )

    searches = []
    for keywords in keyword_terms:
        for location in locations:
            url = build_linkedin_jobs_url(
                keywords=keywords,
                location=location,
                date_posted=date_posted,
                min_salary=min_salary,
                easy_apply=args.easy_apply,
            )
            searches.append({"keywords": keywords, "location": location, "url": url})

    if args.json:
        print(json.dumps(searches, indent=2))
    else:
        for search in searches:
            print(search["url"])

    if args.open:
        for search in searches:
            if not webbrowser.open_new_tab(search["url"]):
                raise SystemExit(f"Could not open browser for: {search['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
