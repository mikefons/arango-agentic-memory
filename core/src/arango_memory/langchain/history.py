"""`ArangoChatMessageHistory` — session transcript over the core (DESIGN.md §21).

Persists each message through the durable core `store()` (so history survives
restarts and benefits from idempotency + the write worker) and reconstructs the
session transcript on read. The originating role is carried on the episode's
`message_type` so `messages` can rebuild the right `BaseMessage` subclass.

`clear()` soft-deletes the session's episodes (sets `invalid_at`); it never hard
-deletes, so the WORM guarantee on episodes (§17) is preserved — only a tombstone
is added, mirroring `security.forget`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from arango.cursor import Cursor
from arango.database import StandardDatabase
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from ..ingest.store import store
from ..models import utcnow_iso

_LOAD = """
FOR e IN episodes
  FILTER e.tenant_id == @tenant_id AND e.agent_id == @agent_id
     AND e.session_id == @session_id AND e.invalid_at == null
  SORT e.ingested_at ASC, e._key ASC
  RETURN { content: e.content, message_type: e.message_type }
"""

_CLEAR = """
FOR e IN episodes
  FILTER e.tenant_id == @tenant_id AND e.agent_id == @agent_id
     AND e.session_id == @session_id AND e.invalid_at == null
  UPDATE e WITH { invalid_at: @now } IN episodes
"""


def _to_message(content: str, message_type: str | None) -> BaseMessage:
    if message_type == "human":
        return HumanMessage(content=content)
    if message_type == "ai":
        return AIMessage(content=content)
    if message_type == "system":
        return SystemMessage(content=content)
    if message_type == "tool":
        return ToolMessage(content=content, tool_call_id="")
    return ChatMessage(content=content, role=message_type or "user")


class ArangoChatMessageHistory(BaseChatMessageHistory):
    """A LangChain chat history backed by the ArangoDB memory core."""

    def __init__(
        self,
        db: StandardDatabase,
        *,
        tenant_id: str,
        agent_id: str,
        session_id: str,
        mode: str = "lite",
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.session_id = session_id
        self.mode = mode
        self._turn = self._existing_count()

    def _existing_count(self) -> int:
        cursor = cast(
            Cursor,
            self.db.aql.execute(
                "FOR e IN episodes FILTER e.tenant_id == @tenant_id "
                "AND e.agent_id == @agent_id AND e.session_id == @session_id "
                "COLLECT WITH COUNT INTO n RETURN n",
                bind_vars={
                    "tenant_id": self.tenant_id,
                    "agent_id": self.agent_id,
                    "session_id": self.session_id,
                },
            ),
        )
        return int(next(iter(cursor), 0))

    @property
    def messages(self) -> list[BaseMessage]:  # type: ignore[override]
        cursor = cast(
            Cursor,
            self.db.aql.execute(
                _LOAD,
                bind_vars={
                    "tenant_id": self.tenant_id,
                    "agent_id": self.agent_id,
                    "session_id": self.session_id,
                },
            ),
        )
        return [_to_message(row["content"], row.get("message_type")) for row in cursor]

    def add_message(self, message: BaseMessage) -> None:
        content = message.content if isinstance(message.content, str) else str(message.content)
        if not content:
            return
        store(
            self.db,
            content=content,
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            turn_index=self._turn,
            mode=self.mode,
            message_type=message.type,
        )
        self._turn += 1

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        for message in messages:
            self.add_message(message)

    def clear(self) -> None:
        self.db.aql.execute(
            _CLEAR,
            bind_vars={
                "tenant_id": self.tenant_id,
                "agent_id": self.agent_id,
                "session_id": self.session_id,
                "now": utcnow_iso(),
            },
        )
