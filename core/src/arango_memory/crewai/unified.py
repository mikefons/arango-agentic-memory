"""CrewAI ≥ 1.10 unified-memory backend (CW-1, DESIGN.md §21) — requires the `crewai` extra.

CrewAI 1.10 replaced the text-based `Storage` interface with `Memory(storage=StorageBackend)`,
whose `search()` receives only a query *embedding*. The core's retrieval is hybrid (BM25 +
vector + entity graph + rerank) and needs the query *text*, so this backend ships with a
`PairedEmbedder` that the same `Memory` must use: it embeds with the core's embedder and
remembers which text produced each vector, letting `search()` recover the text and run the
core's full `retrieve()`. A vector the embedder didn't produce (e.g. evicted from its LRU)
falls back to exact cosine search over this backend's records.

Layout: each crewai record is one core memory (text, embedding, graph — PII-redacted by the
core's store path) plus one `crewai_records` row holding crewai's own fields (id, scope,
categories, metadata, importance, timestamps, source, private) and `memory_key`. The text is
never duplicated into the side row. Each record *revision* gets its own core memory key
(record id + revision folded into the idempotency key's `turn_index`), so deleting or
rewriting a record soft-deletes exactly its memory and never collides with the core's rule
that a soft-deleted key is not re-stored.

Scores are cosine similarity in [0, 1] — what crewai's Qdrant backend reports and what its
consolidation threshold and composite score expect. The core decides *which* memories are
candidates; crewai's recall still applies its own recency/importance blend on top.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import threading
from array import array
from collections import OrderedDict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase
from crewai.memory.types import MemoryRecord, ScopeInfo

from ..embedding import Embedder, get_embedder
from ..embedding_cache import embed_batch_cached
from ..generation import Generator
from ..ingest.store import store
from ..lifecycle.decay import reset_access
from ..retrieve.search import retrieve
from ..security.forget import forget_memories

if TYPE_CHECKING:
    from crewai.memory.unified_memory import Memory

_COLLECTION = "crewai_records"
# Core candidates fetched per requested result, so scope/category filters applied after the
# core's retrieval still leave enough to fill `limit` (crewai's LanceDB backend uses 3×).
_CANDIDATE_FACTOR = 3
# Generous token budget: the backend returns hits, not assembled context, so the core's
# context budget must never be what trims the candidate list.
_NO_TOKEN_BUDGET = 1_000_000


def _vec_key(vec: Sequence[float]) -> bytes:
    return hashlib.blake2b(array("d", vec).tobytes(), digest_size=16).digest()


class PairedEmbedder:
    """crewai embedder callable (`list[str] -> list[list[float]]`) over the core's embedder,
    remembering the text behind each vector so the paired backend can recover query text."""

    def __init__(
        self, *, tenant_id: str, embedder: Embedder | None = None, capacity: int = 4096
    ) -> None:
        self.tenant_id = tenant_id
        self.embedder = embedder or get_embedder()
        self._capacity = capacity
        self._texts: OrderedDict[bytes, str] = OrderedDict()
        self._lock = threading.Lock()

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002 — crewai's name
        by_text = embed_batch_cached(self.embedder, input, tenant_id=self.tenant_id)
        vectors = [[float(x) for x in by_text[text]] for text in input]
        with self._lock:
            for text, vec in zip(input, vectors, strict=True):
                key = _vec_key(vec)
                self._texts[key] = text
                self._texts.move_to_end(key)
            while len(self._texts) > self._capacity:
                self._texts.popitem(last=False)
        return vectors

    def text_for(self, vec: Sequence[float]) -> str | None:
        with self._lock:
            return self._texts.get(_vec_key(vec))


def _iso(dt: datetime) -> str:
    """crewai timestamps are naive UTC; normalise aware ones so string order is time order."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.isoformat()


def _norm_scope(scope: str | None) -> str | None:
    """`None`/root → no filter; otherwise `/a/b` (leading slash, no trailing)."""
    if scope is None or not scope.strip("/"):
        return None
    return "/" + scope.strip("/")


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return max(0.0, min(1.0, dot / norm)) if norm else 0.0


# Rows of this (tenant, agent) whose memory is still valid — a tenant-level `forget` hides
# them without touching this collection. `@prefix` is a path prefix, not a string prefix.
_ROWS = """
FOR r IN crewai_records
  FILTER r.tenant_id == @tenant_id AND r.agent_id == @agent_id
  FILTER @prefix == null OR r.scope == @prefix OR STARTS_WITH(r.scope, CONCAT(@prefix, "/"))
  FILTER @categories == null OR LENGTH(INTERSECTION(r.categories, @categories)) > 0
  FILTER @metadata == null OR MATCHES(r.metadata, @metadata)
  FILTER @record_ids == null OR r.record_id IN @record_ids
  FILTER @memory_keys == null OR r.memory_key IN @memory_keys
  FILTER @older_than == null OR r.created_at < @older_than
  LET m = DOCUMENT(memories, r.memory_key)
  FILTER m != null AND m.invalid_at == null
"""


class ArangoMemoryBackend:
    """crewai `StorageBackend` over the core, bound to one (tenant, agent)."""

    def __init__(
        self,
        db: StandardDatabase,
        *,
        tenant_id: str,
        agent_id: str,
        embedder: PairedEmbedder,
        mode: str = "lite",
        extract: bool = True,
        generator: Generator | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.embedder = embedder
        self.mode = mode
        self.extract = extract
        self.generator = generator

    # ── helpers ────────────────────────────────────────────────────────────────────────────
    def _row_key(self, record_id: str) -> str:
        raw = f"{self.tenant_id}\x1f{self.agent_id}\x1f{record_id}"
        return hashlib.blake2b(raw.encode(), digest_size=16).hexdigest()

    def _query(
        self,
        tail: str,
        *,
        scope_prefix: str | None = None,
        categories: list[str] | None = None,
        metadata_filter: dict[str, Any] | None = None,
        record_ids: list[str] | None = None,
        memory_keys: list[str] | None = None,
        older_than: datetime | None = None,
        **extra: Any,
    ) -> list[Any]:
        bind: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "prefix": _norm_scope(scope_prefix),
            "categories": categories or None,
            "metadata": metadata_filter or None,
            "record_ids": record_ids,
            "memory_keys": memory_keys,
            "older_than": _iso(older_than) if older_than is not None else None,
            **extra,
        }
        return list(cast(Cursor, self.db.aql.execute(_ROWS + tail, bind_vars=bind)))

    @staticmethod
    def _to_record(row: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            id=row["record_id"],
            content=row["content"],
            scope=row["scope"],
            categories=row["categories"],
            metadata=row["metadata"],
            importance=row["importance"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_accessed=datetime.fromisoformat(row["last_accessed"]),
            source=row["source"],
            private=row["private"],
        )

    def _store_revision(self, record: MemoryRecord, rev: int) -> str:
        turn = int.from_bytes(
            hashlib.blake2b(f"{record.id}\x1f{rev}".encode(), digest_size=7).digest(), "big"
        )
        result = store(
            self.db,
            content=record.content,
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            turn_index=turn,
            embedder=self.embedder.embedder,
            generator=self.generator,
            mode=self.mode,
            extract=self.extract,
        )
        return result.memory_ids[0]

    def _write_row(self, record: MemoryRecord, *, memory_key: str, rev: int, digest: str) -> None:
        self.db.collection(_COLLECTION).insert(
            {
                "_key": self._row_key(record.id),
                "tenant_id": self.tenant_id,
                "agent_id": self.agent_id,
                "record_id": record.id,
                "memory_key": memory_key,
                "rev": rev,
                "content_digest": digest,
                "scope": _norm_scope(record.scope) or "/",
                "categories": list(record.categories),
                "metadata": dict(record.metadata),
                "importance": record.importance,
                "created_at": _iso(record.created_at),
                "last_accessed": _iso(record.last_accessed),
                "source": record.source,
                "private": record.private,
            },
            overwrite=True,
            silent=True,
        )

    @staticmethod
    def _digest(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    # ── writes ─────────────────────────────────────────────────────────────────────────────
    def save(self, records: list[MemoryRecord]) -> None:
        for record in records:
            self.update(record)

    def update(self, record: MemoryRecord) -> None:
        """Insert or replace by id. A content change stores a new revision and soft-deletes
        the old memory; a metadata-only change rewrites just the side row."""
        coll = self.db.collection(_COLLECTION)
        existing = cast(dict[str, Any] | None, coll.get(self._row_key(record.id)))
        digest = self._digest(record.content)
        if existing is not None and existing["content_digest"] == digest:
            memory_key, rev = existing["memory_key"], existing["rev"]
        else:
            rev = existing["rev"] + 1 if existing is not None else 0
            memory_key = self._store_revision(record, rev)
            if existing is not None:
                forget_memories(
                    self.db, tenant_id=self.tenant_id, memory_keys=[existing["memory_key"]]
                )
        self._write_row(record, memory_key=memory_key, rev=rev, digest=digest)

    def delete(
        self,
        scope_prefix: str | None = None,
        categories: list[str] | None = None,
        record_ids: list[str] | None = None,
        older_than: datetime | None = None,
        metadata_filter: dict[str, Any] | None = None,
    ) -> int:
        rows = self._query(
            "RETURN { key: r._key, memory_key: r.memory_key }",
            scope_prefix=scope_prefix,
            categories=categories,
            metadata_filter=metadata_filter,
            record_ids=record_ids,
            older_than=older_than,
        )
        if not rows:
            return 0
        forget_memories(
            self.db, tenant_id=self.tenant_id, memory_keys=[r["memory_key"] for r in rows]
        )
        self.db.collection(_COLLECTION).delete_many(
            [{"_key": r["key"]} for r in rows], silent=True
        )
        return len(rows)

    def reset(self, scope_prefix: str | None = None) -> None:
        self.delete(scope_prefix=scope_prefix)

    def touch_records(self, record_ids: list[str]) -> None:
        """crewai's recall hook: refresh the returned records' access time, and the core's
        spaced-repetition access (search itself is a read-only probe)."""
        rows = self._query(
            "UPDATE r WITH { last_accessed: @now } IN crewai_records RETURN r.memory_key",
            record_ids=record_ids,
            now=_iso(datetime.utcnow()),  # noqa: DTZ003 — crewai timestamps are naive UTC
        )
        reset_access(self.db, rows)

    # ── reads ──────────────────────────────────────────────────────────────────────────────
    def search(
        self,
        query_embedding: list[float],
        scope_prefix: str | None = None,
        categories: list[str] | None = None,
        metadata_filter: dict[str, Any] | None = None,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> list[tuple[MemoryRecord, float]]:
        filters: dict[str, Any] = {
            "scope_prefix": scope_prefix,
            "categories": categories,
            "metadata_filter": metadata_filter,
        }
        text = self.embedder.text_for(query_embedding)
        if text is not None:
            hits = retrieve(
                self.db,
                query=text,
                tenant_id=self.tenant_id,
                agent_id=self.agent_id,
                k=limit * _CANDIDATE_FACTOR,
                max_memory_tokens=_NO_TOKEN_BUDGET,
                embedder=self.embedder.embedder,
                mode=self.mode,
                generator=self.generator,
                record_access=False,
            ).hits
            keys = [h.key for h in hits]
            rows = self._query(
                "RETURN MERGE(r, { content: m.text, embedding: m.embedding })",
                memory_keys=keys,
                **filters,
            )
            order = {key: i for i, key in enumerate(keys)}
            rows.sort(key=lambda row: order[row["memory_key"]])
        else:
            rows = self._query(
                "SORT COSINE_SIMILARITY(m.embedding, @q) DESC LIMIT @limit "
                "RETURN MERGE(r, { content: m.text, embedding: m.embedding })",
                q=query_embedding,
                limit=limit,
                **filters,
            )
        out: list[tuple[MemoryRecord, float]] = []
        for row in rows:
            score = _cosine(query_embedding, row["embedding"])
            if score >= min_score:
                out.append((self._to_record(row), score))
        return out[:limit]

    def get_record(self, record_id: str) -> MemoryRecord | None:
        rows = self._query("RETURN MERGE(r, { content: m.text })", record_ids=[record_id])
        return self._to_record(rows[0]) if rows else None

    def list_records(
        self, scope_prefix: str | None = None, limit: int = 200, offset: int = 0
    ) -> list[MemoryRecord]:
        rows = self._query(
            "SORT r.created_at DESC LIMIT @offset, @limit RETURN MERGE(r, { content: m.text })",
            scope_prefix=scope_prefix,
            offset=offset,
            limit=limit,
        )
        return [self._to_record(row) for row in rows]

    def get_scope_info(self, scope: str) -> ScopeInfo:
        path = _norm_scope(scope) or "/"
        rows = self._query(
            "RETURN { scope: r.scope, categories: r.categories, created_at: r.created_at }",
            scope_prefix=scope,
        )
        child_prefix = path.rstrip("/") + "/"
        children = {
            child_prefix + row["scope"][len(child_prefix):].split("/", 1)[0]
            for row in rows
            if row["scope"].startswith(child_prefix) and len(row["scope"]) > len(child_prefix)
        }
        created = sorted(row["created_at"] for row in rows)
        return ScopeInfo(
            path=path,
            record_count=len(rows),
            categories=sorted({c for row in rows for c in row["categories"]}),
            oldest_record=datetime.fromisoformat(created[0]) if created else None,
            newest_record=datetime.fromisoformat(created[-1]) if created else None,
            child_scopes=sorted(children),
        )

    def list_scopes(self, parent: str = "/") -> list[str]:
        return list(self.get_scope_info(parent).child_scopes)

    def list_categories(self, scope_prefix: str | None = None) -> dict[str, int]:
        rows = self._query(
            "FOR c IN r.categories COLLECT category = c WITH COUNT INTO n "
            "RETURN { category, n }",
            scope_prefix=scope_prefix,
        )
        return {row["category"]: row["n"] for row in rows}

    def count(self, scope_prefix: str | None = None) -> int:
        rows = self._query("COLLECT WITH COUNT INTO n RETURN n", scope_prefix=scope_prefix)
        return int(rows[0]) if rows else 0

    # ── async (crewai's async API; the core client is sync) ───────────────────────────────
    async def asave(self, records: list[MemoryRecord]) -> None:
        await asyncio.to_thread(self.save, records)

    async def asearch(
        self,
        query_embedding: list[float],
        scope_prefix: str | None = None,
        categories: list[str] | None = None,
        metadata_filter: dict[str, Any] | None = None,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> list[tuple[MemoryRecord, float]]:
        return await asyncio.to_thread(
            self.search, query_embedding, scope_prefix, categories, metadata_filter, limit,
            min_score,
        )

    async def adelete(
        self,
        scope_prefix: str | None = None,
        categories: list[str] | None = None,
        record_ids: list[str] | None = None,
        older_than: datetime | None = None,
        metadata_filter: dict[str, Any] | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self.delete, scope_prefix, categories, record_ids, older_than, metadata_filter
        )


def arango_crewai_memory(
    db: StandardDatabase,
    *,
    tenant_id: str,
    agent_id: str,
    embedder: Embedder | None = None,
    mode: str = "lite",
    extract: bool = True,
    generator: Generator | None = None,
    **memory_kwargs: Any,
) -> Memory:
    """A crewai `Memory` backed by the core: pass it as `Crew(memory=...)`.

    Wires the backend and its paired embedder together (both must be the same instance for
    hybrid search). `memory_kwargs` go to `Memory` (e.g. `llm=`, `root_scope=`).
    """
    from crewai.memory.unified_memory import Memory

    paired = PairedEmbedder(tenant_id=tenant_id, embedder=embedder)
    backend = ArangoMemoryBackend(
        db, tenant_id=tenant_id, agent_id=agent_id, embedder=paired, mode=mode,
        extract=extract, generator=generator,
    )
    return Memory(storage=backend, embedder=paired, **memory_kwargs)
