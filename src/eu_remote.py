"""Dedicated fetcher for remote-only jobs open to EU / EMEA / Spain."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from typing import Any

from src.job_fetcher import JobPosting

EU_LOCATION_MARKERS = (
    "europe",
    "eu",
    "emea",
    "spain",
    "españa",
    "worldwide",
    "anywhere",
    "global",
    "remote",
    "cet",
)


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
        headers={"User-Agent": "tailor-cv/1.0 (eu-remote job search)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _matches_query(haystack: str, query: str) -> bool:
    terms = [t.lower() for t in query.split() if t.strip()]
    return not terms or all(term in haystack.lower() for term in terms)


def _is_eu_remote_location(location: str) -> bool:
    low = location.lower()
    if any(blocked in low for blocked in ("us only", "usa only", "north america only", "latam only")):
        return False
    return any(marker in low for marker in EU_LOCATION_MARKERS)


def fetch_eu_remote_jobs(query: str = "", limit: int = 30) -> list[JobPosting]:
    """Aggregate remote jobs open to EU/Spain from SpainTech, Arbeitnow, and Remotive."""
    collected: list[JobPosting] = []
    seen: set[tuple[str, str]] = set()

    def _add(job: JobPosting) -> None:
        key = (job.company.strip().lower(), job.role.strip().lower())
        if key not in seen and job.url:
            seen.add(key)
            collected.append(job)

    # 1. Spanish Top Tech Companies (remote == True / workplace == 'remote')
    try:
        data = _fetch_json("https://spanishtoptechcompanies.com/api/v1/jobs.json")
        items = data if isinstance(data, list) else data.get("jobs", data.get("data", []))
        for item in items:
            if item.get("workplace") != "remote":
                continue
            title = item.get("title", "")
            keywords = item.get("keywords") or []
            roles = item.get("roles") or []
            location = item.get("location") or "EU Remote"
            if not _matches_query(f"{title} {' '.join(keywords)} {' '.join(roles)} {location}", query):
                continue

            desc_parts = [
                f"Role: {title}",
                f"Company: {item.get('company', 'Unknown')}",
                "Workplace: remote",
                f"Location: {location}",
                f"Keywords: {', '.join(keywords)}" if keywords else "",
            ]
            meta = {"workplace": "remote"}
            if item.get("ats"):
                meta["ats"] = str(item["ats"])
            _add(
                JobPosting(
                    source="eu_remote:spain",
                    company=item.get("company", "Unknown"),
                    role=title,
                    location=location,
                    url=item.get("url", ""),
                    description="\n".join(p for p in desc_parts if p),
                    metadata=meta,
                )
            )
    except Exception:
        pass

    # 2. Arbeitnow (remote == True)
    try:
        data = _fetch_json("https://www.arbeitnow.com/api/job-board-api")
        for item in data.get("data", []):
            if not item.get("remote"):
                continue
            title = item.get("title", "")
            desc = _clean_html(item.get("description", ""))
            tags = " ".join(item.get("tags", []))
            if not _matches_query(f"{title} {tags} {desc}", query):
                continue
            _add(
                JobPosting(
                    source="eu_remote:arbeitnow",
                    company=item.get("company_name", "Unknown"),
                    role=title,
                    location=item.get("location", "Europe (Remote)"),
                    url=item.get("url", ""),
                    description=desc,
                    metadata={"workplace": "remote"},
                )
            )
    except Exception:
        pass

    # 3. Remotive (filtered for Europe/EMEA/Worldwide/Spain)
    try:
        params = urllib.parse.urlencode({"search": query, "limit": 40})
        data = _fetch_json(f"https://remotive.com/api/remote-jobs?{params}")
        for item in data.get("jobs", []):
            loc = item.get("candidate_required_location", "")
            if not _is_eu_remote_location(loc):
                continue
            _add(
                JobPosting(
                    source="eu_remote:remotive",
                    company=item.get("company_name", "Unknown"),
                    role=item.get("title", ""),
                    location=loc or "Worldwide",
                    url=item.get("url", ""),
                    description=_clean_html(item.get("description", "")),
                    metadata={"workplace": "remote"},
                )
            )
    except Exception:
        pass

    return collected[:limit]
