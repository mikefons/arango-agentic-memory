"""Integration tests for prospective indexing (DESIGN.md §8 Stage 4, full mode)."""

from __future__ import annotations

from collections.abc import Callable

from arango.database import StandardDatabase

from arango_memory.generation import FakeGenerator
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult


def test_prospective_query_makes_memory_findable(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
) -> None:
    ctx = {"tenant_id": "t_pros", "agent_id": "a"}
    # The hypothetical questions share no words with the stored text.
    gen = FakeGenerator(
        handler=lambda p, s: "what is the user's favorite color\nwhich color do they prefer"
    )
    store(db, content="The sky was a deep shade today", mode="full", generator=gen, **ctx)

    # Query matches a prospective question, not the original text.
    result = wait_for_searchable(db, query="favorite color", **ctx)
    assert result.hits
    assert any("deep shade" in h.text for h in result.hits)


def test_lite_mode_writes_no_prospective_queries(db: StandardDatabase) -> None:
    store(db, content="some lowercase content", mode="lite", tenant_id="t_lite", agent_id="a")
    memory = next(
        db.aql.execute(
            "FOR m IN memories FILTER m.tenant_id == @t RETURN m", bind_vars={"t": "t_lite"}
        )
    )
    assert memory["prospective_queries"] == []
