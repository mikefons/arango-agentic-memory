"""Scenario runner — plays an agent loop against the core's HTTP API (DESIGN.md §22).

Drives the same endpoints the Vercel adapter calls (`/v1/store`, `/v1/step`,
`/v1/retrieve`). Writes are async (durable queue), so QA waits until the worker
has drained and the search view is consistent. The client is any object with
httpx-style `post`/`get` (the test passes a FastAPI `TestClient`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from ..eval.locomo import _recall_hit, _token_f1
from .scenario import Scenario


class _Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


class HttpClient(Protocol):
    def post(self, url: str, *, json: Any) -> _Response: ...
    def get(self, url: str, *, params: Any) -> _Response: ...


@dataclass
class SimResult:
    scenario_id: str
    mode: str
    recall_at_k: float
    mean_f1: float
    n_questions: int
    steps_recorded: int


def _ctx(scenario: Scenario, agent_id: str) -> dict[str, str]:
    return {"tenant_id": scenario.scenario_id, "agent_id": agent_id}


def _play_sessions(client: HttpClient, scenario: Scenario, ctx: dict[str, str]) -> int:
    """Store every turn and record its tool calls. Returns steps issued."""
    write_ctx = {**ctx, "access_level": "write"}  # mutating endpoints require write (§17)
    steps = 0
    turn_index = 0
    prev_step_key: str | None = None
    for session in scenario.sessions:
        for turn in session:
            store = client.post(
                "/v1/store",
                json={"content": f"{turn.speaker}: {turn.text}", "ctx": write_ctx,
                      "turn_index": turn_index},
            )
            memory_key = store.json()["memory_ids"][0]
            turn_index += 1
            for tool in turn.tools:
                resp = client.post(
                    "/v1/step",
                    json={
                        "tool_name": tool.tool_name,
                        "arguments": tool.arguments,
                        "outcome": tool.outcome,
                        "ctx": write_ctx,
                        "source_memory_key": memory_key,
                        "prev_step_key": prev_step_key,
                    },
                )
                prev_step_key = resp.json()["step_id"]
                steps += 1
    return steps


def _await_ready(
    client: HttpClient, scenario: Scenario, ctx: dict[str, str], attempts: int, delay: float
) -> None:
    """Poll until the async writes are committed and searchable."""
    if not scenario.qa:
        return
    probe = scenario.qa[0].question
    for _ in range(attempts):
        resp = client.post("/v1/retrieve", json={"query": probe, "ctx": ctx})
        if resp.json()["hits"]:
            return
        time.sleep(delay)


def run_scenario(
    client: HttpClient,
    scenario: Scenario,
    *,
    mode: str = "lite",
    agent_id: str = "assistant",
    k: int = 10,
    ready_attempts: int = 40,
    ready_delay: float = 0.25,
) -> SimResult:
    """Play a scenario end-to-end and score recall + procedural reuse."""
    ctx = _ctx(scenario, agent_id)
    steps = _play_sessions(client, scenario, ctx)
    _await_ready(client, scenario, ctx, ready_attempts, ready_delay)

    recall_hits = 0
    f1_total = 0.0
    for qa in scenario.qa:
        resp = client.post(
            "/v1/retrieve",
            json={"query": qa.question, "ctx": ctx, "opts": {"mode": mode, "k": k}},
        )
        body = resp.json()
        hit_texts = [h["text"] for h in body["hits"]]
        recall_hits += int(_recall_hit(hit_texts, qa.gold_fact))
        f1_total += _token_f1(hit_texts[0] if hit_texts else "", qa.answer)

    n = len(scenario.qa)
    return SimResult(
        scenario_id=scenario.scenario_id,
        mode=mode,
        recall_at_k=recall_hits / n if n else 0.0,
        mean_f1=f1_total / n if n else 0.0,
        n_questions=n,
        steps_recorded=steps,
    )
