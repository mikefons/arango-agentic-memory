"""Entity summaries in the retrieved context (Graphiti's ENTITIES section, DESIGN.md §23).

Dream State distills a one-line summary per well-attested entity; retrieval can append the
summaries of the entities its hits mention, inside the same token budget.
"""

from __future__ import annotations

import pytest
from arango.database import StandardDatabase

from arango_memory.config import settings
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import force_view_sync, retrieve


def _summarize(db: StandardDatabase, tenant: str, name: str, summary: str) -> None:
    db.aql.execute(
        "FOR e IN entities FILTER e.tenant_id == @t AND e.name == @n "
        "UPDATE e WITH { summary: @s } IN entities",
        bind_vars={"t": tenant, "n": name, "s": summary},
    )


def _seed(db: StandardDatabase, tenant: str) -> None:
    store(db, content="Zeta launched the Orion camera", tenant_id=tenant, agent_id="a")
    store(db, content="Kappa bakes bread on Sundays", tenant_id=tenant, agent_id="a")
    force_view_sync(db, tenant)
    _summarize(db, tenant, "Zeta", "Zeta is the user's employer, a camera maker.")
    _summarize(db, tenant, "Kappa", "Kappa is the user's neighbour.")


def test_off_by_default(db: StandardDatabase) -> None:
    _seed(db, "t_es1")
    result = retrieve(db, query="Zeta camera", tenant_id="t_es1", agent_id="a", k=1)
    assert result.hits and "Entity summaries" not in result.context


def test_appends_summaries_of_entities_the_hits_mention(db: StandardDatabase) -> None:
    _seed(db, "t_es2")
    result = retrieve(db, query="Zeta camera", tenant_id="t_es2", agent_id="a", k=1,
                      entity_summaries=True)
    assert result.hits[0].text.startswith("Zeta")
    memories, _, entities = result.context.partition("\n\nEntity summaries:\n")
    assert "Zeta launched" in memories
    assert entities == "- Zeta: Zeta is the user's employer, a camera maker."
    # Kappa is summarized but no hit mentions it; Orion is mentioned but has no summary.
    assert "Kappa" not in result.context and "- Orion" not in result.context


def test_setting_turns_it_on(db: StandardDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(db, "t_es3")
    monkeypatch.setattr(settings, "retrieve_entity_summaries", True)
    result = retrieve(db, query="Zeta camera", tenant_id="t_es3", agent_id="a", k=1)
    assert "Entity summaries:" in result.context


def test_stays_within_the_token_budget(db: StandardDatabase) -> None:
    _seed(db, "t_es4")
    _summarize(db, "t_es4", "Zeta", "word " * 400)  # far over the 20% entity share
    result = retrieve(db, query="Zeta camera", tenant_id="t_es4", agent_id="a", k=2,
                      max_memory_tokens=100, entity_summaries=True)
    assert "Entity summaries" not in result.context  # an over-budget summary is dropped
    assert 0 < result.tokens_injected <= 100
