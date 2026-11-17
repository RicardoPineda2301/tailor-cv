"""Human-in-the-loop Playwright assistant for pre-filling job applications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.pdf_generator import draw_cv
from src.tailor import tailor_cv
from src.tracker import DEFAULT_DB_PATH, log_application

DEFAULT_BROWSER_PROFILE = Path("output/.browser_profile")


def _extract_contact_fields(cv_data: dict[str, Any]) -> dict[str, str]:
    """Parse label/value pairs from personal_details list."""
    details = cv_data.get("personal_details", [])
    labels = {
        "Name",
        "Address",
        "Phone",
        "Email",
        "LinkedIn",
        "Website",
        "Portfolio",
        "Work Status",
    }
    parsed: dict[str, str] = {}
    for idx, token in enumerate(details):
        if token in labels and idx + 1 < len(details):
            parsed[token.lower()] = details[idx + 1]
    return parsed


def _try_fill_first(page: Any, selectors: list[str], value: str) -> bool:
    if not value:
        return False
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0 and locator.is_visible():
                current = locator.input_value()
                if not current.strip():
                    locator.fill(value)
                    return True
        except Exception:
            continue
    return False


def _try_attach_cv(page: Any, pdf_path: Path) -> bool:
    try:
        file_inputs = page.locator('input[type="file"]')
        count = file_inputs.count()
        for idx in range(count):
            try:
                file_inputs.nth(idx).set_input_files(str(pdf_path.resolve()))
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _prefill_common_fields(page: Any, contact: dict[str, str], pdf_path: Path) -> list[str]:
    """Best-effort fill for standard ATS fields (Greenhouse, Lever, Ashby, Workable, etc.)."""
    filled: list[str] = []
    full_name = contact.get("name", "")
    first_name = full_name.split()[0] if full_name else ""
    last_name = " ".join(full_name.split()[1:]) if len(full_name.split()) > 1 else ""

    if _try_fill_first(
        page,
        ['input[name="name"]', 'input[autocomplete="name"]', 'input[placeholder*="Full name" i]'],
        full_name,
    ):
        filled.append("name")
    else:
        if _try_fill_first(
            page,
            ['input[name*="first" i]', 'input[id*="first" i]', 'input[autocomplete="given-name"]'],
            first_name,
        ):
            filled.append("first_name")
        if _try_fill_first(
            page,
            ['input[name*="last" i]', 'input[id*="last" i]', 'input[autocomplete="family-name"]'],
            last_name,
        ):
            filled.append("last_name")

    if _try_fill_first(
        page,
        ['input[type="email"]', 'input[name*="email" i]', 'input[id*="email" i]'],
        contact.get("email", ""),
    ):
        filled.append("email")

    if _try_fill_first(
        page,
        ['input[type="tel"]', 'input[name*="phone" i]', 'input[id*="phone" i]'],
        contact.get("phone", ""),
    ):
        filled.append("phone")

    linkedin = contact.get("linkedin", "")
    if linkedin and not linkedin.startswith("http"):
        linkedin = f"https://{linkedin}"
    if _try_fill_first(
        page,
        ['input[name*="linkedin" i]', 'input[id*="linkedin" i]', 'input[placeholder*="linkedin" i]'],
        linkedin,
    ):
        filled.append("linkedin")

    portfolio = contact.get("portfolio", "") or contact.get("website", "")
    if portfolio and not portfolio.startswith("http"):
        portfolio = f"https://{portfolio}"
    if _try_fill_first(
        page,
        [
            'input[name*="website" i]',
            'input[name*="portfolio" i]',
            'input[id*="website" i]',
            'input[placeholder*="portfolio" i]',
        ],
        portfolio,
    ):
        filled.append("portfolio")

    if _try_attach_cv(page, pdf_path):
        filled.append("cv_pdf")

    return filled


def run_apply_assistant(
    job_url: str,
    company: str,
    role: str,
    cv_data: dict[str, Any],
    variant: str,
    pdf_out_path: Path,
    job_description: str = "",
    mode: str = "auto",
    provider: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Open job_url in a persistent Chromium browser, tailor CV, pre-fill fields, and pause."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: python -m pip install playwright && python -m playwright install chromium"
        ) from exc

    DEFAULT_BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    contact = _extract_contact_fields(cv_data)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(DEFAULT_BROWSER_PROFILE),
            headless=False,
        )
        page = context.pages[0] if context.pages else context.new_page()
        print(f"Opening: {job_url}")
        page.goto(job_url, wait_until="domcontentloaded", timeout=45000)

        jd_text = job_description.strip()
        if not jd_text:
            try:
                jd_text = page.inner_text("body")[:12000]
            except Exception:
                jd_text = f"{role} at {company}"

        result = tailor_cv(cv_data, jd_text, mode=mode, provider=provider)
        saved_pdf = draw_cv(pdf_out_path, result.tailored_data)
        print(f"Tailored CV ready: {saved_pdf} (match: {result.match_score}%, mode: {result.mode_used})")

        filled = _prefill_common_fields(page, contact, saved_pdf)
        if filled:
            print(f"Pre-filled fields: {', '.join(filled)}")
        else:
            print("No standard form inputs detected on initial view (open the Apply form if needed).")

        print("\n--- Human-in-the-loop review ---")
        print("1. Complete any login, CAPTCHA, or custom questions in the browser.")
        print("2. If you opened the Apply form on a second step, attach:", saved_pdf.resolve())
        answer = input("Did you submit the application? [y/N] (press Enter when done): ").strip().lower()
        status = "applied" if answer in ("y", "yes", "s", "si", "sí") else "tailored"

        app_id = log_application(
            company=company,
            role=role,
            variant=variant,
            mode_used=result.mode_used,
            match_score=result.match_score,
            matched_skills=result.matched_skills,
            pdf_path=saved_pdf,
            job_url=job_url,
            status=status,
            db_path=db_path,
        )
        context.close()

    print(f"Logged application #{app_id} with status '{status}'.")
    return app_id
