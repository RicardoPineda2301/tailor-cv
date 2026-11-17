"""Human-in-the-loop Playwright assistant for pre-filling job applications."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from src.job_fetcher import JobPosting
from src.pdf_generator import draw_cover_letter, draw_cv
from src.tailor import answer_form_questions, generate_cover_letter, tailor_cv
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
        [
            'input[name="name"]',
            'input[name="_systemfield_name"]',
            'input[autocomplete="name"]',
            'input[aria-label="Name" i]',
            'input[placeholder*="Full name" i]',
        ],
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

def _prefill_from_answers(
    page: Any,
    cv_data: dict[str, Any],
    contact: dict[str, str],
) -> list[str]:
    """Scan visible text/url inputs and textareas and fill matching profile/answers."""
    answers = cv_data.get("application_answers") or {}
    rules: list[tuple[str, tuple[str, ...], str]] = [
        ("github", ("github",), str(answers.get("github", ""))),
        ("city", ("city", "ciudad", "town"), str(answers.get("city", ""))),
        ("postal_code", ("postcode", "postal", "zip", "código postal", "codigo postal"), str(answers.get("postal_code", ""))),
        ("country", ("country", "país", "pais"), str(answers.get("country", ""))),
        ("location", ("location", "ubicación", "ubicacion", "reside"), str(answers.get("location", ""))),
        ("address", ("address", "street", "dirección", "direccion"), contact.get("address", "")),
        (
            "work_authorization",
            ("authorized", "authorisation", "authorization", "work permit", "visa", "eligible to work", "right to work"),
            str(answers.get("work_authorization", "")),
        ),
        ("notice_period", ("notice", "availability", "start date", "incorporación"), str(answers.get("notice_period", ""))),
        ("english_level", ("english", "inglés", "ingles"), str(answers.get("english_level", ""))),
        ("b2b_preference", ("b2b", "freelance", "contractor", "autónomo", "autonomo"), str(answers.get("b2b_preference", ""))),
        ("salary", ("salary", "compensation", "rate", "salario", "sueldo"), str(answers.get("salary_expectation", ""))),
    ]

    filled: list[str] = []
    try:
        fields = page.locator(
            'input[type="text"], input[type="url"], input:not([type]), textarea'
        )
        total = min(fields.count(), 40)
    except Exception:
        return filled

    for idx in range(total):
        loc = fields.nth(idx)
        try:
            if not loc.is_visible():
                continue
            if loc.input_value().strip():
                continue

            meta_text = loc.evaluate(
                """el => {
                    const id = el.id || '';
                    const lbl = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                    const parentLbl = el.closest('label');
                    return [
                        el.name || '',
                        el.id || '',
                        el.placeholder || '',
                        el.getAttribute('aria-label') || '',
                        lbl ? lbl.innerText : '',
                        parentLbl ? parentLbl.innerText : ''
                    ].join(' ').toLowerCase();
                }"""
            )
            for key, keywords, value in rules:
                if value and any(kw in meta_text for kw in keywords):
                    loc.fill(value)
                    filled.append(key)
                    break
        except Exception:
            continue
    return filled


def _collect_unanswered_questions(page: Any) -> list[dict[str, Any]]:
    """Collect unfilled text/textarea, unchecked radio groups, and unselected selects."""
    try:
        return page.evaluate(
            """() => {
                const questions = [];
                let counter = 0;

                const getLabel = (el) => {
                    const id = el.id || '';
                    const forLbl = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                    const parentLbl = el.closest('label');
                    const fieldWrap = el.closest('div, fieldset, li, section');
                    const heading = fieldWrap ? fieldWrap.querySelector('label, legend, p, span') : null;
                    return (
                        (forLbl && forLbl.innerText) ||
                        (parentLbl && parentLbl.innerText) ||
                        el.getAttribute('aria-label') ||
                        el.placeholder ||
                        (heading && heading.innerText) ||
                        el.name ||
                        el.id ||
                        ''
                    ).trim().replace(/\\s+/g, ' ').slice(0, 350);
                };

                const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);

                // 1. Text inputs & textareas
                const textEls = document.querySelectorAll(
                    'input[type="text"], input[type="url"], input[type="number"], input:not([type]), textarea'
                );
                for (const el of textEls) {
                    if (!isVisible(el) || el.disabled || el.readOnly) continue;
                    if ((el.value || '').trim()) continue;
                    const roleAttr = (el.getAttribute('role') || '').toLowerCase();
                    if (roleAttr === 'searchbox' || /search/i.test(el.placeholder || '')) continue;
                    const qid = `q_${counter++}`;
                    el.setAttribute('data-tailor-qid', qid);
                    const lbl = getLabel(el);
                    if (!lbl) continue;
                    questions.push({
                        id: qid,
                        type: el.tagName.toLowerCase() === 'textarea' ? 'textarea' : 'text',
                        label: lbl,
                        options: []
                    });
                }

                // 2. Radio groups (where none is checked)
                const radioGroups = new Map();
                for (const el of document.querySelectorAll('input[type="radio"]')) {
                    if (!el.name || el.disabled) continue;
                    if (!radioGroups.has(el.name)) radioGroups.set(el.name, []);
                    radioGroups.get(el.name).push(el);
                }
                for (const [name, radios] of radioGroups.entries()) {
                    if (radios.some(r => r.checked)) continue;
                    const qid = `q_${counter++}`;
                    const groupWrap = radios[0].closest('fieldset, [role="radiogroup"], div');
                    const legend = groupWrap ? groupWrap.querySelector('legend, label, p, span') : null;
                    const groupLabel = (legend ? legend.innerText : name).trim().replace(/\\s+/g, ' ').slice(0, 350);
                    const options = [];
                    radios.forEach((r, idx) => {
                        const optId = `${qid}_opt_${idx}`;
                        r.setAttribute('data-tailor-radio', optId);
                        const rLbl = r.id ? document.querySelector(`label[for="${CSS.escape(r.id)}"]`) : null;
                        const pLbl = r.closest('label');
                        const txt = ((rLbl && rLbl.innerText) || (pLbl && pLbl.innerText) || r.value || '').trim();
                        if (txt) options.push(txt);
                    });
                    if (options.length > 0) {
                        questions.push({ id: qid, type: 'radio', label: groupLabel, options, radioName: name });
                    }
                }

                // 3. Select dropdowns (where value is empty)
                for (const sel of document.querySelectorAll('select')) {
                    if (!isVisible(sel) || sel.disabled || sel.value) continue;
                    const qid = `q_${counter++}`;
                    sel.setAttribute('data-tailor-qid', qid);
                    const opts = Array.from(sel.options)
                        .filter(o => o.value && !/select|choose|--/i.test(o.text))
                        .map(o => o.text.trim())
                        .slice(0, 25);
                    if (opts.length > 0) {
                        questions.push({ id: qid, type: 'select', label: getLabel(sel), options: opts });
                    }
                }

                return questions;
            }"""
        )
    except Exception:
        return []


def _fill_ai_form_questions(
    page: Any,
    cv_data: dict[str, Any],
    jd_text: str,
    company: str,
    role: str,
    mode: str,
    provider: str | None,
) -> list[str]:
    """Use AI (with local fallback) to answer remaining form questions."""
    questions = _collect_unanswered_questions(page)
    if not questions:
        return []

    answers = answer_form_questions(
        cv_data=cv_data,
        job_description=jd_text,
        company=company,
        role=role,
        questions=questions,
        mode=mode,
        provider=provider,
    )
    if not answers:
        return []

    filled_labels: list[str] = []
    q_by_id = {q["id"]: q for q in questions}

    for qid, ans in answers.items():
        q = q_by_id.get(qid)
        if not q or not ans:
            continue
        qtype = q["type"]
        try:
            if qtype in ("text", "textarea"):
                loc = page.locator(f'[data-tailor-qid="{qid}"]').first
                if loc.count() > 0 and not loc.input_value().strip():
                    loc.fill(ans)
                    filled_labels.append(q["label"][:32])
            elif qtype == "select":
                loc = page.locator(f'select[data-tailor-qid="{qid}"]').first
                if loc.count() > 0:
                    loc.select_option(label=ans)
                    filled_labels.append(q["label"][:32])
            elif qtype == "radio":
                opts = q.get("options") or []
                match_idx = next(
                    (i for i, opt in enumerate(opts) if opt.lower() == ans.lower()),
                    None,
                )
                if match_idx is not None:
                    radio = page.locator(f'input[data-tailor-radio="{qid}_opt_{match_idx}"]').first
                    if radio.count() > 0:
                        try:
                            radio.check(force=True, timeout=2000)
                        except Exception:
                            radio.evaluate("el => el.click()")
                        filled_labels.append(q["label"][:32])
        except Exception:
            continue

    return filled_labels


def _detect_cover_letter_target(page: Any, jd_text: str) -> tuple[bool, Any, Any]:
    """Check if the page or JD asks for a cover letter.

    Returns (should_generate, textarea_locator_or_None, file_input_locator_or_None).
    """
    textarea_selectors = [
        'textarea[name*="cover" i]',
        'textarea[id*="cover" i]',
        'textarea[placeholder*="cover" i]',
        'textarea[aria-label*="cover" i]',
    ]
    for sel in textarea_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True, loc, None
        except Exception:
            continue

    file_selectors = [
        'input[type="file"][name*="cover" i]',
        'input[type="file"][id*="cover" i]',
        'input[type="file"][aria-label*="cover" i]',
    ]
    for sel in file_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return True, None, loc
        except Exception:
            continue

    jd_lower = jd_text.lower()
    mentions_cl = any(
        phrase in jd_lower
        for phrase in ("cover letter", "carta de presentación", "carta de presentacion", "motivation letter")
    )
    return mentions_cl, None, None


def _open_apply_form_if_needed(page: Any) -> bool:
    """Click an 'Apply' button/link if no CV upload input is visible yet."""
    if page.locator('input[type="file"]').count() > 0:
        return False

    apply_selectors = [
        'a:has-text("Apply Now")',
        'button:has-text("Apply Now")',
        'a:has-text("Apply for this job")',
        'button:has-text("Apply for this job")',
        'a:has-text("Apply")',
        'button:has-text("Apply")',
        'a[href*="#app"]',
        'a[href*="apply"]',
    ]
    for sel in apply_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click()
                page.wait_for_timeout(1800)
                return True
        except Exception:
            continue
    return False


def _prepare_job_tab(
    page: Any,
    job_url: str,
    company: str,
    role: str,
    cv_data: dict[str, Any],
    pdf_out_path: Path,
    job_description: str = "",
    mode: str = "auto",
    provider: str | None = None,
) -> tuple[Any, Path, list[str]]:
    """Navigate a browser tab to job_url, tailor CV, open form, and pre-fill fields."""
    contact = _extract_contact_fields(cv_data)
    print(f"Opening: {job_url}", flush=True)
    page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1000)

    jd_text = job_description.strip()
    if not jd_text:
        try:
            jd_text = page.inner_text("body")[:12000]
        except Exception:
            jd_text = f"{role} at {company}"

    if _open_apply_form_if_needed(page):
        print("  Clicked 'Apply' to open application form.", flush=True)

    result = tailor_cv(cv_data, jd_text, mode=mode, provider=provider)
    saved_pdf = draw_cv(pdf_out_path, result.tailored_data)
    print(f"  Tailored CV: {saved_pdf} (match: {result.match_score}%, mode: {result.mode_used})", flush=True)

    filled = _prefill_common_fields(page, contact, saved_pdf)
    filled.extend(_prefill_from_answers(page, cv_data, contact))
    ai_filled = _fill_ai_form_questions(
        page=page,
        cv_data=cv_data,
        jd_text=jd_text,
        company=company,
        role=role,
        mode=mode,
        provider=provider,
    )
    filled.extend(ai_filled)
    if filled:
        print(f"  Pre-filled : {', '.join(filled)}")
    else:
        print("  Pre-filled : none detected automatically.")

    needs_cl, cl_textarea, cl_file = _detect_cover_letter_target(page, jd_text)
    if needs_cl:
        cl_text, cl_mode = generate_cover_letter(
            result.tailored_data, jd_text, company=company, role=role, mode=mode, provider=provider
        )
        cl_txt_path = saved_pdf.with_name(f"{saved_pdf.stem}_CoverLetter.txt")
        cl_txt_path.write_text(cl_text, encoding="utf-8")
        print(f"  Cover letter ({cl_mode}): {cl_txt_path}")

        if cl_textarea is not None:
            try:
                if not cl_textarea.input_value().strip():
                    cl_textarea.fill(cl_text)
                    filled.append("cover_letter_text")
            except Exception:
                pass
        elif cl_file is not None:
            cl_pdf_path = saved_pdf.with_name(f"{saved_pdf.stem}_CoverLetter.pdf")
            draw_cover_letter(cl_pdf_path, result.tailored_data, company, role, cl_text)
            try:
                cl_file.set_input_files(str(cl_pdf_path.resolve()))
                filled.append("cover_letter_pdf")
            except Exception:
                pass

    return result, saved_pdf, filled


def _has_captcha(page: Any) -> bool:
    """Detect common CAPTCHA widgets so we never attempt to bypass them."""
    captcha_selectors = [
        'iframe[src*="recaptcha"]',
        'iframe[src*="hcaptcha"]',
        'iframe[src*="turnstile"]',
        '.g-recaptcha',
        '.h-captcha',
        '.cf-turnstile',
    ]
    for sel in captcha_selectors:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            continue
    return False


def _collect_validation_errors(page: Any) -> list[str]:
    """Read visible validation error messages from the form."""
    try:
        errors = page.evaluate(
            """() => {
                const msgs = new Set();
                const selectors = [
                    '[aria-invalid="true"]',
                    '.error',
                    '.field-error',
                    '.invalid-feedback',
                    '[role="alert"]',
                    'p.text-red-500',
                    'span.text-red-500'
                ];
                for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                        const txt = (el.innerText || '').trim();
                        if (txt && txt.length < 220) msgs.add(txt);
                    }
                }
                for (const el of document.querySelectorAll('input:invalid, textarea:invalid, select:invalid')) {
                    if (el.validationMessage) {
                        msgs.add(`${el.name || el.id || 'field'}: ${el.validationMessage}`);
                    }
                }
                return Array.from(msgs);
            }"""
        )
        return [str(e) for e in errors if e]
    except Exception:
        return []


def _is_submission_confirmed(page: Any, initial_url: str) -> bool:
    """Check if the page indicates a successful application submission."""
    try:
        body_low = page.inner_text("body").lower()
    except Exception:
        body_low = ""
    markers = (
        "thank you for applying",
        "application has been submitted",
        "application received",
        "we have received your application",
        "thanks for applying",
        "gracias por tu candidatura",
        "solicitud enviada",
    )
    if any(m in body_low for m in markers):
        return True
    current_url = page.url.lower()
    if current_url != initial_url.lower() and any(
        k in current_url for k in ("thank", "confirm", "success", "submitted")
    ):
        return True
    return False


def _heal_missing_fields(
    page: Any,
    cv_data: dict[str, Any],
    contact: dict[str, str],
    errors: list[str],
) -> int:
    """Attempt deterministic fixes for invalid/missing required fields."""
    fixes = 0

    # 1. Re-run profile/answer prefill in case dynamic fields appeared
    fixes += len(_prefill_from_answers(page, cv_data, contact))

    # 2. Check required or consent/privacy checkboxes
    try:
        checkboxes = page.locator('input[type="checkbox"]')
        for idx in range(min(checkboxes.count(), 10)):
            cb = checkboxes.nth(idx)
            if cb.is_checked():
                continue
            is_req = cb.evaluate(
                """el => el.required || el.getAttribute('aria-required') === 'true' ||
                /process_information|consent|privacy|gdpr|agree|terms/i.test((el.name || '') + ' ' + (el.id || ''))"""
            )
            if not is_req:
                continue
            try:
                cb.scroll_into_view_if_needed()
                cb.check(force=True, timeout=3000)
                if not cb.is_checked():
                    cb.evaluate(
                        "el => { el.checked = true; el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); }"
                    )
                if cb.is_checked():
                    print(f"  [Self-Heal] Checked: {cb.get_attribute('name') or cb.get_attribute('id')}")
                    fixes += 1
            except Exception as exc:
                print(f"  [Self-Heal] Checkbox error: {exc}")
                continue
    except Exception:
        pass

    # 3. If phone validation failed, try stripping spaces from phone number
    if any("phone" in e.lower() or "tel" in e.lower() for e in errors):
        compact_phone = contact.get("phone", "").replace(" ", "")
        if compact_phone:
            try:
                tel = page.locator('input[type="tel"]').first
                if tel.count() > 0 and tel.is_visible():
                    tel.fill(compact_phone)
                    fixes += 1
            except Exception:
                pass

    return fixes


def _input_with_timeout(prompt: str, timeout_seconds: int = 120) -> str:
    """Read stdin with a timeout so unattended runs never hang forever."""
    result: list[str] = []

    def _reader() -> None:
        try:
            result.append(input(prompt))
        except EOFError:
            result.append("")

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        print(f"\n[Timeout] No response after {timeout_seconds}s; closing cleanly.")
        return ""
    return result[0] if result else ""


def _submit_with_self_heal(
    page: Any,
    cv_data: dict[str, Any],
    contact: dict[str, str],
    max_retries: int = 2,
    timeout_seconds: int = 120,
) -> tuple[bool, list[str]]:
    """Click Submit and self-heal form validation errors up to max_retries or timeout_seconds."""
    initial_url = page.url
    deadline = time.monotonic() + timeout_seconds
    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Submit Application")',
        'button:has-text("Submit")',
        'button:has-text("Send Application")',
    ]

    for attempt in range(1, max_retries + 2):
        if time.monotonic() >= deadline:
            return False, [f"Timed out after {timeout_seconds}s."]
        if _has_captcha(page):
            return False, ["CAPTCHA detected — manual completion required."]

        clicked = False
        for sel in submit_selectors:
            try:
                btn = page.locator(sel).last
                if btn.count() > 0 and btn.is_visible():
                    btn.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            return False, ["No visible Submit button found."]

        page.wait_for_timeout(3000)

        if _is_submission_confirmed(page, initial_url):
            return True, []

        errors = _collect_validation_errors(page)
        print(f"  [Self-Heal attempt {attempt}] Errors detected: {errors or ['Submission not confirmed']}")

        if attempt > max_retries:
            return False, errors or ["Submission could not be confirmed after retries."]

        healed = _heal_missing_fields(page, cv_data, contact, errors)
        print(f"  [Self-Heal attempt {attempt}] Applied {healed} automatic fix(es), retrying...")
        if healed == 0 and not errors:
            return False, ["No clear validation error to heal automatically."]

    return False, ["Max self-heal retries reached."]


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
    dry_run: bool = False,
    auto_submit: bool = False,
) -> int:
    """Open job_url in a persistent Chromium browser, tailor CV, pre-fill & pause."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: python -m pip install playwright && python -m playwright install chromium"
        ) from exc

    DEFAULT_BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    print("Launching Chromium (close any previous tailor-cv Chromium window if it hangs here)...", flush=True)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(DEFAULT_BROWSER_PROFILE),
            headless=False,
        )
        page = context.pages[0] if context.pages else context.new_page()
        result, saved_pdf, _ = _prepare_job_tab(
            page=page,
            job_url=job_url,
            company=company,
            role=role,
            cv_data=cv_data,
            pdf_out_path=pdf_out_path,
            job_description=job_description,
            mode=mode,
            provider=provider,
        )

        if dry_run:
            preview_path = pdf_out_path.parent / "dry_run_preview.png"
            page.screenshot(path=str(preview_path), full_page=True)
            print(f"[DRY-RUN] Screenshot saved: {preview_path}")
            print("[DRY-RUN] Form was NOT submitted.")
            status = "tailored"
        elif auto_submit:
            contact = _extract_contact_fields(cv_data)
            ok, errs = _submit_with_self_heal(page, cv_data, contact)
            if ok:
                print("[AUTO-SUBMIT] Application confirmed!")
                status = "applied"
            else:
                print(f"[AUTO-SUBMIT] Stopped for manual review: {errs}")
                answer = _input_with_timeout(
                    "Did you finish submitting manually? [y/N] (auto-closes in 120s): ",
                    timeout_seconds=120,
                ).strip().lower()
                status = "applied" if answer in ("y", "yes", "s", "si", "sí") else "tailored"
        else:
            print("\n--- Human-in-the-loop review ---")
            print("Review the pre-filled tab, answer any custom questions, and click Submit.")
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


def run_batch_apply(
    jobs: list[JobPosting],
    cv_data: dict[str, Any],
    variant: str,
    output_dir: Path,
    slugify_fn: Any,
    cv_filename: str = "CV.pdf",
    mode: str = "auto",
    provider: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    dry_run: bool = False,
) -> list[int]:
    """Open multiple job postings in parallel Chromium tabs, pre-fill each, and pause once."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: python -m pip install playwright && python -m playwright install chromium"
        ) from exc

    if not jobs:
        print("No jobs to open.")
        return []

    DEFAULT_BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    prepared: list[tuple[int, JobPosting, Any, Path]] = []

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(DEFAULT_BROWSER_PROFILE),
            headless=False,
        )
        for idx, job in enumerate(jobs, start=1):
            page = context.pages[0] if (idx == 1 and context.pages) else context.new_page()
            job_dir = output_dir / f"{slugify_fn(job.company)}_{slugify_fn(job.role)}_{variant}"
            print(f"\n[{idx}/{len(jobs)}] Preparing {job.role} @ {job.company}")
            try:
                result, saved_pdf, _ = _prepare_job_tab(
                    page=page,
                    job_url=job.url,
                    company=job.company,
                    role=job.role,
                    cv_data=cv_data,
                    pdf_out_path=job_dir / cv_filename,
                    job_description=job.description,
                    mode=mode,
                    provider=provider,
                )
                prepared.append((idx, job, result, saved_pdf))
            except Exception as exc:
                print(f"  Skipped due to error: {exc}")

        if dry_run:
            print(f"\n[DRY-RUN] Prepared {len(prepared)} tabs without submitting.")
            submitted_indices: set[int] = set()
        else:
            print("\n=== All tabs ready for review ===")
            for idx, job, _, _ in prepared:
                print(f"  Tab [{idx}]: {job.role} @ {job.company}")
            raw = input(
                "\nReview each tab and click Submit. Which tab numbers did you submit? (e.g. 1,2 or 'all', Enter for none): "
            ).strip().lower()
            if raw == "all":
                submitted_indices = {idx for idx, _, _, _ in prepared}
            else:
                submitted_indices = {
                    int(tok) for tok in raw.replace(",", " ").split() if tok.isdigit()
                }

        logged_ids: list[int] = []
        for idx, job, result, saved_pdf in prepared:
            status = "applied" if idx in submitted_indices else "tailored"
            app_id = log_application(
                company=job.company,
                role=job.role,
                variant=variant,
                mode_used=result.mode_used,
                match_score=result.match_score,
                matched_skills=result.matched_skills,
                pdf_path=saved_pdf,
                job_url=job.url,
                status=status,
                db_path=db_path,
            )
            logged_ids.append(app_id)
            print(f"Logged #{app_id} [{status}] {job.role} @ {job.company}")

        context.close()
    return logged_ids
