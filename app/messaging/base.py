"""Messaging interface: abstract base for delivery channels."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.logger import get_logger

logger = get_logger("messaging")


class BaseMessenger(ABC):
    """Abstract messenger. All delivery channels implement this."""

    name: str = "base"
    last_error: str = ""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if credentials and settings are present."""
        ...

    @abstractmethod
    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a single message. Return True on success."""
        ...

    def send_messages(self, messages: list[str], parse_mode: str = "HTML") -> bool:
        """Send a list of messages with per-message retry on transient failures.

        Live, configured sends are retried up to the default max_attempts with
        backoff. Dry-run and unconfigured channels go through send() directly
        without retry so they never sleep.
        """
        from app.messaging.retry import call_with_retry
        dry_run = getattr(self, "dry_run", False)
        all_ok = True
        for msg in messages:
            if dry_run or not self.is_configured():
                if not self.send(msg, parse_mode=parse_mode):
                    all_ok = False
            else:
                ok, reason = call_with_retry(
                    lambda m=msg, pm=parse_mode: self.send(m, parse_mode=pm),
                    channel=self.name,
                )
                if not ok:
                    all_ok = False
                    self.last_error = reason
        return all_ok
