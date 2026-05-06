"""Retry/backoff helper for transient messenger delivery failures."""

from __future__ import annotations

import time
from typing import Callable

from app.logger import get_logger

logger = get_logger("messaging.retry")

# Seconds to wait between attempts: attempt 1->2 waits 10s, attempt 2->3 waits 30s.
_DEFAULT_DELAYS: tuple[int, ...] = (10, 30)


def call_with_retry(
    fn: Callable[[], bool],
    *,
    channel: str,
    max_attempts: int = 3,
    delay_seconds: tuple[int, ...] = _DEFAULT_DELAYS,
) -> tuple[bool, str]:
    """Call fn() up to max_attempts times with fixed-gap backoff.

    Returns (success, last_error_reason).
    fn() must return True on success, False on transient failure.
    Exceptions from fn() are caught and treated as failures.
    Sleeps between attempts using delay_seconds[attempt_index]; if there are
    fewer delay values than gaps, the last value is reused.
    """
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            ok = fn()
        except Exception as exc:
            ok = False
            last_error = str(exc)
            logger.warning(
                "%s delivery: attempt %d/%d raised: %s",
                channel, attempt, max_attempts, exc,
            )
        else:
            if ok:
                if attempt > 1:
                    logger.info(
                        "%s delivery: succeeded on attempt %d/%d",
                        channel, attempt, max_attempts,
                    )
                return True, ""
            last_error = f"attempt {attempt} returned failure"
            logger.warning(
                "%s delivery: attempt %d/%d failed",
                channel, attempt, max_attempts,
            )

        if attempt < max_attempts:
            idx = min(attempt - 1, len(delay_seconds) - 1)
            delay = delay_seconds[idx]
            logger.info(
                "%s delivery: retrying in %ds (attempt %d/%d)...",
                channel, delay, attempt + 1, max_attempts,
            )
            time.sleep(delay)

    logger.error(
        "%s delivery: all %d attempt(s) exhausted. Last error: %s",
        channel, max_attempts, last_error,
    )
    return False, last_error
