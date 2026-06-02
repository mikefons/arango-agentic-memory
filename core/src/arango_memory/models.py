"""Internal record models and helpers shared across the pipeline.

Step 0 covers the minimal walking-skeleton records: episodes (WORM provenance)
and memories (episodic). Entities and edges are added in later steps.
See DESIGN.md §5.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime


def utcnow_iso() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


def idempotency_key(
    *, tenant_id: str, agent_id: str, session_id: str | None, content: str, turn_index: int
) -> str:
    """Deterministic key so retries/re-runs cannot duplicate a record (DESIGN.md §5).

    Used as the document `_key` for episodes/memories, making inserts naturally
    idempotent via overwrite_mode="ignore".
    """
    raw = f"{tenant_id}\x1f{agent_id}\x1f{session_id or ''}\x1f{turn_index}\x1f{content}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
