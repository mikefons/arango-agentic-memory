#!/usr/bin/env python
"""A shared brain across sessions — the arango-memory MCP server, demonstrated.

This drives the **real MCP server** (`python -m arango_memory.mcp`) over stdio, exactly the
way Claude Desktop / Cursor / Windsurf do, and proves the one thing MCP uniquely shows: an
assistant's memory is a *service*, not in-process state. So the demo runs as **two separate
MCP sessions** — each spawns its own server subprocess — that share nothing but the memory
backend:

  • Session 1 (a fresh server process) *stores* three facts about the user.
  • Session 2 (a **different** server process, empty context) *recalls* them.

If session 2 answers correctly, the memory clearly lived in the backend the whole time — not in
any window or process. The recall queries are natural language; retrieval is semantic, so they
match by meaning, not keywords (needs real embeddings — see the README). The store→recall
sequence is transport-agnostic (`run_store` / `run_recall` take a `call` function), so the same
logic is smoke-tested keyless in `core/tests/test_mcp.py`.

Prereq: a running core (`docker compose up` from the repo root brings up ArangoDB + the core
API). Then: `python scenario.py`. Point elsewhere with `ARANGO_MEMORY_CORE_URL`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# One MCP `call_tool` for the scenario: name + kwargs → the tool's JSON result.
Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

AGENT_ID = "assistant"

# Session 1 teaches the assistant three facts, each aimed at a different capability.
FACTS: list[str] = [
    "I'm allergic to shellfish.",
    "My sister Mira is visiting next week.",
    "I moved from Munich to Berlin in March.",
]

# Session 2 asks — in natural language that shares almost no keywords with the facts, so only
# semantic retrieval can connect them. Each query names the memory it should surface (a keyword
# the retrieved context must contain) and the capability it exercises. We check the retrieved
# *set*, not the top-1 rank: `search` hands the host all the relevant context and the host's
# model picks — so "did the right memory come back?" is the honest question.
RECALLS: list[tuple[str, str, str]] = [
    ("Is there anything I can't eat?", "shellfish",
     "persistence — it never saw session 1's context"),
    ("Remind me who Mira is?", "Mira",
     "entity graph — resolves the person across mentions"),
    ("Where do I live now?", "Berlin",
     "supersession — Berlin is newer than the stored Munich"),
]


# Ingestion runs real entity extraction + embedding per turn, so give the read-your-writes
# barrier room to drain all three before session 2 reads (the 5s default is for fast paths).
_FLUSH_TIMEOUT_MS = 30_000


async def run_store(call: Call, *, tenant: str) -> bool:
    """Session 1: commit the facts, then flush so they're guaranteed retrievable (read-your-
    writes, MA-1) before the second session — no sleeps, no races. Returns True if the queue
    fully drained (a `timeout` means ingestion is still catching up)."""
    for fact in FACTS:
        await call("store", {"content": fact, "tenant_id": tenant, "agent_id": AGENT_ID})
    flushed = await call(
        "flush", {"tenant_id": tenant, "agent_id": AGENT_ID, "timeout_ms": _FLUSH_TIMEOUT_MS}
    )
    return flushed.get("status") == "flushed"


async def run_recall(call: Call, *, tenant: str) -> list[dict[str, Any]]:
    """Session 2: run each recall query, returning the raw retrieval results (context + hits)."""
    out: list[dict[str, Any]] = []
    for query, _expect, _proves in RECALLS:
        out.append(await call("search", {"query": query, "tenant_id": tenant,
                                         "agent_id": AGENT_ID}))
    return out


def _surfaced(result: dict[str, Any], expect: str) -> str | None:
    """The retrieved memory containing `expect` (case-insensitive), if the query surfaced it.
    Checks the whole hit set, not just the top rank — `search` hands the host all the context."""
    for hit in result.get("hits") or []:
        text = str(hit.get("text", ""))
        if expect.lower() in text.lower():
            return text.strip()
    return None


# ── real MCP stdio transport ──────────────────────────────────────────────────────────────
def _server_params() -> StdioServerParameters:
    """Launch `python -m arango_memory.mcp` as a stdio MCP server — the same command a
    Claude Desktop / Cursor config uses. It inherits the environment, so
    ARANGO_MEMORY_CORE_URL / ARANGO_MEMORY_API_KEY point it at your core."""
    return StdioServerParameters(command=sys.executable, args=["-m", "arango_memory.mcp"],
                                 env=dict(os.environ))


def _unwrap(result: Any) -> dict[str, Any]:
    """A FastMCP tool result → the tool's JSON payload. Prefers structured content; falls back
    to the first text block."""
    data = getattr(result, "structuredContent", None)
    if isinstance(data, dict):
        return data.get("result", data) if set(data) == {"result"} else data
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if text:
            return cast(dict[str, Any], json.loads(text))
    return {}


class _McpSession:
    """A live MCP session that spawns its own server subprocess.

    Use as `async with _McpSession() as call:` — `call(name, args)` invokes a tool."""

    async def __aenter__(self) -> Call:
        self._stdio = stdio_client(_server_params())
        self._read, self._write = await self._stdio.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()

        async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return _unwrap(await self._session.call_tool(name, args))

        return call

    async def __aexit__(self, *exc: Any) -> None:
        await self._session.__aexit__(*exc)
        await self._stdio.__aexit__(*exc)


# ── presentation ──────────────────────────────────────────────────────────────────────────
def _print_header(text: str) -> None:
    print(f"\n\033[1m{text}\033[0m")


async def _preflight() -> None:
    """Fail fast with a friendly message if the core isn't up (the server subprocess needs it)."""
    url = os.environ.get("ARANGO_MEMORY_CORE_URL", "http://localhost:8080")
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            (await http.get(f"{url}/health")).raise_for_status()
    except Exception as exc:  # noqa: BLE001 — a preflight failure should read as guidance, not a trace
        print(f"✗ core not reachable at {url} ({type(exc).__name__}).\n"
              f"  Start it first — from the repo root:  docker compose up\n"
              f"  or point ARANGO_MEMORY_CORE_URL at a running core.", file=sys.stderr)
        raise SystemExit(1) from None


async def main() -> int:
    await _preflight()
    tenant = f"mcp-demo-{uuid.uuid4().hex[:8]}"  # isolated per run
    print(f"shared brain: MCP server → core → ArangoDB   ·   tenant: {tenant}")

    _print_header("── Session 1 · a fresh MCP server process — STORE ──")
    async with _McpSession() as call:
        drained = await run_store(call, tenant=tenant)
    for fact in FACTS:
        print(f"  → store  {fact!r}")
    print("  ✓ committed + flushed" if drained
          else "  ⚠ flush timed out — ingestion still catching up; recall may be partial")

    _print_header("── Session 2 · a DIFFERENT server process, empty context — RECALL ──")
    async with _McpSession() as call:
        results = await run_recall(call, tenant=tenant)
    ok = 0
    for (query, expect, proves), result in zip(RECALLS, results, strict=True):
        surfaced = _surfaced(result, expect)
        ok += surfaced is not None
        mark = "✓" if surfaced else "✗"
        print(f'  » "{query}"')
        print(f"     {mark} recalled: {surfaced or '(the right memory did not surface)'}")
        print(f"       └ {proves}")

    _print_header(f"✓ session 2 recalled {ok}/{len(RECALLS)} with zero shared context")
    print("  Two separate server processes; the memory lived in the backend the whole time.\n"
          "  Tip: `list_entities` / `stats` on this tenant to see the entity graph it built.")
    return 0 if ok == len(RECALLS) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
