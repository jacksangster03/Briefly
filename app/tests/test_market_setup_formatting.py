from __future__ import annotations

from app.briefing.email_formatter import EmailFormatter
from app.briefing.move_context import MoveContext, format_move_context_line


def _ctx() -> MoveContext:
    return MoveContext(
        symbol="^GSPC",
        label="S&P 500",
        asset_type="equity_index",
        level_display="7,403.05",
        daily_move_display="-0.07%",
        move_direction="down",
        move_context_label="Normal day",
        level_context_label=None,
        move_percentile_1y=None,
        level_range_percentile_1y=None,
        day_range_display="-0.7% to +0.3%",
        day_range_position_label="upper half",
        final_display="S&P 500: 7,403.05 -0.07%",
        session_mode="morning",
    )


def test_market_setup_range_text_is_neutral_and_session_aware():
    line = format_move_context_line(_ctx())
    assert "| range -0.7% to +0.3%" in line
    assert "closed upper half" in line


def test_email_colorization_keeps_range_segment_neutral():
    fmt = EmailFormatter("Europe/Madrid")
    line = "S&P 500: 7,403.05 -0.07% | range -0.7% to +0.3% | closed upper half"
    out = fmt._colorize_structured_line(line, "market setup")
    assert "range -0.7% to +0.3%" in out
    # Move is colorized, range remains raw text.
    assert '<span style="color:' in out
    tail = out.split("| range ", 1)[1]
    assert "<span style=" not in tail
