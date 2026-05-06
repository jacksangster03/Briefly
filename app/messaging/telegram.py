"""Telegram delivery via the Bot API (raw HTTP, no heavy async library)."""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any

import requests

from app.logger import get_logger
from app.messaging.base import BaseMessenger
from app.schemas.delivery import ChartAsset
from app.settings import Settings

logger = get_logger("telegram")

API_BASE = "https://api.telegram.org"

# Inline keyboard buttons attached to each chart message for feedback
_FEEDBACK_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "✓ Useful",       "callback_data": "fb:useful"},
        {"text": "✗ Not relevant", "callback_data": "fb:not_relevant"},
        {"text": "⚠ Wrong data",   "callback_data": "fb:wrong_data"},
    ]]
}


class TelegramMessenger(BaseMessenger):
    name = "telegram"

    def __init__(self, settings: Settings):
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.dry_run = settings.dry_run
        # When the top-level pipeline prints messages via --show-output,
        # this messenger skips its own dry-run echo so the terminal
        # doesn't show the same payload twice.
        self.show_output = settings.show_output
        self.last_error: str = ""

    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        self.last_error = ""
        if self.dry_run:
            logger.info("[DRY RUN] Would send Telegram message (%d chars)", len(text))
            if not self.show_output:
                print(f"\n{'='*60}")
                print("[DRY RUN] Telegram message:")
                print(f"{'='*60}")
                # Strip HTML tags for console preview
                import re
                preview = re.sub(r"<[^>]+>", "", text)
                print(preview)
                print(f"{'='*60}\n")
            return True

        if not self.is_configured():
            logger.warning("Telegram not configured; message not sent")
            self.last_error = "not configured"
            return False

        url = f"{API_BASE}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.ok:
                logger.info("Telegram message sent (%d chars)", len(text))
                return True
            else:
                error = resp.json().get("description", resp.text[:200])
                logger.error("Telegram send failed: %s", error)
                self.last_error = error
                return False
        except requests.exceptions.RequestException as exc:
            logger.error("Telegram request failed: %s", exc)
            self.last_error = str(exc)
            return False

    def send_photo(self, asset: ChartAsset, caption: str = "", with_feedback_keyboard: bool = False) -> bool:
        """Send a single chart image to Telegram."""
        if self.dry_run:
            logger.info("[DRY RUN] Would send Telegram photo '%s' (%d bytes)", asset.title, len(asset.content))
            return True

        if not self.is_configured():
            logger.warning("Telegram not configured; photo not sent")
            return False

        url = f"{API_BASE}/bot{self.token}/sendPhoto"
        files = {
            "photo": (
                asset.filename or "chart.png",
                BytesIO(asset.content),
                asset.content_type,
            )
        }
        data: dict[str, Any] = {
            "chat_id": self.chat_id,
            "caption": caption or asset.title,
        }
        if with_feedback_keyboard:
            keyboard = dict(_FEEDBACK_KEYBOARD)
            # Encode chart key into callback data so feedback handler knows the source
            keyboard["inline_keyboard"] = [[
                {"text": btn["text"], "callback_data": f"{btn['callback_data']}:{asset.key}"}
                for btn in row
            ] for row in _FEEDBACK_KEYBOARD["inline_keyboard"]]
            data["reply_markup"] = json.dumps(keyboard)

        try:
            resp = requests.post(url, data=data, files=files, timeout=30)
            if resp.ok:
                logger.info("Telegram photo sent: %s", asset.title)
                return True
            error = resp.json().get("description", resp.text[:200])
            logger.error("Telegram photo send failed: %s", error)
            return False
        except requests.exceptions.RequestException as exc:
            logger.error("Telegram photo request failed: %s", exc)
            return False

    def send_chart_album(self, assets: list[ChartAsset], caption_prefix: str = "") -> bool:
        """Send up to 10 charts as a single Telegram media group album."""
        if not assets:
            return True
        if self.dry_run:
            logger.info("[DRY RUN] Would send Telegram album (%d charts)", len(assets))
            return True
        if not self.is_configured():
            logger.warning("Telegram not configured; album not sent")
            return False

        # Telegram media groups support 2-10 items
        batch = assets[:10]
        media_json = []
        files: dict[str, Any] = {}
        for idx, asset in enumerate(batch):
            file_key = f"photo_{idx}"
            media_item: dict[str, Any] = {
                "type": "photo",
                "media": f"attach://{file_key}",
            }
            if idx == 0:
                media_item["caption"] = caption_prefix or asset.title
            media_json.append(media_item)
            files[file_key] = (asset.filename or f"chart_{idx}.png", BytesIO(asset.content), asset.content_type)

        url = f"{API_BASE}/bot{self.token}/sendMediaGroup"
        data = {"chat_id": self.chat_id, "media": json.dumps(media_json)}
        try:
            resp = requests.post(url, data=data, files=files, timeout=60)
            if resp.ok:
                logger.info("Telegram album sent (%d charts)", len(batch))
                return True
            error = resp.json().get("description", resp.text[:200])
            logger.error("Telegram album send failed: %s", error)
            return False
        except requests.exceptions.RequestException as exc:
            logger.error("Telegram album request failed: %s", exc)
            return False

    def answer_callback_query(self, callback_query_id: str, text: str = "Feedback recorded") -> bool:
        """Acknowledge a Telegram inline keyboard callback to dismiss the loading spinner."""
        if self.dry_run:
            return True
        if not self.is_configured():
            return False
        url = f"{API_BASE}/bot{self.token}/answerCallbackQuery"
        try:
            resp = requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=10)
            return resp.ok
        except requests.exceptions.RequestException:
            return False
