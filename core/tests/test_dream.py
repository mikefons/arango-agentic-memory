"""Consolidation / Dream State pass (DESIGN.md §13)."""

from __future__ import annotations

from arango.database import StandardDatabase

from arango_memory.generation import FakeGenerator
from arango_memory.ingest.store import store
from arango_memory.lifecycle.dream import run_dream_state


def _key_of(db: StandardDatabase, tenant: str, name: str) -> str:
    return next(
        db.aql.execute(
            "FOR e IN entities FILTER e.tenant_id == @t AND e.name == @n RETURN e._key",
            bind_vars={"t": tenant, "n": name},
        )
    )


def _gen(conflict: str = "DISTINCT", summary: str = "") -> FakeGenerator:
    # The conflict-review system prompt is the only one mentioning "DISTINCT".
    def handler(prompt: str, system: str | None) -> str:
        return conflict if system and "DISTINCT" in system else summary

    return FakeGenerator(handler=handler)


def _flag(db: StandardDatabase, key: str, conflict_with: str) -> None:
    db.collection("entities").update(
        {"_key": key, "needs_review": True, "conflict_with": conflict_with}
    )


def test_distinct_verdict_clears_flag_without_supersede(db: StandardDatabase) -> None:
    t = "t_d1"
    store(db, content="Acme Corp and Globex Inc", tenant_id=t, agent_id="a")
    acme, globex = _key_of(db, t, "Acme Corp"), _key_of(db, t, "Globex Inc")
    _flag(db, acme, globex)

    result = run_dream_state(db, tenant_id=t, generator=_gen(conflict="DISTINCT"))
    assert result.cleared == 1
    assert result.superseded == 0
    assert not result.breaker_tripped
    assert db.collection("entities").get(acme)["needs_review"] is False
    assert db.collection("Supersedes").count() == 0


def test_contradiction_verdict_supersedes(db: StandardDatabase) -> None:
    t = "t_d2"
    store(db, content="Acme Corp and Globex Inc", tenant_id=t, agent_id="a")
    acme, globex = _key_of(db, t, "Acme Corp"), _key_of(db, t, "Globex Inc")
    _flag(db, acme, globex)

    # breaker_threshold=1.0 so a single 100%-deprecation run still applies.
    result = run_dream_state(
        db, tenant_id=t, generator=_gen(conflict="CONTRADICTS"), breaker_threshold=1.0
    )
    assert result.superseded == 1
    assert db.collection("Supersedes").get(f"{acme}__{globex}") is not None
    assert db.collection("entities").get(globex)["invalid_at"] is not None
    assert db.collection("entities").get(acme)["needs_review"] is False


def test_distills_summary_for_well_attested_entity(db: StandardDatabase) -> None:
    t = "t_d3"
    store(db, content="Zeta launched", tenant_id=t, agent_id="a")
    zeta = _key_of(db, t, "Zeta")
    db.collection("entities").update({"_key": zeta, "mention_count": 5})

    result = run_dream_state(db, tenant_id=t, generator=_gen(summary="Zeta is a product."))
    assert result.consolidated == 1
    entity = db.collection("entities").get(zeta)
    assert entity["summary"] == "Zeta is a product."
    assert entity["consolidated_at"] is not None


def test_circuit_breaker_halts_mass_deprecation(db: StandardDatabase) -> None:
    t = "t_d4"
    store(db, content="Acme Corp and Globex Inc", tenant_id=t, agent_id="a")
    store(db, content="Stark Co and Wayne Co", tenant_id=t, agent_id="a")
    acme, globex = _key_of(db, t, "Acme Corp"), _key_of(db, t, "Globex Inc")
    stark, wayne = _key_of(db, t, "Stark Co"), _key_of(db, t, "Wayne Co")
    _flag(db, acme, globex)
    _flag(db, stark, wayne)

    # Both flagged → both would supersede → 100% deprecation > 0.5 → halt.
    result = run_dream_state(db, tenant_id=t, generator=_gen(conflict="CONTRADICTS"))
    assert result.breaker_tripped is True
    assert result.superseded == 0
    assert db.collection("Supersedes").count() == 0
    assert db.collection("entities").get(acme)["needs_review"] is True   # nothing applied
    assert db.collection("entities").get(globex)["invalid_at"] is None
