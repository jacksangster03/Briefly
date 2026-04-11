"""Schemas for rich delivery payloads and media assets."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChartAsset(BaseModel):
    """A rendered image asset attached to a briefing."""

    key: str
    title: str
    caption: str = ""
    filename: str = ""
    content_type: str = "image/png"
    content_id: str = ""
    content: bytes = b""


class EmailRenderResult(BaseModel):
    """Formatted email payload with optional inline media."""

    subject: str
    plain_text: str
    html_body: str
    inline_assets: list[ChartAsset] = Field(default_factory=list)
