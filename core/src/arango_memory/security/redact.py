"""PII redaction at ingestion (DESIGN.md §17).

Redacted text is what gets persisted — the original is never stored. A
deterministic regex pass (always on) handles structured secrets/PII; in full
mode an additional generator pass catches contextual PII (names, addresses).
Patterns are deliberately specific so ordinary prose and numbers pass through
untouched.
"""

from __future__ import annotations

import re

from ..generation import Generator

# (compiled pattern, placeholder). Order matters — keys before generic tokens.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[REDACTED_CARD]"),
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\b(?:AKIA|ghp_|gho_|xoxb-)[A-Za-z0-9-]{10,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{12,}\b"), "[REDACTED_TOKEN]"),
]

_LLM_SYSTEM = (
    "Redact personal data (names, addresses, phone numbers) from the text by "
    "replacing each with a [REDACTED_PII] placeholder. Return ONLY the redacted "
    "text, preserving everything else verbatim."
)


def redact_regex(text: str) -> str:
    """Apply the deterministic structured-PII patterns."""
    for pattern, placeholder in _PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


def redact(text: str, *, mode: str = "lite", generator: Generator | None = None) -> str:
    """Redact PII. Regex always; full mode adds a generator pass for contextual PII."""
    redacted = redact_regex(text)
    if mode == "full" and generator is not None:
        out = generator.complete(redacted, system=_LLM_SYSTEM).strip()
        if out:
            redacted = out
    return redacted
