"""SQLite application tracker with CSV export."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("output/applications.db")
VALID_STATUSES = ("tailored", "applied", "interview", "rejected", "offer")


def _connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            variant TEXT NOT NULL,
            mode_used TEXT NOT NULL,
            match_score INTEGER NOT NULL,
            matched_skills TEXT NOT NULL,
            pdf_path TEXT NOT NULL,
            job_url TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'tailored'
        )
        """
    )
    conn.commit()
    return conn


def log_application(
    company: str,
    role: str,
    variant: str,
    mode_used: str,
    match_score: int,
    matched_skills: list[str],
    pdf_path: str | Path,
    job_url: str = "",
    status: str = "tailored",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Insert a tailored CV application record and return its row ID."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Valid: {VALID_STATUSES}")

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO applications (
                created_at, company, role, variant, mode_used,
                match_score, matched_skills, pdf_path, job_url, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                company,
                role,
                variant,
                mode_used,
                match_score,
                ", ".join(matched_skills),
                str(pdf_path),
                job_url,
                status,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_applications(db_path: str | Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Return all tracked applications ordered from newest to oldest."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM applications ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]


def update_status(
    app_id: int,
    status: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> bool:
    """Update the status of an application by ID. Returns True if updated."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Valid: {VALID_STATUSES}")

    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE applications SET status = ? WHERE id = ?",
            (status, app_id),
        )
        conn.commit()
        return cur.rowcount > 0


def export_csv(
    csv_path: str | Path = "output/applications.csv",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> Path:
    """Export all tracked applications to a CSV file."""
    rows = list_applications(db_path)
    out = Path(csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id",
        "created_at",
        "company",
        "role",
        "variant",
        "mode_used",
        "match_score",
        "matched_skills",
        "pdf_path",
        "job_url",
        "status",
    ]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out
