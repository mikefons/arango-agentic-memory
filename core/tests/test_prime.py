"""Task briefing endpoint `/v1/prime` (MA-3, §14/§19).

The handoff verb: agent B primes for a task and gets agent A's history + entities +
tool runs in one budgeted package, spanning read_agent_ids (MA-2). Leads with the pure
budget-assembly logic, then the end-to-end handoff shape.
"""

from __future__ import annotations

from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.ingest.procedural import record_step
from arango_memory.ingest.store import store
from arango_memory.retrieve.prime import Include, _assemble, prime
from arango_memory.retrieve.search import MemoryHit, force_view_sync


# ── pure assembly (no DB) ─────────────────────────────────
def test_assemble_respects_token_budget_and_truncates_lowest_ranked() -> None:
    # Many small, pre-sorted hits; a small budget keeps a subset, drops the tail.
    hits = [MemoryHit(text=f"history fact {i}", score=1.0 - i / 100) for i in range(50)]
    result = _assemble(hits, entities=[], steps=[], max_tokens=80, include=Include())
    assert result.tokens_injected <= 80
    assert 0 < len(result.hits) < 50, "budget should keep some but not all hits"
    # Kept hits are the top-scored ones (truncation drops the tail).
    assert result.hits[0].score >= result.hits[-1].score


def test_assemble_omits_disabled_and_empty_sections() -> None:
    hits = [MemoryHit(text="a fact", score=0.9)]
    steps = [{"tool_name": "search", "outcome": "success", "use_count": 3}]
    result = _assemble(
        hits, entities=[], steps=steps, max_tokens=1500,
        include=Include(episodic=False, semantic=True, procedural=True),
    )
    assert "Relevant history" not in result.context   # disabled
    assert "Key entities" not in result.context        # enabled but empty
    assert "Prior tool runs" in result.context
    assert result.hits == []                            # disabled section → not returned


def test_assemble_all_three_sections_present() -> None:
    result = _assemble(
        [MemoryHit(text="h", score=1.0)],
        entities=[{"name": "Cook", "label": "person", "summary": "the kitchen hand"}],
        steps=[{"tool_name": "confront", "outcome": "success", "use_count": 2}],
        max_tokens=1500, include=Include(),
    )
    for header in ("## Relevant history", "## Key entities", "## Prior tool runs"):
        assert header in result.context


# ── end-to-end (DB) ───────────────────────────────────────
def test_prime_briefs_agent_b_from_agent_a(db: StandardDatabase) -> None:
    # Agent A works a session: stores facts + records a tool run under a shared tier.
    store(db, content="the cook was seen near the vault at midnight", tenant_id="t",
          agent_id="guild::query")
    store(db, content="the guard's alibi contradicts the cook", tenant_id="t",
          agent_id="guild::query")
    record_step(db, tool_name="confront", arguments={"npc": "cook"}, outcome="success",
                tenant_id="t", agent_id="guild::query")
    force_view_sync(db, "t")

    # Agent B primes for the next job, reading across its own id + the shared tier.
    result = prime(
        db, task="who is the traitor near the vault", tenant_id="t", agent_id="brann",
        read_agent_ids=["brann", "guild::query"],
    )
    assert "cook" in result.context.lower(), "briefing missing A's history"
    assert result.hits, "expected retrieved history"
    assert any(s["tool_name"] == "confront" for s in result.steps), "missing A's tool run"


def test_prime_procedural_spans_read_agents(db: StandardDatabase) -> None:
    record_step(db, tool_name="dig", arguments={}, outcome="success",
                tenant_id="t2", agent_id="a")
    record_step(db, tool_name="parley", arguments={}, outcome="failure",
                tenant_id="t2", agent_id="b")
    tools = {
        s["tool_name"]
        for s in prime(db, task="x", tenant_id="t2", agent_id="a",
                       read_agent_ids=["a", "b"]).steps
    }
    assert {"dig", "parley"} <= tools


def test_prime_endpoint_over_http(api: TestClient) -> None:
    ctx = {"tenant_id": "t_http", "agent_id": "a", "access_level": "write"}
    api.post("/v1/store", json={"content": "the bridge is trapped", "ctx": ctx, "sync": True})
    resp = api.post(
        "/v1/prime",
        json={"task": "is the bridge safe", "ctx": {"tenant_id": "t_http", "agent_id": "a"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"context", "hits", "entities", "steps", "tokens_injected"}
    assert "bridge" in body["context"].lower()
