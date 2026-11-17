"""PDF CV generator using ReportLab."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

PAGE_WIDTH, PAGE_HEIGHT = A4
SIDEBAR_WIDTH = 155


def load_profile(profile_path: str | Path) -> dict[str, Any]:
    """Load a JSON CV profile from disk."""
    with open(profile_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_cv_data(profile: dict[str, Any], variant: str | None = None) -> dict[str, Any]:
    """Merge base profile fields with the selected variant."""
    variants = profile.get("variants", {})
    if not variants:
        raise ValueError("Profile JSON must define at least one entry under 'variants'.")

    if variant is None:
        variant = next(iter(variants))
    elif variant not in variants:
        available = ", ".join(variants.keys())
        raise ValueError(f"Unknown variant '{variant}'. Available: {available}")

    selected = variants[variant]
    return {
        "name": profile["name"],
        "skills": copy.deepcopy(selected.get("skills", profile["skills"])),
        "languages": copy.deepcopy(selected.get("languages", profile["languages"])),
        "education": copy.deepcopy(selected.get("education", profile["education"])),
        "summary": selected["summary"],
        "personal_details": copy.deepcopy(selected["personal_details"]),
        "work_experience": copy.deepcopy(selected["work_experience"]),
        "application_answers": copy.deepcopy(
            selected.get("application_answers", profile.get("application_answers", {}))
        ),
    }


def _draw_paragraph(
    cnv: canvas.Canvas,
    text: str,
    style: ParagraphStyle,
    x: float,
    y: float,
    width: float,
) -> float:
    paragraph = Paragraph(text, style)
    _, height = paragraph.wrap(width, PAGE_HEIGHT)
    paragraph.drawOn(cnv, x, y - height)
    return y - height


def _draw_separator(cnv: canvas.Canvas, x1: float, x2: float, y: float) -> None:
    cnv.setStrokeColor(colors.HexColor("#c8ccd2"))
    cnv.setLineWidth(0.8)
    cnv.line(x1, y, x2, y)


def _draw_star(
    cnv: canvas.Canvas,
    cx: float,
    cy: float,
    outer_r: float,
    filled: bool,
) -> None:
    """Draw a 5-pointed vector star centered at (cx, cy)."""
    inner_r = outer_r * 0.42
    path = cnv.beginPath()
    for i in range(10):
        angle = math.radians(90 + i * 36)
        r = outer_r if i % 2 == 0 else inner_r
        px = cx + r * math.cos(angle)
        py = cy + r * math.sin(angle)
        if i == 0:
            path.moveTo(px, py)
        else:
            path.lineTo(px, py)
    path.close()

    if filled:
        cnv.setFillColor(colors.white)
        cnv.setStrokeColor(colors.white)
    else:
        cnv.setFillColor(colors.HexColor("#636b7d"))
        cnv.setStrokeColor(colors.HexColor("#636b7d"))
    cnv.setLineWidth(0.4)
    cnv.drawPath(path, stroke=1, fill=1)


def _draw_sidebar_rating(
    cnv: canvas.Canvas,
    x: float,
    y: float,
    label: str,
    rating: int,
    max_stars: int = 5,
) -> None:
    cnv.setFont("Helvetica-Bold", 8.3)
    cnv.setFillColor(colors.white)
    cnv.drawString(x, y, label)

    clamped = max(0, min(max_stars, int(rating)))
    outer_r = 3.2
    spacing = 8.4
    right_margin = SIDEBAR_WIDTH - 13
    first_cx = right_margin - (max_stars - 1) * spacing

    for idx in range(max_stars):
        _draw_star(cnv, first_cx + idx * spacing, y + 2.7, outer_r, filled=idx < clamped)


def draw_cv(filename: str | Path, data: dict[str, Any]) -> Path:
    """Render a CV dictionary to a PDF file and return its Path."""
    out_path = Path(filename)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cnv = canvas.Canvas(str(out_path), pagesize=A4)

    sidebar_color = colors.HexColor("#414857")
    title_color = colors.HexColor("#252a33")
    body_color = colors.HexColor("#3e434b")
    muted_color = colors.HexColor("#6f7682")

    # Base backgrounds
    cnv.setFillColor(colors.white)
    cnv.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)
    cnv.setFillColor(sidebar_color)
    cnv.rect(0, 0, SIDEBAR_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)

    # Sidebar
    left_x = 15
    y = PAGE_HEIGHT - 36

    cnv.setFillColor(colors.white)
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left_x, y, "PERSONAL DETAILS")
    y -= 18

    detail_labels = {
        "Name",
        "Address",
        "Phone",
        "Email",
        "LinkedIn",
        "Website",
        "Portfolio",
        "Work Status",
        "Work Authorization",
        "Contract Type",
    }
    for line in data["personal_details"]:
        lower_line = line.lower()
        is_url = any(
            token in lower_line for token in ("linkedin.com/", "github.io", "http://", "https://")
        )
        if line in detail_labels:
            cnv.setFont("Helvetica-Bold", 8.7)
        elif is_url:
            cnv.setFont("Helvetica", 7.4)
        else:
            cnv.setFont("Helvetica", 8.8)
        cnv.drawString(left_x, y, line)
        if is_url:
            target_url = line if lower_line.startswith(("http://", "https://")) else f"https://{line}"
            text_w = cnv.stringWidth(line, "Helvetica", 7.4)
            cnv.linkURL(target_url, (left_x, y - 2, left_x + text_w, y + 9), relative=0)
        y -= 13

    y -= 12
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left_x, y, "SKILLS")
    y -= 16

    for skill, rating in data["skills"]:
        _draw_sidebar_rating(cnv, left_x, y, skill, rating)
        y -= 13

    y -= 12
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left_x, y, "LANGUAGES")
    y -= 16

    for lang, rating in data["languages"]:
        _draw_sidebar_rating(cnv, left_x, y, lang, rating)
        y -= 13

    # Main content area
    main_x = SIDEBAR_WIDTH + 14
    main_width = PAGE_WIDTH - main_x - 20
    y = PAGE_HEIGHT - 38

    cnv.setFillColor(title_color)
    cnv.setFont("Helvetica-Bold", 26)
    cnv.drawString(main_x, y, data["name"])
    y -= 18

    summary_style = ParagraphStyle(
        "summary",
        fontName="Helvetica",
        fontSize=9.1,
        leading=12,
        textColor=body_color,
    )
    y = _draw_paragraph(cnv, data["summary"], summary_style, main_x, y, main_width)
    y -= 10
    _draw_separator(cnv, main_x, main_x + main_width, y)
    y -= 22

    # Education
    cnv.setFillColor(title_color)
    cnv.setFont("Helvetica-Bold", 15)
    cnv.drawString(main_x, y, "EDUCATION")
    y -= 16

    edu = data["education"]
    cnv.setFillColor(muted_color)
    cnv.setFont("Helvetica", 10)
    cnv.drawString(main_x, y, edu["dates"])

    edu_x = main_x + 136
    cnv.setFillColor(title_color)
    cnv.setFont("Helvetica-Bold", 12)
    cnv.drawString(edu_x, y, edu["title"])
    y -= 14

    cnv.setFont("Helvetica", 10)
    cnv.setFillColor(body_color)
    cnv.drawString(edu_x, y, edu["institution"])
    y -= 6

    edu_style = ParagraphStyle(
        "edu",
        fontName="Helvetica",
        fontSize=8.9,
        leading=11,
        textColor=body_color,
    )
    y = _draw_paragraph(cnv, edu["description"], edu_style, edu_x, y, main_width - 140)
    y -= 14
    _draw_separator(cnv, main_x, main_x + main_width, y)
    y -= 22

    # Work experience timeline
    cnv.setFillColor(title_color)
    cnv.setFont("Helvetica-Bold", 15)
    cnv.drawString(main_x, y, "WORK EXPERIENCE")
    y -= 12

    timeline_x = main_x + 10
    start_line_y = y - 4
    current_y = y - 16
    date_x = main_x + 22
    text_x = main_x + 155
    text_width = main_width - 165

    role_style = ParagraphStyle(
        "role",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=13,
        textColor=title_color,
    )
    company_style = ParagraphStyle(
        "company",
        fontName="Helvetica-Oblique",
        fontSize=10,
        leading=12,
        textColor=muted_color,
    )
    bullet_style = ParagraphStyle(
        "bullet",
        fontName="Helvetica",
        fontSize=8.8,
        leading=10.8,
        textColor=body_color,
    )

    dot_positions = []
    for item in data["work_experience"]:
        dot_positions.append(current_y + 4)

        cnv.setFillColor(muted_color)
        cnv.setFont("Helvetica", 10)
        cnv.drawString(date_x, current_y, item["dates"])

        current_y = _draw_paragraph(
            cnv, f"<b>{item['title']}</b>", role_style, text_x, current_y, text_width
        )
        current_y -= 1
        current_y = _draw_paragraph(
            cnv, item["company"], company_style, text_x, current_y, text_width
        )
        for bullet in item["bullets"]:
            current_y = _draw_paragraph(
                cnv, f"- {bullet}", bullet_style, text_x, current_y - 1, text_width
            )
        current_y -= 7

    end_line_y = current_y + 8
    cnv.setStrokeColor(colors.HexColor("#8e949f"))
    cnv.setLineWidth(1)
    cnv.line(timeline_x, start_line_y, timeline_x, end_line_y)

    cnv.setFillColor(colors.white)
    for dot_y in dot_positions:
        cnv.circle(timeline_x, dot_y, 3, stroke=1, fill=1)

    cnv.save()
    return out_path


def draw_cover_letter(
    filename: str | Path,
    cv_data: dict[str, Any],
    company: str,
    role: str,
    body_text: str,
) -> Path:
    """Render a clean PDF cover letter matching the CV's typography and palette."""
    out_path = Path(filename)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cnv = canvas.Canvas(str(out_path), pagesize=A4)
    title_color = colors.HexColor("#252a33")
    body_color = colors.HexColor("#3e434b")
    muted_color = colors.HexColor("#6f7682")

    margin_x = 56
    content_width = PAGE_WIDTH - (margin_x * 2)
    y = PAGE_HEIGHT - 64

    cnv.setFillColor(title_color)
    cnv.setFont("Helvetica-Bold", 20)
    cnv.drawString(margin_x, y, cv_data["name"])
    y -= 18

    cnv.setFillColor(muted_color)
    cnv.setFont("Helvetica", 10)
    cnv.drawString(margin_x, y, f"Application: {role} — {company}")
    y -= 14
    _draw_separator(cnv, margin_x, margin_x + content_width, y)
    y -= 28

    para_style = ParagraphStyle(
        "cover_body",
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        textColor=body_color,
    )
    for paragraph in body_text.strip().split("\n\n"):
        cleaned = paragraph.strip().replace("\n", "<br/>")
        if cleaned:
            y = _draw_paragraph(cnv, cleaned, para_style, margin_x, y, content_width)
            y -= 12

    cnv.save()
    return out_path
