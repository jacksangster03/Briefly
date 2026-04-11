"""Email delivery via SMTP (Python stdlib, no external dependency)."""

from __future__ import annotations

import re
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.logger import get_logger
from app.messaging.base import BaseMessenger
from app.schemas.delivery import ChartAsset
from app.settings import Settings

logger = get_logger("email")


class EmailMessenger(BaseMessenger):
    name = "email"

    def __init__(self, settings: Settings):
        self.host = settings.email_host
        self.port = settings.email_port
        self.user = settings.email_user
        self.password = settings.email_password
        self.to_addr = settings.email_to
        self.dry_run = settings.dry_run

    def is_configured(self) -> bool:
        return bool(self.user and self.password and self.to_addr)

    def send(self, text: str, parse_mode: str = "HTML", subject: str = "") -> bool:
        if self.dry_run:
            logger.info("[DRY RUN] Would send email (%d chars)", len(text))
            return True

        if not self.is_configured():
            logger.warning("Email not configured; message not sent")
            return False

        if not subject:
            # Extract subject from first line of text
            plain = re.sub(r"<[^>]+>", "", text)
            subject = plain.split("\n")[0][:80] or "Market Briefing"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.user
        msg["To"] = self.to_addr

        # Plain text version (strip HTML)
        plain_text = re.sub(r"<[^>]+>", "", text)
        msg.attach(MIMEText(plain_text, "plain"))

        # HTML version
        html_body = text.replace("\n", "<br>\n")
        msg.attach(MIMEText(f"<html><body>{html_body}</body></html>", "html"))

        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.user, self.to_addr, msg.as_string())
            logger.info("Email sent to %s (%d chars)", self.to_addr, len(text))
            return True
        except Exception as exc:
            logger.error("Email send failed: %s", exc)
            return False

    def send_rich(
        self,
        *,
        subject: str,
        plain_text: str,
        html_body: str,
        inline_assets: list[ChartAsset] | None = None,
    ) -> bool:
        """Send an HTML email with optional inline PNG charts."""
        inline_assets = inline_assets or []

        if self.dry_run:
            logger.info(
                "[DRY RUN] Would send rich email (%d chars, %d inline assets)",
                len(plain_text),
                len(inline_assets),
            )
            return True

        if not self.is_configured():
            logger.warning("Email not configured; rich message not sent")
            return False

        msg = MIMEMultipart("related")
        msg["Subject"] = subject or "Market Briefing"
        msg["From"] = self.user
        msg["To"] = self.to_addr

        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(plain_text, "plain"))
        alternative.attach(MIMEText(html_body, "html"))
        msg.attach(alternative)

        for asset in inline_assets:
            subtype = (asset.content_type.split("/")[-1] or "png").lower()
            image = MIMEImage(asset.content, _subtype=subtype)
            image.add_header("Content-ID", f"<{asset.content_id}>")
            image.add_header("Content-Disposition", "inline", filename=asset.filename or f"{asset.key}.png")
            msg.attach(image)

        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.user, self.to_addr, msg.as_string())
            logger.info(
                "Rich email sent to %s (%d chars, %d inline assets)",
                self.to_addr,
                len(plain_text),
                len(inline_assets),
            )
            return True
        except Exception as exc:
            logger.error("Rich email send failed: %s", exc)
            return False
