from __future__ import annotations

from app.briefing.email_formatter import EmailFormatter
from app.briefing.move_colors import move_color_bucket, move_color_hex


def test_move_color_bucket_uses_magnitude_scale():
    assert move_color_bucket(-5.27) == "negative_strong"
    assert move_color_bucket(-1.13) == "negative_small"
    assert move_color_bucket(+5.0) == "positive_strong"
    assert move_color_bucket(None) == "neutral"


def test_move_color_hex_watchlist_severity_order():
    stronger = move_color_hex(-5.27)
    weaker = move_color_hex(-1.13)
    assert stronger != weaker
    assert stronger == "#C81E2B"
    assert weaker == "#FF8A93"


def test_watchlist_line_uses_deterministic_scale_in_email():
    formatter = EmailFormatter("Europe/Madrid")
    line = "AMD -5.27% | AVGO -1.13% | LLY +5.00% | MSFT +0.40% | CASH 0.00%"
    rendered = formatter._colorize_structured_line(line, "Watchlist")
    assert "#C81E2B" in rendered  # strongest red
    assert "#FF8A93" in rendered  # muted red
    assert "#00A88F" in rendered  # strongest green
    assert "#47CDB8" in rendered  # muted green

