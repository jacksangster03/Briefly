"""Telegram delivery via the Bot API (raw HTTP, no heavy async library)."""

from __future__ import annotations

import requests

from app.logger import get_logger
from app.messaging.base import BaseMessenger
from app.settings import Settings

logger = get_logger("telegram")

API_BASE = "https://api.telegram.org"


class TelegramMessenger(BaseMessenger):
    name = "telegram"

    def __init__(self, settings: Settings):
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.dry_run = settings.dry_run

    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        if self.dry_run:
            logger.info("[DRY RUN] Would send Telegram message (%d chars)", len(text))
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
                return False
        except requests.exceptions.RequestException as exc:
            logger.error("Telegram request failed: %s", exc)
            return False
