"""Modular job fetcher and ranker supporting pluggable regional/remote sources."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from src.tailor import tailor_local


@dataclass
class JobPosting:
    """Normalized job listing across any job board source."""

    source: str
    company: str
    role: str
    location: str
    url: str
    description: str
    match_score: int = 0
    matched_skills: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


JobFetcher = Callable[[str, int], list[JobPosting]]


def _clean_html(raw: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "tailor-cv/1.0 (personal job search assistant)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _matches_query(haystack: str, query: str) -> bool:
    terms = [t.lower() for t in query.split() if t.strip()]
    return not terms or all(term in haystack.lower() for term in terms)


def fetch_arbeitnow(query: str = "", limit: int = 25) -> list[JobPosting]:
    """Fetch European & remote tech jobs from Arbeitnow's public API."""
    data = _fetch_json("https://www.arbeitnow.com/api/job-board-api")
    results: list[JobPosting] = []

    for item in data.get("data", []):
        title = item.get("title", "")
        desc = _clean_html(item.get("description", ""))
        tags = " ".join(item.get("tags", []))
        if not _matches_query(f"{title} {tags} {desc}", query):
            continue

        results.append(
            JobPosting(
                source="arbeitnow",
                company=item.get("company_name", "Unknown"),
                role=title,
                location=item.get("location", "Remote / EU"),
                url=item.get("url", ""),
                description=desc,
            )
        )
        if len(results) >= limit:
            break
    return results


def fetch_remotive(query: str = "", limit: int = 25) -> list[JobPosting]:
    """Fetch global/US/LATAM/EU remote jobs from Remotive's public API."""
    params = urllib.parse.urlencode({"search": query, "limit": limit})
    data = _fetch_json(f"https://remotive.com/api/remote-jobs?{params}")
    results: list[JobPosting] = []

    for item in data.get("jobs", [])[:limit]:
        results.append(
            JobPosting(
                source="remotive",
                company=item.get("company_name", "Unknown"),
                role=item.get("title", ""),
                location=item.get("candidate_required_location", "Remote"),
                url=item.get("url", ""),
                description=_clean_html(item.get("description", "")),
            )
        )
    return results


def fetch_spanish_tech(query: str = "", limit: int = 25) -> list[JobPosting]:
    """Fetch Spain & EU-remote tech jobs from spanishtoptechcompanies.com."""
    data = _fetch_json("https://spanishtoptechcompanies.com/api/v1/jobs.json")
    items = data if isinstance(data, list) else data.get("jobs", data.get("data", []))
    results: list[JobPosting] = []

    for item in items:
        title = item.get("title", "")
        keywords = item.get("keywords") or []
        roles = item.get("roles") or []
        workplace = item.get("workplace") or ""
        location = item.get("location") or "Spain / Remote"
        searchable = f"{title} {' '.join(keywords)} {' '.join(roles)} {location} {workplace}"

        if not _matches_query(searchable, query):
            continue

        desc_parts = [
            f"Role: {title}",
            f"Company: {item.get('company', 'Unknown')}",
            f"Workplace: {workplace}" if workplace else "",
            f"Keywords: {', '.join(keywords)}" if keywords else "",
            f"Roles: {', '.join(roles)}" if roles else "",
        ]
        meta = {}
        if item.get("ats"):
            meta["ats"] = str(item["ats"])
        if workplace:
            meta["workplace"] = str(workplace)

        results.append(
            JobPosting(
                source="spain",
                company=item.get("company", "Unknown"),
                role=title,
                location=location,
                url=item.get("url", ""),
                description="\n".join(p for p in desc_parts if p),
                metadata=meta,
            )
        )
        if len(results) >= limit:
            break
    return results


def _fetch_eu_remote_wrapper(query: str = "", limit: int = 25) -> list[JobPosting]:
    from src.eu_remote import fetch_eu_remote_jobs

    return fetch_eu_remote_jobs(query=query, limit=limit)


# Registry of available job sources. Add new regional/remote providers here.
JOB_SOURCES: dict[str, JobFetcher] = {
    "eu_remote": _fetch_eu_remote_wrapper,
    "spain": fetch_spanish_tech,
    "arbeitnow": fetch_arbeitnow,
    "remotive": fetch_remotive,
}


def search_jobs(
    cv_data: dict[str, Any],
    query: str = "",
    sources: tuple[str, ...] | None = None,
    limit: int = 10,
) -> list[JobPosting]:
    """Fetch jobs from selected sources and rank them against cv_data."""
    selected_sources = sources or tuple(JOB_SOURCES.keys())
    raw_jobs: list[JobPosting] = []

    for name in selected_sources:
        fetcher = JOB_SOURCES.get(name)
        if not fetcher:
            raise ValueError(f"Unknown job source '{name}'. Available: {', '.join(JOB_SOURCES)}")
        try:
            raw_jobs.extend(fetcher(query, 30))
        except Exception:
            continue

    for job in raw_jobs:
        res = tailor_local(cv_data, f"{job.role}\n{job.description}")
        job.match_score = res.match_score
        job.matched_skills = res.matched_skills

    raw_jobs.sort(key=lambda j: -j.match_score)
    return raw_jobs[:limit]
