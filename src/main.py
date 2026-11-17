"""CLI entry point for tailor-cv."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from src.apply_assistant import run_apply_assistant, run_batch_apply
from src.job_fetcher import JOB_SOURCES, search_jobs
from src.pdf_generator import build_cv_data, draw_cover_letter, draw_cv, load_profile
from src.tailor import generate_cover_letter, tailor_cv
from src.tracker import (
    DEFAULT_DB_PATH,
    VALID_STATUSES,
    export_csv,
    list_applications,
    log_application,
    update_status,
)

DEFAULT_PROFILE = Path("data/profile.json")
DEFAULT_OUTPUT_DIR = Path("output")


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.strip())
    return cleaned.strip("_") or "item"


def _candidate_cv_filename(full_name: str) -> str:
    """Return a generic, recruiter-friendly CV filename like 'Ricardo_Pineda_CV.pdf'."""
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    short = "_".join(parts[:2]) if parts else "Candidate"
    return f"{_slugify(short.title())}_CV.pdf"


def _read_job_description(args: argparse.Namespace) -> str:
    if args.jd_text:
        return args.jd_text
    if args.jd_file:
        return Path(args.jd_file).read_text(encoding="utf-8")
    raise ValueError("Provide either --jd-file or --jd-text.")


def _cmd_generate(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    variant = args.variant or next(iter(profile["variants"]))
    cv_data = build_cv_data(profile, variant)
    out_file = args.out or (DEFAULT_OUTPUT_DIR / f"CV_{_slugify(cv_data['name'])}_{variant}.pdf")
    saved = draw_cv(out_file, cv_data)
    print(f"Generated base CV: {saved}")
    return 0


def _cmd_tailor(args: argparse.Namespace) -> int:
    jd_text = _read_job_description(args)
    profile = load_profile(args.profile)
    variant = args.variant or next(iter(profile["variants"]))
    cv_data = build_cv_data(profile, variant)

    result = tailor_cv(
        cv_data=cv_data,
        job_description=jd_text,
        mode=args.mode,
        provider=args.provider,
    )

    app_dir = DEFAULT_OUTPUT_DIR / "applications" / f"{_slugify(args.company)}_{_slugify(args.role)}_{variant}"
    out_file = args.out or (app_dir / _candidate_cv_filename(cv_data["name"]))
    saved = draw_cv(out_file, result.tailored_data)

    app_id = log_application(
        company=args.company,
        role=args.role,
        variant=variant,
        mode_used=result.mode_used,
        match_score=result.match_score,
        matched_skills=result.matched_skills,
        pdf_path=saved,
        job_url=args.url or "",
        db_path=args.db,
    )

    print(f"PDF generated   : {saved}")
    print(f"Tracker ID      : #{app_id}")
    print(f"Mode used       : {result.mode_used}")
    print(f"Match score     : {result.match_score}%")
    print(f"Matched skills  : {', '.join(result.matched_skills) or 'None'}")
    if result.missing_keywords:
        print(f"Missing keywords: {', '.join(result.missing_keywords)}")
    if result.notes:
        print(f"Notes           : {result.notes}")

    if args.cover_letter:
        cl_text, cl_mode = generate_cover_letter(
            result.tailored_data,
            jd_text,
            company=args.company,
            role=args.role,
            mode=args.mode,
            provider=args.provider,
        )
        cl_pdf = saved.with_name(f"{saved.stem}_CoverLetter.pdf")
        draw_cover_letter(cl_pdf, result.tailored_data, args.company, args.role, cl_text)
        print(f"Cover letter PDF: {cl_pdf} ({cl_mode})")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    rows = list_applications(args.db)
    if not rows:
        print("No applications tracked yet.")
        return 0

    for row in rows:
        print(
            f"#{row['id']} [{row['status']}] {row['company']} - {row['role']} "
            f"({row['variant']}, {row['match_score']}%, {row['mode_used']}) -> {row['pdf_path']}"
        )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    updated = update_status(args.id, args.status, db_path=args.db)
    if not updated:
        print(f"Application #{args.id} not found.", file=sys.stderr)
        return 1
    print(f"Updated application #{args.id} -> {args.status}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    out = export_csv(args.out, db_path=args.db)
    print(f"Exported tracker to: {out}")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    variant = args.variant or next(iter(profile["variants"]))
    cv_data = build_cv_data(profile, variant)

    sources = tuple(JOB_SOURCES.keys()) if args.source == "all" else (args.source,)
    jobs = search_jobs(cv_data, query=args.query, sources=sources, limit=args.limit)
    if not jobs:
        print("No matching jobs found.")
        return 0

    for idx, job in enumerate(jobs, start=1):
        skills = ", ".join(job.matched_skills) or "None"
        extra = " ".join(f"{k}={v}" for k, v in job.metadata.items())
        extra_suffix = f" ({extra})" if extra else ""
        print(f"[{idx}] {job.role} @ {job.company} ({job.location}) [{job.source}]{extra_suffix}")
        print(f"    Match: {job.match_score}% | Skills: {skills}")
        print(f"    URL  : {job.url}")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    variant = args.variant or next(iter(profile["variants"]))
    cv_data = build_cv_data(profile, variant)

    jd_text = ""
    if args.jd_text:
        jd_text = args.jd_text
    elif args.jd_file:
        jd_text = Path(args.jd_file).read_text(encoding="utf-8")

    app_dir = DEFAULT_OUTPUT_DIR / "applications" / f"{_slugify(args.company)}_{_slugify(args.role)}_{variant}"
    out_file = Path(args.out) if args.out else (app_dir / _candidate_cv_filename(cv_data["name"]))

    run_apply_assistant(
        job_url=args.url,
        company=args.company,
        role=args.role,
        cv_data=cv_data,
        variant=variant,
        pdf_out_path=out_file,
        job_description=jd_text,
        mode=args.mode,
        provider=args.provider,
        db_path=Path(args.db),
        dry_run=args.dry_run,
        auto_submit=args.auto_submit,
    )
    return 0


def _cmd_apply_batch(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    variant = args.variant or next(iter(profile["variants"]))
    cv_data = build_cv_data(profile, variant)

    sources = tuple(JOB_SOURCES.keys()) if args.source == "all" else (args.source,)
    jobs = search_jobs(cv_data, query=args.query, sources=sources, limit=args.count)
    run_batch_apply(
        jobs=jobs,
        cv_data=cv_data,
        variant=variant,
        output_dir=DEFAULT_OUTPUT_DIR / "applications",
        slugify_fn=_slugify,
        cv_filename=_candidate_cv_filename(cv_data["name"]),
        mode=args.mode,
        provider=args.provider,
        db_path=Path(args.db),
        dry_run=args.dry_run,
        auto_submit=args.auto_submit,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tailor-cv",
        description="Tailor your real CV to specific job descriptions and track applications.",
    )
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE), help="Path to profile JSON.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to SQLite tracker DB.")

    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="Generate a base CV PDF without tailoring.")
    p_gen.add_argument("--variant", default=None, help="Profile variant (e.g. eu, latam).")
    p_gen.add_argument("--out", default=None, help="Output PDF path.")
    p_gen.set_defaults(func=_cmd_generate)

    p_tailor = sub.add_parser("tailor", help="Tailor CV for a job description and log it.")
    p_tailor.add_argument("--company", required=True, help="Target company name.")
    p_tailor.add_argument("--role", required=True, help="Target role title.")
    p_tailor.add_argument("--jd-file", help="Path to a text file with the job description.")
    p_tailor.add_argument("--jd-text", help="Inline job description text.")
    p_tailor.add_argument("--variant", default=None, help="Profile variant (e.g. eu, latam).")
    p_tailor.add_argument(
        "--mode",
        choices=("auto", "ai", "local"),
        default="auto",
        help="Tailoring mode: auto (AI with local fallback), ai, or local.",
    )
    p_tailor.add_argument(
        "--provider",
        choices=("gemini", "mistral", "openai"),
        default=None,
        help="Preferred AI provider when using AI/auto mode.",
    )
    p_tailor.add_argument("--url", default="", help="Optional job posting URL.")
    p_tailor.add_argument("--out", default=None, help="Optional custom output PDF path.")
    p_tailor.add_argument(
        "--cover-letter",
        action="store_true",
        help="Also generate a tailored Cover Letter PDF.",
    )
    p_tailor.set_defaults(func=_cmd_tailor)

    p_list = sub.add_parser("list", help="List tracked applications.")
    p_list.set_defaults(func=_cmd_list)

    p_status = sub.add_parser("status", help="Update application status.")
    p_status.add_argument("--id", type=int, required=True, help="Application ID.")
    p_status.add_argument("--status", choices=VALID_STATUSES, required=True, help="New status.")
    p_status.set_defaults(func=_cmd_status)

    p_export = sub.add_parser("export", help="Export tracked applications to CSV.")
    p_export.add_argument("--out", default="output/applications.csv", help="Output CSV path.")
    p_export.set_defaults(func=_cmd_export)

    p_search = sub.add_parser("search", help="Search public job APIs and rank matches.")
    p_search.add_argument("--query", default="", help="Search keywords (e.g. 'QA Automation').")
    p_search.add_argument(
        "--source",
        choices=("all", *JOB_SOURCES.keys()),
        default="all",
        help="Job API source.",
    )
    p_search.add_argument("--variant", default=None, help="Profile variant (e.g. eu).")
    p_search.add_argument("--limit", type=int, default=10, help="Max results to display.")
    p_search.set_defaults(func=_cmd_search)

    p_apply = sub.add_parser("apply", help="Open job URL in Playwright, tailor CV, pre-fill & pause.")
    p_apply.add_argument("--url", required=True, help="Job application URL.")
    p_apply.add_argument("--company", required=True, help="Target company name.")
    p_apply.add_argument("--role", required=True, help="Target role title.")
    p_apply.add_argument("--variant", default=None, help="Profile variant (e.g. eu).")
    p_apply.add_argument("--jd-file", help="Optional job description text file.")
    p_apply.add_argument("--jd-text", help="Optional inline job description text.")
    p_apply.add_argument(
        "--mode",
        choices=("auto", "ai", "local"),
        default="auto",
        help="Tailoring mode.",
    )
    p_apply.add_argument(
        "--provider",
        choices=("gemini", "mistral", "openai"),
        default=None,
        help="Preferred AI provider.",
    )
    p_apply.add_argument("--out", default=None, help="Optional custom output PDF path.")
    p_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Tailor CV, pre-fill form, save screenshot, and exit without submitting.",
    )
    p_apply.add_argument(
        "--auto-submit",
        action="store_true",
        help="Attempt to submit and self-heal validation errors (pauses on CAPTCHA/unknowns).",
    )
    p_apply.set_defaults(func=_cmd_apply)

    p_batch = sub.add_parser(
        "apply-batch",
        help="Search top jobs and open multiple pre-filled browser tabs at once.",
    )
    p_batch.add_argument("--query", default="QA", help="Search keywords.")
    p_batch.add_argument(
        "--source",
        choices=("all", *JOB_SOURCES.keys()),
        default="eu_remote",
        help="Job API source.",
    )
    p_batch.add_argument("--variant", default=None, help="Profile variant (e.g. eu).")
    p_batch.add_argument("--count", type=int, default=3, help="Number of tabs to open.")
    p_batch.add_argument(
        "--mode",
        choices=("auto", "ai", "local"),
        default="auto",
        help="Tailoring mode.",
    )
    p_batch.add_argument(
        "--provider",
        choices=("gemini", "mistral", "openai"),
        default=None,
        help="Preferred AI provider.",
    )
    p_batch.add_argument("--dry-run", action="store_true", help="Pre-fill tabs and exit without pausing.")
    p_batch.set_defaults(func=_cmd_apply_batch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
