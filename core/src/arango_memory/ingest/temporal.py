"""Explicit valid-time extraction (DESIGN.md §4, §8 Stage 2).

When a memory states *when* a fact holds ("We moved to Berlin in 2019"), that is
the entity's `valid_time` — distinct from the ingestion time. This is a
deterministic, keyless regex parser (no model, no extra): it recognises ISO
dates, "Month [DD,] YYYY", and bare 4-digit years, returning the earliest match
as a UTC ISO-8601 string. Absent an explicit date it returns `None` and the
caller falls back to ingestion time (`valid_time_explicit = False`).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH_RE = "|".join(_MONTHS)

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY_YEAR = re.compile(
    rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE
)
_MONTH_YEAR = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{4}})\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def _iso(year: int, month: int = 1, day: int = 1) -> str | None:
    try:
        return datetime(year, month, day, tzinfo=UTC).isoformat()
    except ValueError:
        return None


def parse_explicit_time(text: str) -> str | None:
    """Return the first explicit date in `text` as a UTC ISO string, else None."""
    if m := _ISO.search(text):
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if m := _MONTH_DAY_YEAR.search(text):
        return _iso(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
    if m := _MONTH_YEAR.search(text):
        return _iso(int(m.group(2)), _MONTHS[m.group(1).lower()])
    if m := _YEAR.search(text):
        return _iso(int(m.group(1)))
    return None
