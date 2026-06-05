"""Agentic simulation harness — the real-data regression gate (DESIGN.md §22, Step 3.5a).

Asserts the four end-to-end categories: cross-session recall (lite and full),
procedural memory + reuse, graceful degradation, and tenant isolation.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from arango.database import StandardDatabase
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import arango_memory.ingest.worker as worker_mod
from arango_memory.sim import load_scenario, run_scenario

SCENARIO = Path(__file__).parent / "data" / "sim_scenario.json"
RECALL_FLOOR = 0.66  # lite/BM25 floor for the smoke slice


def _poll_steps(api: TestClient, tenant_id: str, want: int) -> list[dict[str, Any]]:
    for _ in range(40):
        resp = api.get("/v1/steps", params={"tenant_id": tenant_id, "agent_id": "assistant"})
        steps = resp.json()["steps"]
        if len(steps) >= want:
            return steps
        time.sleep(0.25)
    return steps


def _poll_hits(api: TestClient, query: str, ctx: dict[str, str]) -> list[dict[str, Any]]:
    for _ in range(40):
        resp = api.post("/v1/retrieve", json={"query": query, "ctx": ctx})
        hits = resp.json()["hits"]
        if hits:
            return hits
        time.sleep(0.25)
    return hits


@pytest.mark.parametrize("mode", ["lite", "full"])
def test_sim_cross_session_recall(api: TestClient, mode: str) -> None:
    result = run_scenario(api, load_scenario(SCENARIO), mode=mode)
    assert result.steps_recorded == 4  # tool calls issued across the scenario
    assert result.recall_at_k >= RECALL_FLOOR, (
        f"{mode}: Recall@{result.n_questions}={result.recall_at_k:.2f} below {RECALL_FLOOR}"
    )


def test_sim_procedural_memory_and_reuse(api: TestClient, db: StandardDatabase) -> None:
    scenario = load_scenario(SCENARIO)
    run_scenario(api, scenario)

    steps = _poll_steps(api, scenario.scenario_id, want=3)
    by_name = {s["tool_name"]: s for s in steps}
    assert set(by_name) == {"weather_lookup", "product_search", "booking_create"}
    # product_search recurs in two sessions → reused, not duplicated.
    assert by_name["product_search"]["use_count"] == 2
    # Procedural edges: step → triggering memory, and step → step sequencing.
    assert db.collection("TOUCHED").count() >= 3
    assert db.collection("TRANSITION").count() >= 1


def test_sim_write_failure_is_isolated(api: TestClient, monkeypatch: MonkeyPatch) -> None:
    # Simulate the DB being unreachable for writes (§15): the turn must not break.
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(worker_mod, "store", boom)
    ctx = {"tenant_id": "t_deg", "agent_id": "a"}

    stored = api.post("/v1/store", json={"content": "an important fact", "ctx": ctx})
    assert stored.status_code == 200
    assert stored.json()["status"] == "queued"  # enqueue never blocks/raises

    # Retrieval degrades to a working, memory-less turn (no 500, empty context).
    got = api.post("/v1/retrieve", json={"query": "important fact", "ctx": ctx})
    assert got.status_code == 200
    assert got.json()["hits"] == []


def test_sim_tenant_isolation(api: TestClient) -> None:
    a = {"tenant_id": "iso_a", "agent_id": "x"}
    b = {"tenant_id": "iso_b", "agent_id": "x"}
    api.post("/v1/store", json={"content": "alpha amber falcon", "ctx": a})
    api.post("/v1/store", json={"content": "beta zephyr marker", "ctx": b})

    assert _poll_hits(api, "zephyr marker", b)  # present for its own tenant
    cross = api.post("/v1/retrieve", json={"query": "zephyr marker", "ctx": a})
    assert cross.json()["hits"] == []  # never leaks to another tenant
