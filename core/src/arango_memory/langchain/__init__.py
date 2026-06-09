"""LangChain / LangGraph adapter (DESIGN.md §21) — in-process, no HTTP hop.

Unlike the Vercel adapter (a thin TS client over the HTTP boundary), the
LangChain adapter is Python running in the same process as the core, so it
calls the core functions (`store`/`retrieve`/`record_step`) directly:

- `ArangoMemoryRetriever` — a `BaseRetriever` that injects relevant memory as
  `Document`s (the modern "retrieval injects context" primitive).
- `ArangoChatMessageHistory` — a `BaseChatMessageHistory` that persists a
  session transcript via the durable core and reconstructs it on read.
- `ArangoMemoryNode` — recall/remember nodes for a LangGraph `StateGraph`
  (retrieve+inject, then store the turn and capture completed tool calls).

All projections exclude embeddings (§17). Requires the `langchain` extra:
`pip install arango-memory[langchain]`.
"""

from __future__ import annotations

from .graph import ArangoMemoryNode
from .history import ArangoChatMessageHistory
from .retriever import ArangoMemoryRetriever

__all__ = [
    "ArangoChatMessageHistory",
    "ArangoMemoryNode",
    "ArangoMemoryRetriever",
]
