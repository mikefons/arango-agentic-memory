"""Task briefing — `POST /v1/prime` (MA-3, §14/§19).

The handoff verb: given a task + ctx, assemble one budgeted briefing so the next
agent starts warm instead of hand-crafting a retrieval query. Pure composition of
existing pipelines — episodic/semantic **history** (hybrid `retrieve`), **key
entities** (traversed from the retrieved memories' mentions), and **prior tool runs**
(most-reused procedural steps across the read agents). Spans `read_agent_ids` (MA-2)
and never mints anything — a read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import tiktoken
from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..embedding import Embedder
from ..generation import Generator
from .enrich import QueryCache
from .search import MemoryHit, retrieve

_ENCODER = tiktoken.get_encoding("cl100k_base")

# Section shares of max_memory_tokens: history / entities / tool-runs.
_SECTION_FRACTIONS = {"episodic": 0.5, "semantic": 0.25, "procedural": 0.25}
_ENTITY_LIMIT = 20
_STEP_LIMIT = 10

# Top entities mentioned by the retrieved memories, deduped, ranked by salience
# (belief/centrality) then mention_count. Read-scoped by construction: the seeds are
# the read_agent_ids-scoped hits. Embeddings never projected (§17).
_ENTITIES_FROM_MEMORIES = """
FOR mem_key IN @keys
  FOR e IN 1..1 OUTBOUND CONCAT('memories/', mem_key) mentions
    FILTER e.tenant_id == @tenant_id AND e.invalid_at == null
    COLLECT id = e._key INTO grp = e
    LET rep = grp[0]
    LET salience = MAX([NOT_NULL(rep.belief, 0), NOT_NULL(rep.centrality, 0)])
    SORT salience DESC, NOT_NULL(rep.mention_count, 0) DESC
    LIMIT @limit
    RETURN { name: rep.name, label: rep.label, summary: rep.summary,
             belief: rep.belief, centrality: rep.centrality,
             community: rep.community, mention_count: rep.mention_count }
"""

# Most-reused tool runs across the read agents (procedural memory, §11).
_STEPS_FOR_AGENTS = """
FOR s IN steps
  FILTER s.tenant_id == @tenant_id AND s.agent_id IN @agent_ids
  SORT s.use_count DESC
  LIMIT @limit
  RETURN { tool_name: s.tool_name, outcome: s.outcome, use_count: s.use_count,
           arguments: s.arguments, pattern_summary: s.pattern_summary, agent_id: s.agent_id }
"""


@dataclass
class Include:
    episodic: bool = True   # the retrieved history section
    semantic: bool = True   # the key-entities section
    procedural: bool = True  # the prior-tool-runs section


@dataclass
class PrimeResult:
    context: str = ""
    hits: list[MemoryHit] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    tokens_injected: int = 0


def _count(text: str) -> int:
    return len(_ENCODER.encode(text))


def _pack(items: list[Any], render: Any, budget: int) -> tuple[list[Any], list[str]]:
    """Greedily keep top items whose rendered lines fit `budget` tokens (items are
    pre-sorted best-first, so this truncates the lowest-ranked)."""
    kept: list[Any] = []
    lines: list[str] = []
    used = 0
    for item in items:
        line = render(item)
        cost = _count(line)
        if used + cost > budget:
            break
        kept.append(item)
        lines.append(line)
        used += cost
    return kept, lines


def _render_hit(h: MemoryHit) -> str:
    return f"- {h.text}"


def _render_entity(e: dict[str, Any]) -> str:
    label = f" ({e['label']})" if e.get("label") else ""
    summary = f" — {e['summary']}" if e.get("summary") else ""
    return f"- {e.get('name', '?')}{label}{summary}"


def _render_step(s: dict[str, Any]) -> str:
    return f"- {s.get('tool_name', '?')} → {s.get('outcome', '?')} (used {s.get('use_count', 0)}x)"


def _assemble(
    hits: list[MemoryHit],
    entities: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    *,
    max_tokens: int,
    include: Include,
) -> PrimeResult:
    """Pure assembly: pack each enabled section under its budget share, join into a
    markdown briefing. No DB — unit-testable."""
    sections: list[str] = []
    result = PrimeResult()

    if include.episodic:
        kept, lines = _pack(hits, _render_hit, int(max_tokens * _SECTION_FRACTIONS["episodic"]))
        result.hits = kept
        if lines:
            sections.append("## Relevant history\n" + "\n".join(lines))
    if include.semantic:
        kept, lines = _pack(entities, _render_entity,
                            int(max_tokens * _SECTION_FRACTIONS["semantic"]))
        result.entities = kept
        if lines:
            sections.append("## Key entities\n" + "\n".join(lines))
    if include.procedural:
        kept, lines = _pack(steps, _render_step,
                            int(max_tokens * _SECTION_FRACTIONS["procedural"]))
        result.steps = kept
        if lines:
            sections.append("## Prior tool runs\n" + "\n".join(lines))

    result.context = "\n\n".join(sections)
    result.tokens_injected = _count(result.context)
    return result


def prime(
    db: StandardDatabase,
    *,
    task: str,
    tenant_id: str,
    agent_id: str,
    read_agent_ids: list[str] | None = None,
    mode: str = "lite",
    k: int = 10,
    max_memory_tokens: int = 1500,
    include: Include | None = None,
    embedder: Embedder | None = None,
    generator: Generator | None = None,
    cache: QueryCache | None = None,
) -> PrimeResult:
    """Assemble a task briefing (history + entities + tool runs) under one budget."""
    inc = include or Include()
    agent_ids = read_agent_ids or [agent_id]

    hits: list[MemoryHit] = []
    entities: list[dict[str, Any]] = []
    # History + entities both derive from the retrieved memories, so run retrieval when
    # either section is on (entities need the seeds even if the history section is off).
    if inc.episodic or inc.semantic:
        result = retrieve(
            db, query=task, tenant_id=tenant_id, agent_id=agent_id,
            read_agent_ids=read_agent_ids, k=k, max_memory_tokens=max_memory_tokens,
            embedder=embedder, mode=mode, generator=generator, cache=cache,
        )
        hits = result.hits
        if inc.semantic:
            seed_keys = [h.key for h in hits if h.key]
            if seed_keys:
                ent_bind: dict[str, Any] = {
                    "keys": seed_keys, "tenant_id": tenant_id, "limit": _ENTITY_LIMIT,
                }
                entities = list(cast(Cursor, db.aql.execute(
                    _ENTITIES_FROM_MEMORIES, bind_vars=ent_bind)))

    steps: list[dict[str, Any]] = []
    if inc.procedural:
        step_bind: dict[str, Any] = {
            "tenant_id": tenant_id, "agent_ids": agent_ids, "limit": _STEP_LIMIT,
        }
        steps = list(cast(Cursor, db.aql.execute(_STEPS_FOR_AGENTS, bind_vars=step_bind)))

    return _assemble(hits, entities, steps, max_tokens=max_memory_tokens, include=inc)
