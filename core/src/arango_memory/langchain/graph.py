"""`ArangoMemoryNode` — recall/remember nodes for LangGraph (DESIGN.md §21).

Two graph nodes that mirror the Vercel adapter's middleware (§20):

- `recall(state)`  — retrieve relevant memory for the latest human turn and
  inject it as a single `SystemMessage` (a stale memory block is replaced, not
  stacked).
- `remember(state)` — durably store the latest human/AI turns and capture
  completed tool calls as procedural memory, pairing each AI `tool_call` with
  its `ToolMessage` result and chaining them via `prev_step_key` (§11).

Both degrade quietly: the core's `retrieve()` already swallows faults, and
procedural writes are best-effort so a memory fault never breaks the graph.
A node is just a callable, so this module depends only on `langchain_core` —
the `StateGraph`/`MessagesState` wiring lives in the caller's graph.
"""

from __future__ import annotations

from typing import Any

from arango.database import StandardDatabase
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from ..embedding import Embedder
from ..generation import Generator
from ..ingest.procedural import record_step
from ..ingest.store import store
from ..retrieve.search import retrieve

_MEMORY_TAG = "[MEMORY CONTEXT]"


def _last_human_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if message.type == "human":
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


class ArangoMemoryNode:
    """Recall + remember graph nodes bound to a tenant/agent and the core."""

    def __init__(
        self,
        db: StandardDatabase,
        *,
        tenant_id: str,
        agent_id: str,
        session_id: str | None = None,
        mode: str = "lite",
        k: int = 10,
        max_memory_tokens: int = 1500,
        embedder: Embedder | None = None,
        generator: Generator | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.session_id = session_id
        self.mode = mode
        self.k = k
        self.max_memory_tokens = max_memory_tokens
        self.embedder = embedder
        self.generator = generator
        self._seen_tools: set[str] = set()
        self._last_step_key: str | None = None

    # ── recall: retrieve + inject ─────────────────────────────
    def recall(self, state: dict[str, Any]) -> dict[str, Any]:
        messages: list[BaseMessage] = state.get("messages", [])
        query = _last_human_text(messages)
        if not query:
            return {}
        result = retrieve(
            self.db,
            query=query,
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            k=self.k,
            max_memory_tokens=self.max_memory_tokens,
            mode=self.mode,
            embedder=self.embedder,
            generator=self.generator,
        )
        if not result.context:
            return {}
        block = SystemMessage(content=f"{_MEMORY_TAG}\n{result.context}")
        return {"messages": [block]}

    # ── remember: store turns + capture tool steps ────────────
    def remember(self, state: dict[str, Any]) -> dict[str, Any]:
        messages: list[BaseMessage] = state.get("messages", [])
        for message in messages:
            if _is_memory_block(message):
                continue
            if message.type in ("human", "ai"):
                content = message.content
                text = content if isinstance(content, str) else str(content)
                if text:
                    store(
                        self.db,
                        content=text,
                        tenant_id=self.tenant_id,
                        agent_id=self.agent_id,
                        session_id=self.session_id,
                        mode=self.mode,
                        message_type=message.type,
                        embedder=self.embedder,
                        generator=self.generator,
                    )
        self._capture_tools(messages)
        return {}

    def _capture_tools(self, messages: list[BaseMessage]) -> None:
        calls: dict[str, dict[str, Any]] = {}
        for message in messages:
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    cid = call.get("id")
                    if cid:
                        calls[cid] = {"name": call["name"], "args": call.get("args", {})}
            elif isinstance(message, ToolMessage):
                call_id = message.tool_call_id
                if call_id in self._seen_tools:
                    continue
                self._seen_tools.add(call_id)
                info = calls.get(call_id, {})
                outcome = "failure" if getattr(message, "status", None) == "error" else "success"
                self._last_step_key = record_step(
                    self.db,
                    tool_name=str(info.get("name", "unknown")),
                    arguments=dict(info.get("args", {})),
                    outcome=outcome,
                    tenant_id=self.tenant_id,
                    agent_id=self.agent_id,
                    prev_step_key=self._last_step_key,
                )


def _is_memory_block(message: BaseMessage) -> bool:
    return (
        message.type == "system"
        and isinstance(message.content, str)
        and message.content.startswith(_MEMORY_TAG)
    )
