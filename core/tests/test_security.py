"""PII redaction + WORM enforcement (DESIGN.md §17)."""

from __future__ import annotations

import pytest
from arango.database import StandardDatabase

from arango_memory.generation import FakeGenerator
from arango_memory.ingest.store import store
from arango_memory.security import WormViolation, redact, redact_regex, worm_guard


# ── redaction (unit) ──────────────────────────────────────
def test_redact_regex_masks_structured_pii() -> None:
    out = redact_regex("mail a@b.com ssn 123-45-6789 key sk-ABCDEFGHIJKLMNOP12345")
    assert "[REDACTED_EMAIL]" in out and "a@b.com" not in out
    assert "[REDACTED_SSN]" in out and "123-45-6789" not in out
    assert "[REDACTED_KEY]" in out and "sk-ABCDEF" not in out


def test_redact_leaves_ordinary_text_untouched() -> None:
    text = "Alice met Bob in Paris and bought 1.6 kilograms of coffee for 4 people"
    assert redact_regex(text) == text


def test_redact_full_mode_runs_generator_pass() -> None:
    gen = FakeGenerator(handler=lambda p, s: "[REDACTED_PII] lives here")
    result = redact("John Smith lives here", mode="full", generator=gen)
    assert result == "[REDACTED_PII] lives here"


def test_redact_lite_mode_skips_generator() -> None:
    calls = {"n": 0}

    def handler(prompt: str, system: str | None) -> str:
        calls["n"] += 1
        return "x"

    redact("hello world", mode="lite", generator=FakeGenerator(handler=handler))
    assert calls["n"] == 0


# ── WORM (unit) ───────────────────────────────────────────
def test_worm_guard_blocks_episodes_allows_others() -> None:
    with pytest.raises(WormViolation):
        worm_guard("episodes")
    worm_guard("memories")  # no raise


# ── ingestion integration ─────────────────────────────────
def test_store_persists_only_redacted_content(db: StandardDatabase) -> None:
    store(db, content="email jane@acme.com about 123-45-6789", tenant_id="t_pii", agent_id="a")

    episode = next(db.aql.execute("FOR e IN episodes RETURN e"))
    memory = next(db.aql.execute("FOR m IN memories RETURN m"))
    assert "jane@acme.com" not in episode["content"]
    assert "123-45-6789" not in episode["content"]
    assert "[REDACTED_EMAIL]" in episode["content"]
    assert "jane@acme.com" not in memory["text"]  # original never stored anywhere
