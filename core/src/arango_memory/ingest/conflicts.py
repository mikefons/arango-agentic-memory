"""Write conflicts between concurrent graph writers.

A graph write is retried where it fails, never by replaying the whole write: once the
episode and memory are stored, a replay treats the turn as a duplicate and skips the graph,
so its entity links would be lost. Each statement retried here applies all or nothing (on a
single server or OneShard), so the retry cannot count anything twice.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from ..telemetry.logging import logger

# ArangoDB errors that mean "another writer got there first": write-write conflict and unique
# constraint violation.
_CONFLICT_CODES = frozenset({1200, 1210})
_ATTEMPTS = 8
_BASE_DELAY = 0.05  # seconds, doubling up to _MAX_DELAY: about 3.5 s of waiting in all
_MAX_DELAY = 1.0

T = TypeVar("T")


def is_conflict(exc: BaseException) -> bool:
    return getattr(exc, "error_code", None) in _CONFLICT_CODES


def retry_on_conflict(write: Callable[[], T]) -> T:
    """Run one atomic write, waiting and running it again while it conflicts."""
    for attempt in range(_ATTEMPTS - 1):
        try:
            return write()
        except Exception as exc:
            if not is_conflict(exc):
                raise
            # Jitter, so two writers that collided don't come back in step.
            time.sleep(min(_BASE_DELAY * 2**attempt, _MAX_DELAY) * random.uniform(0.5, 1.5))
    try:
        return write()
    except Exception as exc:
        if is_conflict(exc):
            logger.error("graph write still conflicting; this write's entity links may be lost",
                         extra={"attempts": _ATTEMPTS, "detail": str(exc)})
        raise
