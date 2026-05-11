from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.db.models import NewsClassifierLabel, NewsClassifierShadowRun
from app.db.session import get_session
from app.settings import Settings
from app.web.app import create_web_app


@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "news_ui.db"
    engine = create_app_engine(f"sqlite:///{db_path}")
    db_session._engine = engine
    db_session._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        db_session._engine = old_engine
        db_session._SessionLocal = old_factory
        engine.dispose()


@pytest.fixture
def test_settings(tmp_path, isolated_db):
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True)
    (config_dir / "user_profile.example.yaml").write_text(
        "\n".join(
            [
                "user:",
                "  name: default_user",
                "  timezone: Europe/Madrid",
                "  home_region: spain",
                "delivery:",
                "  morning_brief_time: '08:45'",
            ]
        )
    )
    (config_dir / "watchlists.example.yaml").write_text("primary: [AAPL]\n")
    (config_dir / "sectors.yaml").write_text("sectors: {}\nindices: []\nmacro_instruments: []\n")
    return Settings(configs_dir=str(config_dir), dry_run=True)


@pytest.fixture
def client(test_settings):
    app = create_web_app(test_settings)
    return TestClient(app)


def test_news_intelligence_route_renders_empty_state(client):
    response = client.get("/ui/news?profile=default_user")
    assert response.status_code == 200
    assert "News Intelligence" in response.text
    assert "No local review data found for this date." in response.text
    assert "news-review --date today --limit 50 --dedupe" in response.text


def test_news_intelligence_tabs_render(client):
    for tab in ("overview", "included", "suppressed", "breaking", "labels", "health", "sources"):
        response = client.get(f"/ui/news?profile=default_user&tab={tab}")
        assert response.status_code == 200
        assert "News Intelligence" in response.text


def test_news_intelligence_renders_rows_when_present(client):
    today = date.today()
    with get_session() as db:
        db.add(
            NewsClassifierLabel(
                event_id="evt-1",
                headline="Hard catalyst headline",
                source="newsapi",
                local_date=today,
                deterministic_story_type="earnings_results",
                deterministic_freshness_state="new",
                deterministic_update_status="new",
                deterministic_score=0.9,
                deterministic_breaking_eligible=True,
                included_in_briefing=True,
                session_key="morning",
                tickers_json=["AAPL"],
            )
        )
        db.add(
            NewsClassifierLabel(
                event_id="evt-2",
                headline="Low signal commentary",
                source="fmp_news",
                local_date=today,
                deterministic_story_type="commentary_valuation",
                deterministic_suppression_reason="low_signal",
                deterministic_freshness_state="repeated",
                deterministic_update_status="duplicate",
                deterministic_score=0.1,
                deterministic_breaking_eligible=False,
                included_in_briefing=False,
                session_key="morning",
                tickers_json=["MSFT"],
            )
        )
        db.add(
            NewsClassifierShadowRun(
                run_id="run-1",
                event_id="evt-2",
                session_key="morning",
                local_date=today,
                deterministic_story_type="commentary_valuation",
                ml_story_type="breaking_market_moving",
                deterministic_breaking_eligible=False,
                ml_breaking_eligible=True,
                ml_confidence=0.91,
                agreement=False,
                disagreement_reason="model_overcalls_breaking",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    response = client.get("/ui/news?profile=default_user&tab=included")
    assert response.status_code == 200
    assert "Hard catalyst headline" in response.text
    assert "AAPL" in response.text

    suppressed = client.get("/ui/news?profile=default_user&tab=suppressed")
    assert suppressed.status_code == 200
    assert "Low signal commentary" in suppressed.text
    assert "low_signal" in suppressed.text

    health = client.get("/ui/news?profile=default_user&tab=health")
    assert health.status_code == 200
    assert "authoritative" in health.text
    assert "Shadow disagreements" in health.text


def test_news_route_linked_from_command_centre_and_briefing(client):
    ui = client.get("/ui?profile=default_user")
    assert ui.status_code == 200
    assert "/ui/news?profile=default_user" in ui.text

    briefing = client.get("/ui/briefing?profile=default_user")
    assert briefing.status_code == 200
    assert "/ui/news?profile=default_user" in briefing.text
