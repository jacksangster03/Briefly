from __future__ import annotations

from datetime import date, datetime, timezone

from click.testing import CliRunner

from app.cli import cli
from app.db.models import NewsClassifierLabel
from app.db.session import get_session


def _seed_row():
    with get_session() as db:
        row = NewsClassifierLabel(
            event_id="evt-seed",
            headline="Test headline",
            summary="Test summary",
            source="newsapi",
            domain="example.com",
            deterministic_story_type="commentary_valuation",
            deterministic_suppression_reason="valuation_commentary_without_catalyst",
            deterministic_breaking_eligible=False,
            deterministic_freshness_state="new",
            deterministic_update_status="new",
            deterministic_score=0.75,
            included_in_briefing=False,
            sent_as_breaking=False,
            session_key="morning",
            local_date=date.today(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(row)
    with get_session() as db:
        return db.query(NewsClassifierLabel).filter(NewsClassifierLabel.event_id == "evt-seed").first().id


def test_news_review_and_label_set(validation_isolated_db):
    row_id = _seed_row()
    runner = CliRunner()

    result = runner.invoke(cli, ["news-review", "--date", "today", "--limit", "10"])
    assert result.exit_code == 0
    assert "NEWS REVIEW" in result.output
    assert "Test headline" in result.output

    result2 = runner.invoke(
        cli,
        [
            "news-label-set",
            "--id",
            str(row_id),
            "--story-type",
            "earnings_analysis",
            "--breaking-eligible",
            "false",
            "--ticker-mismatch-risk",
            "high",
            "--notes",
            "manual test",
        ],
    )
    assert result2.exit_code == 0

    with get_session() as db:
        updated = db.query(NewsClassifierLabel).filter(NewsClassifierLabel.id == row_id).first()
        assert updated.manual_story_type == "earnings_analysis"
        assert updated.manual_breaking_eligible is False
        assert updated.manual_ticker_mismatch_risk == "high"
        assert updated.label_source == "manual"
