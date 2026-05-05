"""Deterministic move-to-colour scaling for watchlist and mover surfaces."""

from __future__ import annotations

from typing import Literal

MoveBucket = Literal[
    "negative_strong",
    "negative_medium",
    "negative_small",
    "positive_small",
    "positive_medium",
    "positive_strong",
    "neutral",
]

_MOVE_BUCKET_TO_HEX: dict[MoveBucket, str] = {
    "negative_strong": "#C81E2B",
    "negative_medium": "#E63B4A",
    "negative_small": "#FF8A93",
    "positive_small": "#47CDB8",
    "positive_medium": "#1CC7AE",
    "positive_strong": "#00A88F",
    "neutral": "#8FA4BA",
}


def move_color_bucket(move_pct: float | None) -> MoveBucket:
    """Map daily % move to deterministic colour bucket."""
    if move_pct is None:
        return "neutral"
    move = float(move_pct)
    if move <= -5.0:
        return "negative_strong"
    if move <= -2.0:
        return "negative_medium"
    if move < 0.0:
        return "negative_small"
    if move >= 5.0:
        return "positive_strong"
    if move >= 2.0:
        return "positive_medium"
    if move > 0.0:
        return "positive_small"
    return "neutral"


def move_color_hex(move_pct: float | None) -> str:
    return _MOVE_BUCKET_TO_HEX[move_color_bucket(move_pct)]

