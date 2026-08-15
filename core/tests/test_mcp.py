"""MCP server: tool logic over the core API + tool registration (DESIGN.md §21)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from arango_memory.mcp import tools
from arango_memory.mcp.server import build_server


def test_store_then_search(api: TestClient) -> None:
    queued = tools.store_memory(api, content="Alice likes coffee", tenant_id="mcp1", agent_id="a")
    assert queued["status"] == "queued"

    result: dict[str, object] = {}
    for _ in range(20):
        result = tools.search_memory(api, query="coffee", tenant_id="mcp1", agent_id="a")
        if result["hits"]:
            break
        time.sleep(0.25)
    assert any("coffee" in h["text"] for h in result["hits"])  # type: ignore[union-attr]


def test_record_then_list_steps(api: TestClient) -> None:
    queued = tools.record_step(
        api, tool_name="search", arguments={"q": "x"}, outcome="success",
        tenant_id="mcp2", agent_id="a",
    )
    assert queued["status"] == "queued"

    steps: list[dict[str, object]] = []
    for _ in range(20):
        steps = tools.list_steps(api, tenant_id="mcp2", agent_id="a")["steps"]
        if steps:
            break
        time.sleep(0.25)
    assert steps[0]["tool_name"] == "search"


def test_stats_and_forget(api: TestClient) -> None:
    tools.store_memory(api, content="bravo charlie", tenant_id="mcp3", agent_id="a")
    stats = tools.graph_stats(api, tenant_id="mcp3")
    assert "memories" in stats["counts"]

    forgotten = tools.forget_memory(api, tenant_id="mcp3")
    assert forgotten["status"] == "forgotten"


def test_seed_and_entity_tools(api: TestClient) -> None:
    seeded = tools.seed_profile(
        api, profile={"role": "analyst", "preferences": ["sql"]}, tenant_id="mcp4", agent_id="a"
    )
    assert seeded["status"] == "seeded"

    entities = tools.list_entities(api, tenant_id="mcp4")["entities"]
    assert {"analyst", "sql"} <= {e["name"] for e in entities}

    one = tools.get_entity(api, entity_id=entities[0]["id"], tenant_id="mcp4")
    assert one["entity"]["id"] == entities[0]["id"]


def test_prime_and_flush_tools(api: TestClient) -> None:
    # Agent A writes (sync so it's immediately visible); agent B primes across the tier.
    api.post("/v1/store", json={
        "content": "the bridge is trapped",
        "ctx": {"tenant_id": "mcp5", "agent_id": "shared", "access_level": "write"},
        "sync": True,
    })
    briefing = tools.prime_memory(
        api, task="is the bridge safe", tenant_id="mcp5", agent_id="b",
        read_agent_ids=["b", "shared"],
    )
    assert "bridge" in briefing["context"].lower()
    assert set(briefing) == {"context", "hits", "entities", "steps", "tokens_injected"}

    flushed = tools.flush_memory(api, tenant_id="mcp5", agent_id="b")
    assert flushed["status"] in {"flushed", "timeout"}


async def test_server_registers_expected_tools() -> None:
    server = build_server()
    names = {tool.name for tool in await server.list_tools()}
    assert names == {
        "store", "search", "prime", "flush", "record_step", "list_steps", "forget",
        "stats", "get_entity", "list_entities", "seed",
    }


def test_personal_assistant_recall_across_sessions(api: TestClient) -> None:
    """Smoke-guards the `examples/mcp-memory` scenario: facts stored in one 'session' are
    recalled in another over the memory backend (persistence). Uses the same store→search tool
    path the MCP server exposes. The example's live demo uses natural-language queries + real
    embeddings; here the queries share a keyword so retrieval is deterministic under the keyless
    FakeEmbedder (BM25 carries it)."""
    tenant = "mcp-demo"
    facts = [
        "I'm allergic to shellfish.",
        "My sister Mira is visiting next week.",
        "I moved from Munich to Berlin in March.",
    ]
    for fact in facts:
        tools.store_memory(api, content=fact, tenant_id=tenant, agent_id="assistant")

    def recall(query: str) -> list[dict[str, object]]:
        for _ in range(20):
            hits = tools.search_memory(
                api, query=query, tenant_id=tenant, agent_id="assistant"
            )["hits"]
            if hits:
                return hits  # type: ignore[no-any-return]
            time.sleep(0.25)
        return []

    assert any("shellfish" in h["text"] for h in recall("shellfish allergy"))
    assert any("Mira" in h["text"] for h in recall("who is Mira"))
    assert any("Berlin" in h["text"] for h in recall("Berlin move"))
