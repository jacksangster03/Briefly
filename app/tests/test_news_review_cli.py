from __future__ import annotations

from datetime import date, datetime, timezone

from click.testing import CliRunner

from app.cli import cli
from app.db.models import NewsClassifierLabel, NewsClassifierShadowRun
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


def _seed_duplicate_rows():
    now = datetime.now(timezone.utc)
    with get_session() as db:
        db.add(
            NewsClassifierLabel(
                event_id="evt-dup-a",
                headline="Duplicate headline story",
                summary="same",
                source="newsapi",
                domain="example.com",
                deterministic_story_type="macro_policy",
                deterministic_freshness_state="new",
                deterministic_update_status="new",
                deterministic_score=0.8,
                session_key="morning",
                local_date=date.today(),
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            NewsClassifierLabel(
                event_id="evt-dup-b",
                headline="Duplicate headline story",
                summary="same",
                source="newsapi",
                domain="example.com",
                deterministic_story_type="macro_policy",
                deterministic_freshness_state="new",
                deterministic_update_status="new",
                deterministic_score=0.81,
                session_key="us_pre_open",
                local_date=date.today(),
                created_at=now,
                updated_at=now,
            )
        )


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


def test_news_review_dedupe_collapses_duplicates(validation_isolated_db):
    _seed_duplicate_rows()
    runner = CliRunner()
    out_non = runner.invoke(cli, ["news-review", "--date", "today", "--limit", "20"])
    assert out_non.exit_code == 0
    assert out_non.output.count("Duplicate headline story") == 2

    out_dedupe = runner.invoke(cli, ["news-review", "--date", "today", "--limit", "20", "--dedupe"])
    assert out_dedupe.exit_code == 0
    assert out_dedupe.output.count("Duplicate headline story") == 1
    assert "sessions_seen=2" in out_dedupe.output


def test_news_label_quality_summary(validation_isolated_db):
    _seed_row()
    now = datetime.now(timezone.utc)
    with get_session() as db:
        db.add(
            NewsClassifierShadowRun(
                run_id="audit:run",
                event_id="evt-seed",
                session_key="morning",
                local_date=date.today(),
                model_name="stub",
                model_version="v1",
                deterministic_story_type="commentary_valuation",
                ml_story_type="earnings_analysis",
                deterministic_suppression_reason="valuation_commentary_without_catalyst",
                ml_suppression_reason="",
                deterministic_breaking_eligible=False,
                ml_breaking_eligible=True,
                ml_confidence=0.91,
                agreement=False,
                disagreement_reason="story_type,breaking_eligible",
                created_at=now,
            )
        )
    runner = CliRunner()
    out = runner.invoke(cli, ["news-label-quality", "--to", "today", "--limit-disagreements", "5"])
    assert out.exit_code == 0
    assert "LABEL QUALITY SUMMARY" in out.output
    assert "total_rows=1" in out.output
    assert "deduped_stories=1" in out.output
    assert "manual_label_coverage=" in out.output
    assert "class_distribution=" in out.output
    assert "stale_reprint_distribution=" in out.output
    assert "breaking_eligible_distribution=" in out.output
    assert "top_disagreement_candidates=1" in out.output
