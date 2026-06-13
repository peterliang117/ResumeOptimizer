"""Read local-only job search criteria from profile/search_criteria.md."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SearchCriteria:
    source: str | None = None
    keywords: str | None = None
    keyword_terms: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    date_posted: str | None = None
    target_pay: int | None = None
    work_mode: str | None = None


def _clean_value(value: str) -> str:
    return value.strip().strip("`").strip()


def _parse_money(value: str) -> int | None:
    match = re.search(r"\$?\s*([0-9][0-9,]*)", value)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _normalize_date_posted(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.lower()
    if "24" in normalized or "day" in normalized or "today" in normalized:
        return "day"
    if "week" in normalized:
        return "week"
    if "month" in normalized:
        return "month"
    return None


def read_search_criteria(path: Path = Path("profile/search_criteria.md")) -> SearchCriteria:
    criteria = SearchCriteria()
    if not path.exists():
        return criteria

    lines = path.read_text(encoding="utf-8").splitlines()
    in_locations = False
    in_keywords = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            in_locations = False
            in_keywords = False
            continue

        if stripped.startswith("- Keywords:"):
            inline_keywords = _clean_value(stripped.split(":", 1)[1])
            if inline_keywords:
                criteria.keywords = inline_keywords
                criteria.keyword_terms.append(inline_keywords)
            in_keywords = True
            in_locations = False
            continue

        if in_keywords and line.startswith("  - "):
            criteria.keyword_terms.append(_clean_value(stripped[2:]))
            continue

        if stripped.startswith("- Locations:"):
            in_locations = True
            in_keywords = False
            continue

        if in_locations and line.startswith("  - "):
            criteria.locations.append(_clean_value(stripped[2:]))
            continue

        in_locations = False
        in_keywords = False
        if stripped.startswith("- Source:"):
            criteria.source = _clean_value(stripped.split(":", 1)[1])
        elif stripped.startswith("- Date posted:"):
            criteria.date_posted = _normalize_date_posted(stripped.split(":", 1)[1])
        elif stripped.startswith("- Target pay:"):
            criteria.target_pay = _parse_money(stripped.split(":", 1)[1])
        elif stripped.startswith("- Work mode:"):
            criteria.work_mode = _clean_value(stripped.split(":", 1)[1])

    if not criteria.keywords and criteria.keyword_terms:
        criteria.keywords = criteria.keyword_terms[0]
    return criteria


def require_target_pay(value: int | None, criteria_path: Path) -> int:
    if value is not None:
        return value
    criteria = read_search_criteria(criteria_path)
    if criteria.target_pay is not None:
        return criteria.target_pay
    raise SystemExit(
        f"Target pay is required. Pass --target-pay or set it in {criteria_path} "
        "from profile/search_criteria.example.md."
    )
