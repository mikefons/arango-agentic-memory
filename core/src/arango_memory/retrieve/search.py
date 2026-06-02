"""Minimal retrieval for the Step 0 walking skeleton (DESIGN.md §9).

BM25 search over the memory view (tenant/agent scoped), then naive context
assembly under a token budget. HyDE, vector search, graph expansion, RRF/MMR
fusion, and the adaptive gate are added in later steps. Vector search is
intentionally absent here — cold start falls back to BM25 (DESIGN.md §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import tiktoken
from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..schema.collections import SEARCH_VIEW

_BM25_QUERY = f"""
FOR doc IN {SEARCH_VIEW}
  SEARCH ANALYZER(doc.text IN TOKENS(@query, "text_en"), "text_en")
     AND doc.tenant_id == @tenant_id
     AND doc.agent_id == @agent_id
  FILTER doc.invalid_at == null
  SORT BM25(doc) DESC
  LIMIT @k
  RETURN {{ text: doc.text, score: BM25(doc) }}
"""

# Cheap, model-agnostic token counter for budgeting.
_ENCODER = tiktoken.get_encoding("cl100k_base")


@dataclass
class MemoryHit:
    text: str
    score: float
    source: str = "bm25"


@dataclass
class RetrieveResult:
    context: str = ""
    hits: list[MemoryHit] = field(default_factory=list)
    tokens_injected: int = 0


def _count_tokens(text: str) -> int:
    return len(_ENCODER.encode(text))


def retrieve(
    db: StandardDatabase,
    *,
    query: str,
    tenant_id: str,
    agent_id: str,
    k: int = 10,
    max_memory_tokens: int = 1500,
) -> RetrieveResult:
    """BM25 retrieval + token-budgeted assembly."""
    bind_vars: dict[str, Any] = {
        "query": query,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "k": k,
    }
    cursor = cast(Cursor, db.aql.execute(_BM25_QUERY, bind_vars=bind_vars))
    hits = [MemoryHit(text=row["text"], score=row["score"]) for row in cursor]

    assembled: list[str] = []
    tokens = 0
    for hit in hits:
        cost = _count_tokens(hit.text)
        if tokens + cost > max_memory_tokens:
            break
        assembled.append(hit.text)
        tokens += cost

    context = "\n".join(f"- {line}" for line in assembled)
    return RetrieveResult(context=context, hits=hits, tokens_injected=tokens)
