"""Dual-mode CV tailoring engine (free-tier AI + deterministic keyword fallback)."""

from __future__ import annotations

import copy
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Common aliases so local matching catches related terms without hallucinating.
SKILL_ALIASES: dict[str, set[str]] = {
    "automation": {"automation", "selenium", "cypress", "playwright", "automated testing", "e2e"},
    "qa testing": {"qa", "quality assurance", "testing", "manual testing", "regression", "bug"},
    "test planning": {"test plan", "test planning", "test strategy", "test cases", "qa strategy"},
    "database testing": {"database", "db testing", "etl", "data validation", "sql"},
    "sql": {"sql", "postgres", "postgresql", "mysql", "sqlite", "oracle", "tsql"},
    "java": {"java", "spring", "maven", "gradle", "junit", "testng"},
    "linux": {"linux", "bash", "shell", "ubuntu", "unix", "cli"},
    "python": {"python", "pytest", "django", "flask", "fastapi", "pandas"},
    "react": {"react", "reactjs", "react.js", "redux", "next.js", "nextjs", "frontend"},
    "javascript": {"javascript", "js", "typescript", "ts", "node", "nodejs", "node.js"},
}

STOPWORDS = {
    "with", "that", "this", "from", "have", "will", "your", "team", "work", "experience",
    "years", "year", "looking", "role", "position", "company", "skills", "required",
    "preferred", "ability", "strong", "good", "using", "about", "into", "more", "than",
    "para", "con", "como", "sobre", "entre", "anos", "experiencia", "trabajo", "equipo",
    "requisitos", "buscamos", "puesto", "habilidades", "conocimientos",
}


@dataclass
class TailorResult:
    """Output of tailoring a CV against a job description."""

    tailored_data: dict[str, Any]
    mode_used: str
    match_score: int
    matched_skills: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    notes: str = ""


def load_dotenv(env_path: str | Path = ".env") -> None:
    """Load key=value pairs from a .env file into os.environ if not already set."""
    path = Path(env_path)
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]{2,}", text.lower()))


def _skill_matches_jd(skill_name: str, jd_lower: str, jd_tokens: set[str]) -> bool:
    key = skill_name.lower()
    if key in jd_lower:
        return True
    aliases = SKILL_ALIASES.get(key, {key})
    return any(alias in jd_lower or alias in jd_tokens for alias in aliases)


def _score_text_relevance(text: str, jd_tokens: set[str]) -> int:
    tokens = _tokenize(text) - STOPWORDS
    return len(tokens & jd_tokens)


def _extract_missing_keywords(jd_text: str, cv_data: dict[str, Any], limit: int = 8) -> list[str]:
    cv_blob = " ".join(
        [cv_data["summary"]]
        + [s for s, _ in cv_data["skills"]]
        + [b for exp in cv_data["work_experience"] for b in exp["bullets"]]
    ).lower()
    cv_tokens = _tokenize(cv_blob)

    tech_candidates = re.findall(r"\b[A-Za-z][A-Za-z0-9+#.-]{1,20}\b", jd_text)
    counts: dict[str, int] = {}
    for word in tech_candidates:
        low = word.lower().strip(".-")
        if len(low) < 3 or low in STOPWORDS or low in cv_tokens or low in cv_blob:
            continue
        # Prioritize capitalized/tech-looking tokens
        if word[0].isupper() or any(ch in word for ch in ("+", "#", ".")):
            counts[word] = counts.get(word, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return [word for word, _ in ranked[:limit]]


def tailor_local(cv_data: dict[str, Any], job_description: str) -> TailorResult:
    """Deterministically tailor skills and experience bullets using keyword matching."""
    tailored = copy.deepcopy(cv_data)
    jd_lower = job_description.lower()
    jd_tokens = _tokenize(job_description) - STOPWORDS

    matched_skills: list[str] = []
    unmatched_skills: list[list[Any]] = []
    matched_skill_entries: list[list[Any]] = []

    for skill, rating in tailored["skills"]:
        if _skill_matches_jd(skill, jd_lower, jd_tokens):
            matched_skills.append(skill)
            matched_skill_entries.append([skill, rating])
        else:
            unmatched_skills.append([skill, rating])

    # Keep highest-rated matched skills first, then remaining skills in original order.
    matched_skill_entries.sort(key=lambda item: -item[1])
    tailored["skills"] = matched_skill_entries + unmatched_skills

    # Reorder bullets within each role by relevance to the job description.
    for exp in tailored["work_experience"]:
        exp["bullets"] = sorted(
            exp["bullets"],
            key=lambda b: -_score_text_relevance(b, jd_tokens),
        )

    total_skills = max(len(tailored["skills"]), 1)
    match_score = min(100, round((len(matched_skills) / total_skills) * 100))
    missing = _extract_missing_keywords(job_description, tailored)

    return TailorResult(
        tailored_data=tailored,
        mode_used="local_keyword",
        match_score=match_score,
        matched_skills=matched_skills,
        missing_keywords=missing,
        notes="Tailored deterministically via keyword & alias matching.",
    )


def _detect_provider(preferred: str | None) -> tuple[str, str] | None:
    """Return (provider_name, api_key) if available."""
    providers = [
        ("gemini", "GEMINI_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
    ]
    if preferred:
        preferred = preferred.lower()
        for name, env_var in providers:
            if name == preferred:
                key = os.environ.get(env_var, "").strip()
                return (name, key) if key else None
        return None

    for name, env_var in providers:
        key = os.environ.get(env_var, "").strip()
        if key:
            return (name, key)
    return None


def _build_prompt(cv_data: dict[str, Any], job_description: str) -> str:
    payload = {
        "summary": cv_data["summary"],
        "skills": [s for s, _ in cv_data["skills"]],
        "work_experience": [
            {"title": exp["title"], "company": exp["company"], "bullets": exp["bullets"]}
            for exp in cv_data["work_experience"]
        ],
    }
    return (
        "You are a strict, honest CV tailoring assistant.\n"
        "RULES:\n"
        "1. NEVER invent skills, companies, job titles, dates, or metrics not present in the source CV.\n"
        "2. Rewrite 'summary' (max 55 words) to emphasize ONLY real experience & skills from the source CV that match the job description.\n"
        "3. Return 'ordered_skills' containing the EXACT skill names from the source CV, ordered from most relevant to least relevant for the job.\n"
        "4. Return 'ordered_bullets' as a list of lists (same length and exact bullet texts per role, just reordered by relevance).\n"
        "5. Return 'missing_keywords' (up to 6 keywords from the job description absent from the CV).\n"
        "6. Respond with valid JSON ONLY, no markdown fences, matching this schema:\n"
        '{"summary": "...", "ordered_skills": ["..."], "ordered_bullets": [["..."]], "missing_keywords": ["..."]}\n\n'
        f"SOURCE CV:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
        f"JOB DESCRIPTION:\n{job_description.strip()}"
    )


def _call_gemini(api_key: str, prompt: str) -> str:
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai_compatible(provider: str, api_key: str, prompt: str) -> str:
    if provider == "mistral":
        url = "https://api.mistral.ai/v1/chat/completions"
        model = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
    else:
        url = "https://api.openai.com/v1/chat/completions"
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    body = json.dumps({
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _parse_json_response(raw_text: str) -> dict[str, Any]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL)
    return json.loads(cleaned)


def tailor_with_ai(
    cv_data: dict[str, Any],
    job_description: str,
    provider: str | None = None,
) -> TailorResult:
    """Tailor CV using a free-tier LLM API with strict anti-hallucination validation."""
    detected = _detect_provider(provider)
    if not detected:
        raise RuntimeError(
            "No AI API key found. Set GEMINI_API_KEY, MISTRAL_API_KEY, or OPENAI_API_KEY in .env."
        )

    provider_name, api_key = detected
    prompt = _build_prompt(cv_data, job_description)
    raw = (
        _call_gemini(api_key, prompt)
        if provider_name == "gemini"
        else _call_openai_compatible(provider_name, api_key, prompt)
    )
    ai_json = _parse_json_response(raw)

    # Start from local result so match_score & fallback safety are guaranteed.
    base = tailor_local(cv_data, job_description)
    tailored = copy.deepcopy(cv_data)

    # Validate and apply summary
    new_summary = str(ai_json.get("summary", "")).strip()
    if new_summary:
        tailored["summary"] = new_summary

    # Strictly validate ordered_skills against original skills (no invented skills)
    rating_map = {name: rating for name, rating in cv_data["skills"]}
    ordered_skills: list[list[Any]] = []
    seen_skills: set[str] = set()
    for skill in ai_json.get("ordered_skills", []):
        if skill in rating_map and skill not in seen_skills:
            ordered_skills.append([skill, rating_map[skill]])
            seen_skills.add(skill)
    for name, rating in cv_data["skills"]:
        if name not in seen_skills:
            ordered_skills.append([name, rating])
    tailored["skills"] = ordered_skills

    # Strictly validate ordered_bullets against original bullets per role
    ai_bullets = ai_json.get("ordered_bullets", [])
    for idx, exp in enumerate(tailored["work_experience"]):
        orig_bullets = exp["bullets"]
        if idx < len(ai_bullets) and isinstance(ai_bullets[idx], list):
            valid_set = set(orig_bullets)
            reordered = [b for b in ai_bullets[idx] if b in valid_set]
            for b in orig_bullets:
                if b not in reordered:
                    reordered.append(b)
            exp["bullets"] = reordered

    missing = ai_json.get("missing_keywords") or base.missing_keywords
    return TailorResult(
        tailored_data=tailored,
        mode_used=f"ai:{provider_name}",
        match_score=base.match_score,
        matched_skills=base.matched_skills,
        missing_keywords=[str(k) for k in missing[:8]],
        notes=f"Tailored with {provider_name} AI + strict profile validation.",
    )


def tailor_cv(
    cv_data: dict[str, Any],
    job_description: str,
    mode: str = "auto",
    provider: str | None = None,
) -> TailorResult:
    """Tailor CV using 'auto' (AI with local fallback), 'ai', or 'local' mode."""
    load_dotenv()
    mode = mode.lower()

    if mode == "local":
        return tailor_local(cv_data, job_description)
    if mode == "ai":
        return tailor_with_ai(cv_data, job_description, provider=provider)
    if mode != "auto":
        raise ValueError(f"Invalid mode '{mode}'. Choose 'auto', 'ai', or 'local'.")

    if _detect_provider(provider) is None:
        return tailor_local(cv_data, job_description)

    try:
        return tailor_with_ai(cv_data, job_description, provider=provider)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result = tailor_local(cv_data, job_description)
        result.notes = f"AI call failed ({exc}); used local keyword fallback."
        return result
