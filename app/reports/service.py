"""Phase 7B: Portfolio PDF report generation service."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.models import GeneratedReport
from app.db.session import get_session
from app.logger import get_logger

logger = get_logger("reports")

_REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"

AVAILABLE_SECTIONS = [
    "cover",
    "holdings",
    "risk",
    "attribution",
    "bonds",
    "scenarios",
    "cma",
]

DEFAULT_SECTIONS = ["cover", "holdings", "risk", "attribution", "bonds"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_reports_dir(profile_name: str) -> Path:
    path = _REPORTS_DIR / profile_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_report(
    profile_name: str,
    state: dict[str, Any],
    sections: list[str] | None = None,
    title: str = "Portfolio Report",
) -> dict[str, Any]:
    """Generate a PDF report for the given profile state. Returns metadata dict."""
    from app.reports.renderer import render_pdf

    sections = [s for s in (sections or DEFAULT_SECTIONS) if s in AVAILABLE_SECTIONS]
    if not sections:
        sections = DEFAULT_SECTIONS

    report_dir = _ensure_reports_dir(profile_name)
    report_id = str(uuid.uuid4())[:8]
    filename = f"report_{report_id}.pdf"
    filepath = report_dir / filename

    try:
        render_pdf(
            filepath=filepath,
            profile_name=profile_name,
            title=title,
            sections=sections,
            state=state,
        )
    except Exception as exc:
        logger.error("PDF render failed: %s", exc, exc_info=True)
        return {"available": False, "error": f"PDF generation failed: {exc}"}

    file_size = filepath.stat().st_size if filepath.exists() else 0
    stored_path = str(filepath.resolve())
    generated_at = _utcnow()

    with get_session() as session:
        row = GeneratedReport(
            profile_name=profile_name,
            report_type="portfolio_summary",
            title=title,
            filename=stored_path,
            sections_included_json=json.dumps(sections),
            generated_at=generated_at,
            file_size_bytes=file_size,
            active=True,
            created_at=generated_at,
        )
        session.add(row)
        session.commit()
        report_db_id = row.id

    return {
        "available": True,
        "report_id": report_db_id,
        "filename": filename,
        "filepath": stored_path,
        "relative_path": stored_path,
        "title": title,
        "sections": sections,
        "generated_at": generated_at.isoformat(),
        "file_size_bytes": file_size,
    }


def list_reports(profile_name: str, limit: int = 10) -> list[dict[str, Any]]:
    """List recent generated reports for a profile."""
    with get_session() as session:
        rows = (
            session.query(GeneratedReport)
            .filter(
                GeneratedReport.profile_name == profile_name,
                GeneratedReport.active.is_(True),
            )
            .order_by(GeneratedReport.id.desc())
            .limit(limit)
            .all()
        )
    result = []
    for row in rows:
        sections = []
        try:
            sections = json.loads(row.sections_included_json or "[]")
        except (ValueError, TypeError):
            pass
        result.append({
            "report_id": row.id,
            "title": row.title,
            "filename": Path(row.filename).name if row.filename else "",
            "sections": sections,
            "generated_at": row.generated_at.strftime("%Y-%m-%d %H:%M") if row.generated_at else "",
            "file_size_bytes": row.file_size_bytes or 0,
            "exists": Path(row.filename).exists() if row.filename else False,
        })
    return result


def get_report_filepath(report_id: int) -> Path | None:
    """Resolve the absolute file path for a report by DB id."""
    with get_session() as session:
        row = session.query(GeneratedReport).filter(GeneratedReport.id == report_id).first()
    if not row or not row.filename:
        return None
    path = Path(row.filename)
    return path if path.exists() else None


def delete_report(report_id: int, profile_name: str) -> bool:
    """Soft-delete a report and remove its file."""
    with get_session() as session:
        row = (
            session.query(GeneratedReport)
            .filter(GeneratedReport.id == report_id, GeneratedReport.profile_name == profile_name)
            .first()
        )
        if not row:
            return False
        filepath = Path(row.filename) if row.filename else None
        if filepath and filepath.exists():
            try:
                filepath.unlink()
            except OSError:
                pass
        row.active = False
        session.commit()
    return True
