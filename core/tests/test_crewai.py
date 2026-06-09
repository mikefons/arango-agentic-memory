"""CrewAI adapter — in-process shared-crew memory over the core (DESIGN.md §14, §21).

The storage logic is crewai-free, so it's tested directly against testcontainers
ArangoDB with the Fake providers (keyless). The `crewai.Storage` shim is tested
against a stub `crewai` module injected into `sys.modules` — no heavy real dep.
"""

from __future__ import annotations

import sys
import time
import types
from collections.abc import Iterator

import pytest
from arango.database import StandardDatabase

from arango_memory.crewai import ArangoCrewStorage, crew_memory


def _wait_search(
    storage: ArangoCrewStorage, query: str, attempts: int = 20, delay: float = 0.25
) -> list[dict[str, object]]:
    for _ in range(attempts):
        hits = storage.search(query)
        if hits:
            return hits
        time.sleep(delay)
    return storage.search(query)


# ── storage roundtrip ─────────────────────────────────────
def test_save_and_search_roundtrip(db: StandardDatabase) -> None:
    storage = ArangoCrewStorage(db, tenant_id="cw1", agent_id="analyst")
    storage.save("The quarterly revenue target is two million")
    hits = _wait_search(storage, "revenue target")
    assert any("revenue" in h["context"] for h in hits)  # type: ignore[operator]
    assert all("embedding" not in h["metadata"] for h in hits)  # type: ignore[operator]


def test_search_respects_limit(db: StandardDatabase) -> None:
    storage = ArangoCrewStorage(db, tenant_id="cw2", agent_id="a", k=5)
    for i in range(4):
        storage.save(f"fact number {i} about logistics planning")
    _wait_search(storage, "logistics")
    assert len(storage.search("logistics", limit=2)) <= 2


def test_reset_soft_deletes(db: StandardDatabase) -> None:
    storage = ArangoCrewStorage(db, tenant_id="cw3", agent_id="a")
    storage.save("ephemeral crew note")
    _wait_search(storage, "ephemeral")
    storage.reset()
    for _ in range(20):
        if not storage.search("ephemeral"):
            break
        time.sleep(0.25)
    assert storage.search("ephemeral") == []


# ── G-Memory tiers (§14) ──────────────────────────────────
def test_tiers_isolate_private_and_share_query(db: StandardDatabase) -> None:
    analyst = crew_memory(db, tenant_id="cw4", crew_id="research", agent_id="analyst")
    analyst.interaction.save("analyst private scratchpad about widgets")
    analyst.query.save("shared crew decision: ship on friday")
    _wait_search(analyst.query, "shared crew decision")

    # a different crew member sees the shared query tier, not the analyst's private one
    writer = crew_memory(db, tenant_id="cw4", crew_id="research", agent_id="writer")
    assert any("ship on friday" in h["context"] for h in _wait_search(writer.query, "ship"))
    assert writer.interaction.search("widgets") == []


def test_insight_tier_is_read_only(db: StandardDatabase) -> None:
    mem = crew_memory(db, tenant_id="cw5", crew_id="research", agent_id="a")
    mem.insight.save("agents must not write insights")  # no-op
    assert mem.insight.read_only is True
    # the Dream State path writes insights; simulate it via the shared insight agent_id
    ArangoCrewStorage(db, tenant_id="cw5", agent_id="research::insight").save(
        "distilled strategy: prefer cached retrieval"
    )
    assert any(
        "distilled strategy" in h["context"]
        for h in _wait_search(mem.insight, "distilled strategy")
    )


# ── crewai.Storage shim (stubbed crewai) ──────────────────
@pytest.fixture
def stub_crewai() -> Iterator[None]:
    """Inject a minimal fake `crewai.memory.storage.interface.Storage` base."""
    mod_crewai = types.ModuleType("crewai")
    mod_memory = types.ModuleType("crewai.memory")
    mod_storage = types.ModuleType("crewai.memory.storage")
    mod_iface = types.ModuleType("crewai.memory.storage.interface")

    class Storage:  # mirrors crewai's legacy text-based interface
        def save(self, value: object, metadata: object = None) -> None: ...
        def search(self, query: str, limit: int = 3, score_threshold: float = 0.35) -> object: ...
        def reset(self) -> None: ...

    mod_iface.Storage = Storage  # type: ignore[attr-defined]
    injected = {
        "crewai": mod_crewai,
        "crewai.memory": mod_memory,
        "crewai.memory.storage": mod_storage,
        "crewai.memory.storage.interface": mod_iface,
    }
    saved = {name: sys.modules.get(name) for name in injected}
    sys.modules.update(injected)
    try:
        yield
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


def test_shim_delegates_to_core(db: StandardDatabase, stub_crewai: None) -> None:
    from arango_memory.crewai import to_crewai_storage

    base = ArangoCrewStorage(db, tenant_id="cw6", agent_id="a")
    shim = to_crewai_storage(base)
    assert isinstance(shim, sys.modules["crewai.memory.storage.interface"].Storage)

    shim.save("crew memory via the shim", {"agent": "a"})
    for _ in range(20):
        if shim.search("crew memory"):
            break
        time.sleep(0.25)
    assert any("crew memory" in h["context"] for h in shim.search("crew memory"))
    shim.reset()
