#!/usr/bin/env python3
"""Scan ATS job feeds into the local queue."""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from job_queue import read_rows, write_rows


ALLOWED_GREENHOUSE_HOSTS = {
    "boards-api.greenhouse.io",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "job-boards.eu.greenhouse.io",
}
ALLOWED_ASHBY_HOSTS = {"api.ashbyhq.com", "jobs.ashbyhq.com"}
ALLOWED_LEVER_HOSTS = {"api.lever.co", "jobs.lever.co"}
WORKDAY_HOST_SUFFIXES = (".myworkdayjobs.com", ".workdayjobs.com")


@dataclass
class CompanyConfig:
    name: str
    provider: str
    enabled: bool = True
    board: str | None = None
    api: str | None = None
    tenant: str | None = None
    site: str | None = None
    search: str | None = None


@dataclass
class PortalConfig:
    title_keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    companies: list[CompanyConfig] = field(default_factory=list)


def read_config(path: Path) -> PortalConfig:
    if not path.exists():
        raise SystemExit(f"Portal config not found: {path}. Copy profile/portals.example.yml locally.")

    section: str | None = None
    title_keywords: list[str] = []
    negative_keywords: list[str] = []
    locations: list[str] = []
    companies: list[CompanyConfig] = []
    current_company: dict[str, str] | None = None

    def flush_company() -> None:
        nonlocal current_company
        if not current_company:
            return
        name = current_company.get("name", "").strip()
        provider = current_company.get("provider", "").strip().lower()
        if name and provider:
            companies.append(
                CompanyConfig(
                    name=name,
                    provider=provider,
                    enabled=current_company.get("enabled", "true").strip().lower() != "false",
                    board=current_company.get("board"),
                    api=current_company.get("api"),
                    tenant=current_company.get("tenant"),
                    site=current_company.get("site"),
                    search=current_company.get("search"),
                )
            )
        current_company = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if not raw_line.startswith(" ") and stripped.endswith(":"):
            if section == "companies":
                flush_company()
            section = stripped[:-1]
            continue
        if section in {"title_keywords", "negative_keywords", "locations"} and stripped.startswith("- "):
            value = stripped[2:].strip().strip('"').strip("'")
            if section == "title_keywords":
                title_keywords.append(value)
            elif section == "negative_keywords":
                negative_keywords.append(value)
            else:
                locations.append(value)
            continue
        if section == "companies":
            if stripped.startswith("- "):
                flush_company()
                current_company = {}
                remainder = stripped[2:].strip()
                if ":" in remainder:
                    key, value = remainder.split(":", 1)
                    current_company[key.strip()] = value.strip().strip('"').strip("'")
                continue
            if current_company is not None and ":" in stripped:
                key, value = stripped.split(":", 1)
                current_company[key.strip()] = value.strip().strip('"').strip("'")
    if section == "companies":
        flush_company()

    return PortalConfig(
        title_keywords=title_keywords,
        negative_keywords=negative_keywords,
        locations=locations,
        companies=companies,
    )


def assert_greenhouse_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_GREENHOUSE_HOSTS:
        raise SystemExit(f"Untrusted Greenhouse URL: {url}")
    return url


def assert_provider_url(url: str, allowed_hosts: set[str], provider: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise SystemExit(f"Untrusted {provider} URL: {url}")
    return url


def assert_workday_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if parsed.scheme != "https" or not hostname.endswith(WORKDAY_HOST_SUFFIXES):
        raise SystemExit(f"Untrusted Workday URL: {url}")
    return url


def greenhouse_api_url(company: CompanyConfig) -> str:
    if company.api:
        return assert_greenhouse_url(company.api)
    if company.board:
        board = re.sub(r"[^A-Za-z0-9_-]", "", company.board)
        return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    raise SystemExit(f"Greenhouse company missing board or api: {company.name}")


def strip_html(value: str | None) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|li|div|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def iso_from_millis(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def infer_work_mode(location: str, explicit: str = "") -> str:
    text = f"{location} {explicit}".lower()
    if "remote" in text:
        return "Remote"
    if "hybrid" in text:
        return "Hybrid"
    if location:
        return "On-site or unspecified"
    return ""


def relative_posted_at(value: str | None, *, now: datetime | None = None) -> str:
    """Convert Workday's relative age label without treating updates as publication dates."""
    text = (value or "").strip().lower()
    reference = now or datetime.now(timezone.utc)
    if "today" in text or "just posted" in text:
        return reference.isoformat()
    match = re.search(r"(\d+)\s+(hour|hours|day|days)\s+ago", text)
    if not match:
        return ""
    amount = int(match.group(1))
    delta = timedelta(hours=amount) if match.group(2).startswith("hour") else timedelta(days=amount)
    return (reference - delta).isoformat()


def fetch_greenhouse(company: CompanyConfig) -> list[dict[str, Any]]:
    url = greenhouse_api_url(company)
    response = requests.get(url, params={"content": "true"}, timeout=20, allow_redirects=False)
    response.raise_for_status()
    payload = response.json()
    jobs = payload.get("jobs", [])
    results: list[dict[str, Any]] = []
    for job in jobs:
        absolute_url = job.get("absolute_url")
        if not absolute_url:
            continue
        posted_at = job.get("first_published") or job.get("created_at") or ""
        location = (job.get("location") or {}).get("name", "")
        results.append(
            {
                "company": company.name,
                "role": job.get("title", ""),
                "source": "Greenhouse",
                "url": absolute_url,
                "location": location,
                "work_mode": infer_work_mode(location),
                "employment_type": "",
                "posted_at": posted_at,
                "freshness_source": "first_published" if job.get("first_published") else "created_at" if job.get("created_at") else "",
                "updated_at": job.get("updated_at") or "",
                "direct_employer": True,
                "job_description": strip_html(job.get("content")),
            }
        )
    return results


def ashby_api_url(company: CompanyConfig) -> str:
    if company.api:
        return assert_provider_url(company.api, ALLOWED_ASHBY_HOSTS, "Ashby")
    if company.board:
        board = re.sub(r"[^A-Za-z0-9_-]", "", company.board)
        return f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
    raise SystemExit(f"Ashby company missing board or api: {company.name}")


def fetch_ashby(company: CompanyConfig) -> list[dict[str, Any]]:
    response = requests.get(ashby_api_url(company), timeout=20, allow_redirects=False)
    response.raise_for_status()
    results: list[dict[str, Any]] = []
    for job in response.json().get("jobs", []):
        url = job.get("jobUrl") or job.get("applyUrl")
        if not url:
            continue
        location = job.get("location", "")
        description = job.get("descriptionPlain") or strip_html(job.get("descriptionHtml"))
        compensation = job.get("compensation") or {}
        compensation_text = compensation.get("compensationTierSummary") or compensation.get("scrapeableCompensationSalarySummary") or ""
        if compensation_text:
            description = f"{description}\n\nCompensation: {compensation_text}".strip()
        results.append(
            {
                "company": company.name,
                "role": job.get("title", ""),
                "source": "Ashby",
                "url": url,
                "location": location,
                "work_mode": infer_work_mode(location, str(job.get("workplaceType") or "")),
                "employment_type": job.get("employmentType") or "",
                "posted_at": job.get("publishedAt") or "",
                "freshness_source": "publishedAt" if job.get("publishedAt") else "",
                "updated_at": "",
                "direct_employer": True,
                "job_description": description,
            }
        )
    return results


def lever_api_url(company: CompanyConfig) -> str:
    if company.api:
        return assert_provider_url(company.api, ALLOWED_LEVER_HOSTS, "Lever")
    slug = company.site or company.board
    if slug:
        slug = re.sub(r"[^A-Za-z0-9_-]", "", slug)
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"
    raise SystemExit(f"Lever company missing site, board, or api: {company.name}")


def fetch_lever(company: CompanyConfig) -> list[dict[str, Any]]:
    response = requests.get(lever_api_url(company), timeout=20, allow_redirects=False)
    response.raise_for_status()
    results: list[dict[str, Any]] = []
    for job in response.json():
        url = job.get("hostedUrl") or job.get("applyUrl")
        if not url:
            continue
        categories = job.get("categories") or {}
        location = categories.get("location", "")
        sections = [job.get("descriptionPlain") or strip_html(job.get("description")), job.get("additionalPlain") or strip_html(job.get("additional"))]
        for section in job.get("lists") or []:
            sections.append(section.get("text") or "")
            sections.append(strip_html(section.get("content")))
        salary = job.get("salaryRange") or {}
        if salary:
            sections.append(
                f"Salary: {salary.get('min', '')}-{salary.get('max', '')} {salary.get('currency', '')} {salary.get('interval', '')}"
            )
        results.append(
            {
                "company": company.name,
                "role": job.get("text", ""),
                "source": "Lever",
                "url": url,
                "location": location,
                "work_mode": infer_work_mode(location, str(job.get("workplaceType") or "")),
                "employment_type": categories.get("commitment", ""),
                "posted_at": iso_from_millis(job.get("createdAt")),
                "freshness_source": "createdAt" if job.get("createdAt") else "",
                "updated_at": "",
                "direct_employer": True,
                "job_description": "\n\n".join(part.strip() for part in sections if part and part.strip()),
            }
        )
    return results


def workday_api_url(company: CompanyConfig) -> str:
    if not company.api:
        raise SystemExit(f"Workday company missing api: {company.name}")
    return assert_workday_url(company.api)


def workday_job_url(company: CompanyConfig, external_path: str) -> str:
    parsed = urlparse(workday_api_url(company))
    if not company.site:
        raise SystemExit(f"Workday company missing site: {company.name}")
    path = external_path if external_path.startswith("/") else f"/{external_path}"
    return f"{parsed.scheme}://{parsed.netloc}/en-US/{company.site}{path}"


def fetch_workday(company: CompanyConfig) -> list[dict[str, Any]]:
    api_url = workday_api_url(company)
    results: list[dict[str, Any]] = []
    offset = 0
    limit = 20
    while True:
        response = requests.post(
            api_url,
            json={
                "appliedFacets": {},
                "limit": limit,
                "offset": offset,
                "searchText": company.search or "data",
            },
            timeout=20,
            allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        postings = payload.get("jobPostings", [])
        for job in postings:
            external_path = job.get("externalPath")
            if not external_path:
                continue
            locations = job.get("locationsText") or job.get("location") or ""
            posted_label = job.get("postedOn") or ""
            results.append(
                {
                    "company": company.name,
                    "role": job.get("title", ""),
                    "source": "Workday",
                    "url": workday_job_url(company, external_path),
                    "location": locations,
                    "work_mode": infer_work_mode(locations),
                    "employment_type": "",
                    "posted_at": relative_posted_at(posted_label),
                    "freshness_source": "postedOn" if posted_label else "",
                    "updated_at": "",
                    "direct_employer": True,
                    "job_description": "",
                }
            )
        offset += len(postings)
        total = payload.get("total", offset)
        if not postings or offset >= total:
            break
    return results


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
    "workday": fetch_workday,
}


def normalized(text: str) -> str:
    return text.lower()


def include_job(job: dict[str, str], config: PortalConfig) -> bool:
    title = normalized(job.get("role", ""))
    location = normalized(job.get("location", ""))

    if config.title_keywords and not any(normalized(keyword) in title for keyword in config.title_keywords):
        return False
    if config.negative_keywords and any(normalized(keyword) in title for keyword in config.negative_keywords):
        return False
    if config.locations and location and not any(normalized(keyword) in location for keyword in config.locations):
        return False
    return True


def queue_keys(row: dict[str, str]) -> set[tuple[str, str]]:
    url = str(row.get("url") or "").strip().lower()
    company = str(row.get("company") or "").strip().lower()
    role = str(row.get("role") or "").strip().lower()
    keys: set[tuple[str, str]] = set()
    if url:
        keys.add(("url", url))
    if company and role:
        keys.add(("company_role", f"{company}::{role}"))
    return keys


def append_to_queue(queue_path: Path, jobs: list[dict[str, str]], dry_run: bool) -> int:
    rows = read_rows(queue_path)
    existing: set[tuple[str, str]] = set()
    for row in rows:
        existing.update(queue_keys(row))
    added = 0
    for job in jobs:
        row = {
            "company": job["company"],
            "role": job["role"],
            "source": job["source"],
            "url": job["url"],
            "status": "queued",
            "priority": "medium",
            "notes": f"ATS scan location: {job.get('location', '')}",
        }
        keys = queue_keys(row)
        if existing.intersection(keys):
            continue
        rows.append(row)
        existing.update(keys)
        added += 1
    if not dry_run:
        write_rows(queue_path, rows)
    return added


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan ATS feeds and add matching jobs to jobs/queue.csv.")
    parser.add_argument("--config", type=Path, default=Path("profile/portals.yml"))
    parser.add_argument("--queue", type=Path, default=Path("jobs/queue.csv"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print matched jobs as JSON.")
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Atomically write a timestamped ATS discovery snapshot for another local process.",
    )
    parser.add_argument(
        "--verbose-errors",
        action="store_true",
        help="Print full request exceptions instead of classified failure reasons.",
    )
    return parser.parse_args()


def write_snapshot(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def classify_request_error(exc: requests.RequestException) -> str:
    text = str(exc).lower()
    if "winerror 10013" in text or "forbidden by its access permissions" in text:
        return "network_access_denied"
    if "nameresolutionerror" in text or "getaddrinfo failed" in text:
        return "dns_failure"
    if "proxyerror" in text:
        return "proxy_failure"
    if "sslerror" in text or "certificate verify failed" in text:
        return "tls_failure"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return "request_failure"


def main() -> int:
    args = parse_args()
    config = read_config(args.config)
    matched: list[dict[str, str]] = []
    attempted = 0
    succeeded = 0
    failures: list[dict[str, str]] = []
    for company in config.companies:
        if not company.enabled:
            continue
        fetcher = FETCHERS.get(company.provider)
        if not fetcher:
            print(f"Skipping unsupported provider for {company.name}: {company.provider}")
            continue
        attempted += 1
        try:
            jobs = fetcher(company)
        except requests.RequestException as exc:
            reason = classify_request_error(exc)
            failures.append({"company": company.name, "provider": company.provider, "reason": reason})
            detail = str(exc) if args.verbose_errors else reason
            print(f"Could not fetch {company.name}: {detail}")
            continue
        succeeded += 1
        for job in jobs:
            if include_job(job, config):
                matched.append(job)

    added = append_to_queue(args.queue, matched, args.dry_run)
    if args.json:
        print(json.dumps(matched, indent=2))
    else:
        for job in matched:
            print(f"{job['company']}\t{job['role']}\t{job.get('location', '')}\t{job['url']}")
    print(f"Matched {len(matched)} job(s); added {added} new queue row(s).")
    print(
        "ATS fetch summary: "
        f"attempted={attempted} succeeded={succeeded} failed={len(failures)}"
    )
    if args.snapshot:
        if attempted and succeeded == 0 and failures:
            status = "unavailable"
        elif failures:
            status = "partial"
        else:
            status = "ok"
        write_snapshot(
            args.snapshot,
            {
                "schema_version": 1,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "summary": {
                    "attempted": attempted,
                    "succeeded": succeeded,
                    "failed": len(failures),
                    "matched": len(matched),
                    "new_queue_rows_if_applied": added,
                },
                "jobs": matched,
                "failures": failures,
            },
        )
        print(f"Wrote ATS discovery snapshot: {args.snapshot}")
    if attempted and succeeded == 0 and failures:
        print("ATS discovery channel unavailable; use the browser or connector fallback.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
