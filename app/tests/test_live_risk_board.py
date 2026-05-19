from __future__ import annotations

from app.briefing.session_diagnosis import build_trigger_board


def test_rates_breached_wording_is_active_not_conditional():
    board = build_trigger_board(
        ten_y=4.62,
        ten_y_chg=0.03,
        wti_pct=0.8,
        vix_level=18.0,
        regional_avg=-0.2,
        session_key="morning",
    )
    text = " | ".join(board["active"] + board["watch"] + board["cooled"])
    assert "Rates: ACTIVE" in text
    assert "already above 4.45%" in text
    assert "would re-activate rates pressure" not in text


def test_wti_elevated_wording_without_shock_threshold():
    board = build_trigger_board(
        ten_y=4.30,
        ten_y_chg=0.01,
        wti_pct=1.2,
        vix_level=18.4,
        regional_avg=0.1,
        session_key="morning",
    )
    text = " | ".join(board["active"] + board["watch"] + board["cooled"])
    assert "Oil: ELEVATED" in text
    assert "ACTIVE SHOCK" not in text


def test_vix_unavailable_is_unconfirmed():
    board = build_trigger_board(
        ten_y=4.30,
        ten_y_chg=0.01,
        wti_pct=0.2,
        vix_level=None,
        regional_avg=0.1,
        session_key="morning",
    )
    text = " | ".join(board["active"] + board["watch"] + board["cooled"])
    assert "Volatility: UNCONFIRMED" in text

