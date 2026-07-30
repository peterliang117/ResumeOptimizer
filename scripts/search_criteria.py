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
    preferred_freshness_hours: int = 72
    maximum_freshness_hours: int = 168
    target_pay: int | None = None
    secondary_pay_floor: int | None = None
    secondary_pay_min_score: int = 82
    max_active_per_employer: int = 1
    daily_submission_target: int | None = None
    weekly_submission_min: int | None = None
    weekly_submission_max: int | None = None
    work_mode: str | None = None


def _clean_value(value: str) -> str:
    return value.strip().strip("`").strip()


def _parse_money(value: str) -> int | None:
    match = re.search(r"\$?\s*([0-9][0-9,]*)", value)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _parse_integer(value: str) -> int | None:
    match = re.search(r"\b([0-9]+)\b", value)
    return int(match.group(1)) if match else None


def _parse_range(value: str) -> tuple[int | None, int | None]:
    values = [int(item) for item in re.findall(r"\b([0-9]+)\b", value)]
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], values[0]
    return values[0], values[1]


def _normalize_date_posted(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.lower()
    if "week" in normalized or re.search(r"\b[2-7]\s+days?\b", normalized):
        return "week"
    if "24" in normalized or "day" in normalized or "today" in normalized:
        return "day"
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
        elif stripped.startswith("- Preferred freshness:"):
            parsed = _parse_integer(stripped.split(":", 1)[1])
            if parsed is not None:
                criteria.preferred_freshness_hours = parsed
        elif stripped.startswith("- Maximum freshness:"):
            parsed = _parse_integer(stripped.split(":", 1)[1])
            if parsed is not None:
                criteria.maximum_freshness_hours = parsed * 24 if "day" in stripped.lower() else parsed
        elif stripped.startswith("- Target pay:"):
            criteria.target_pay = _parse_money(stripped.split(":", 1)[1])
        elif stripped.startswith("- Secondary pay floor:"):
            criteria.secondary_pay_floor = _parse_money(stripped.split(":", 1)[1])
        elif stripped.startswith("- Secondary pay minimum score:"):
            parsed = _parse_integer(stripped.split(":", 1)[1])
            if parsed is not None:
                criteria.secondary_pay_min_score = parsed
        elif stripped.startswith("- Maximum active applications per employer:"):
            parsed = _parse_integer(stripped.split(":", 1)[1])
            if parsed is not None:
                criteria.max_active_per_employer = parsed
        elif stripped.startswith("- Daily submission target:"):
            criteria.daily_submission_target = _parse_integer(stripped.split(":", 1)[1])
        elif stripped.startswith("- Weekly submission target:"):
            low, high = _parse_range(stripped.split(":", 1)[1])
            criteria.weekly_submission_min = low
            criteria.weekly_submission_max = high
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
