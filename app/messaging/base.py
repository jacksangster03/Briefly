"""Messaging interface: abstract base for delivery channels."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.logger import get_logger

logger = get_logger("messaging")


class BaseMessenger(ABC):
    """Abstract messenger. All delivery channels implement this."""

    name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if credentials and settings are present."""
        ...

    @abstractmethod
    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a single message. Return True on success."""
        ...

    def send_messages(self, messages: list[str], parse_mode: str = "HTML") -> bool:
        """Send a list of messages (e.g. split long briefings). Return True if all succeed.

        Each channel's send() method is responsible for handling its own
        configuration and dry-run checks.
        """
        all_ok = True
        for msg in messages:
            if not self.send(msg, parse_mode=parse_mode):
                all_ok = False
        return all_ok
