"""Fact-augmented keys (FK-1, DESIGN.md §23).

A chat turn is indexed under what it is mostly about, not under every fact it carries: "I need
throw pillows — I got a West Elm coffee table three weeks ago" is a pillow request to BM25 and to
an embedding alike, so a later "what furniture did I buy?" ranks it far down. This Dream State
pass has the LLM state the facts each memory carries as short standalone sentences and stores
them, with one embedding per fact, on the memory. Retrieval matches the query against them as two
extra arms (`retrieve_fact_keys`) and the memory itself stays the value that is returned.

Incremental: each processed memory is stamped `facts_extracted_at`, so a later pass only reads
new memories.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..embedding import Embedder, get_embedder
from ..embedding_cache import embed_batch_cached
from ..generation import Generator, get_generator
from ..models import utcnow_iso
from ..telemetry import metrics

_FACTS_SYSTEM = (
    "You index a message so it can be found later by what it says. State each fact the message "
    "gives about the speaker or their life as one short, standalone sentence starting with "
    "'The user' (or the person named): what they did, bought, own, plan, prefer or experienced, "
    "with the names, numbers, places and amounts exactly as written. If a message date is given, "
    "turn relative times (yesterday, last Saturday, three weeks ago) into dates. Include a fact "
    "that is mentioned only in passing. Skip requests, questions and general knowledge. One fact "
    "per line, no numbering. If the message states no fact, reply NONE."
)
_MAX_FACT_CHARS = 300
# A pasted document must not become an unbounded prompt.
_MAX_CHARS = 4000

_PENDING = """
FOR m IN memories
  FILTER m.tenant_id == @tenant_id AND m.invalid_at == null AND m.type != "working"
     AND m.facts_extracted_at == null
     AND (@prefix == "" OR STARTS_WITH(m.text, @prefix))
  RETURN { key: m._key, text: m.text, event_time: m.event_time }
"""


@dataclass
class FactKeysResult:
    processed: int = 0
    with_facts: int = 0
    facts: int = 0


def parse_facts(reply: str, limit: int) -> list[str]:
    """Fact lines from a reply (bullets stripped, `NONE` → []), capped."""
    out: list[str] = []
    for line in reply.splitlines():
        fact = line.strip().lstrip("-•*0123456789.) \t").strip()
        if not fact or fact.upper().rstrip(".") == "NONE":
            continue
        out.append(fact[:_MAX_FACT_CHARS])
        if len(out) >= limit:
            break
    return out


def _prompt(text: str, event_time: str | None) -> str:
    body = text if len(text) <= _MAX_CHARS else text[:_MAX_CHARS] + "…"
    return f"Message date: {event_time}\n\n{body}" if event_time else body


def run_fact_keys(
    db: StandardDatabase,
    *,
    tenant_id: str,
    generator: Generator | None = None,
    embedder: Embedder | None = None,
) -> FactKeysResult:
    """Extract and store the facts of every memory not yet processed. Returns counts."""
    gen = generator or get_generator()
    emb = embedder or get_embedder()
    rows = list(cast(Cursor, db.aql.execute(
        _PENDING, bind_vars={"tenant_id": tenant_id, "prefix": settings.fact_keys_prefix})))
    limit = settings.fact_keys_max_per_memory

    def extract(row: dict[str, Any]) -> list[str]:
        reply = gen.complete(_prompt(row["text"], row.get("event_time")),
                             system=_FACTS_SYSTEM, max_tokens=400)
        return parse_facts(reply, limit)

    with ThreadPoolExecutor(max_workers=settings.fact_keys_concurrency) as pool:
        extracted = list(pool.map(extract, rows))

    wanted: Sequence[str] = sorted({f for facts in extracted for f in facts})
    vectors = embed_batch_cached(emb, wanted, tenant_id=tenant_id) if wanted else {}
    memories = db.collection("memories")
    now = utcnow_iso()
    result = FactKeysResult()
    for row, facts in zip(rows, extracted, strict=True):
        doc: dict[str, Any] = {"_key": row["key"], "facts_extracted_at": now}
        if facts:
            doc["facts"] = facts
            doc["fact_embeddings"] = [vectors[f] for f in facts]
            result.with_facts += 1
            result.facts += len(facts)
        memories.update(doc)
        result.processed += 1
    metrics.emit("fact_keys", processed=result.processed, with_facts=result.with_facts,
                 facts=result.facts)
    return result
