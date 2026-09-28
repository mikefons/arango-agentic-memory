"""CrewAI ≥ 1.10 unified-memory backend (CW-1) against a REAL crewai install.

Skipped without the `crewai` extra (the default CI job); the `crewai` CI job installs it, so an
upstream change to `StorageBackend` / `Memory` fails here instead of slipping past a stub (the
legacy shim broke silently that way). Fake providers keep it keyless; `remember()` passes
scope + categories + importance explicitly, which is crewai's zero-LLM save path.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from arango.database import StandardDatabase

crewai = pytest.importorskip("crewai")

from crewai.memory.storage.backend import StorageBackend  # noqa: E402
from crewai.memory.types import MemoryRecord  # noqa: E402
from crewai.memory.unified_memory import Memory  # noqa: E402

from arango_memory.crewai import (  # noqa: E402
    ArangoMemoryBackend,
    PairedEmbedder,
    arango_crewai_memory,
)
from arango_memory.retrieve.search import force_view_sync  # noqa: E402

TENANT = "cw1"


def _backend(db: StandardDatabase, tenant_id: str = TENANT) -> ArangoMemoryBackend:
    paired = PairedEmbedder(tenant_id=tenant_id)
    return ArangoMemoryBackend(db, tenant_id=tenant_id, agent_id="crew", embedder=paired)


def _record(content: str, **fields: object) -> MemoryRecord:
    return MemoryRecord(content=content, **fields)  # type: ignore[arg-type]


def _save(backend: ArangoMemoryBackend, *records: MemoryRecord) -> None:
    backend.save(list(records))
    force_view_sync(backend.db, backend.tenant_id)


def test_backend_satisfies_the_real_storage_protocol(db: StandardDatabase) -> None:
    assert isinstance(_backend(db), StorageBackend)


def test_memory_remember_and_recall_end_to_end(db: StandardDatabase) -> None:
    memory = arango_crewai_memory(db, tenant_id=TENANT, agent_id="crew")
    assert isinstance(memory, Memory)
    saved = memory.remember(
        "The Q3 launch moved to Friday because the payments audit slipped.",
        scope="/crew/research",
        categories=["decisions"],
        importance=0.8,
    )
    assert saved is not None
    force_view_sync(db, TENANT)

    matches = memory.recall("when is the launch?", depth="shallow")
    assert [m.record.id for m in matches] == [saved.id]
    record = matches[0].record
    assert record.scope == "/crew/research" and record.categories == ["decisions"]
    assert record.importance == 0.8


def test_search_recovers_query_text_for_hybrid_retrieval(db: StandardDatabase) -> None:
    # FakeEmbedder vectors are hash noise, so only the core's BM25 arm can find this by
    # keyword — proving search() ran the core's text retrieval, not a pure vector lookup.
    backend = _backend(db)
    _save(backend, _record("zebra migration notes"), _record("unrelated grocery list"))
    (query_vec,) = backend.embedder(["zebra"])
    hits = backend.search(query_vec, limit=1)
    assert [r.content for r, _ in hits] == ["zebra migration notes"]
    assert 0.0 <= hits[0][1] <= 1.0


def test_unknown_vector_falls_back_to_cosine_search(db: StandardDatabase) -> None:
    backend = _backend(db)
    _save(backend, _record("alpha"), _record("beta"))
    # A vector the paired embedder never produced: the stored memory's own embedding.
    target = next(r for r in backend.list_records() if r.content == "beta")
    mem_key = db.collection("crewai_records").get(backend._row_key(target.id))["memory_key"]
    vec = db.collection("memories").get(mem_key)["embedding"]
    backend.embedder._texts.clear()  # forget every text → no recoverable query text
    assert backend.embedder.text_for(vec) is None
    hits = backend.search(vec, limit=1)
    assert hits[0][0].content == "beta" and hits[0][1] == pytest.approx(1.0)


def test_filters_scope_categories_metadata(db: StandardDatabase) -> None:
    backend = _backend(db)
    _save(
        backend,
        _record("roadmap review", scope="/crew/a", categories=["plan"], metadata={"k": 1}),
        _record("roadmap review", scope="/crew/ab", categories=["plan"], metadata={"k": 1}),
        _record("roadmap review", scope="/crew/a/x", categories=["risk"], metadata={"k": 2}),
    )
    (vec,) = backend.embedder(["roadmap review"])
    # Path prefix, not string prefix: /crew/a covers /crew/a/x but not /crew/ab.
    assert {r.scope for r, _ in backend.search(vec, scope_prefix="/crew/a")} == {
        "/crew/a", "/crew/a/x"}
    assert {r.scope for r, _ in backend.search(vec, categories=["risk"])} == {"/crew/a/x"}
    assert {r.scope for r, _ in backend.search(vec, metadata_filter={"k": 1})} == {
        "/crew/a", "/crew/ab"}


def test_update_content_writes_a_new_revision_and_retires_the_old(db: StandardDatabase) -> None:
    backend = _backend(db)
    rec = _record("launch is Friday", scope="/s")
    _save(backend, rec)
    rows = db.collection("crewai_records")
    old_key = rows.get(backend._row_key(rec.id))["memory_key"]

    backend.update(rec.model_copy(update={"importance": 0.9}))  # metadata-only: same memory
    assert rows.get(backend._row_key(rec.id))["memory_key"] == old_key

    backend.update(rec.model_copy(update={"content": "launch is Monday"}))
    new_key = rows.get(backend._row_key(rec.id))["memory_key"]
    assert new_key != old_key
    assert db.collection("memories").get(old_key)["invalid_at"] is not None
    got = backend.get_record(rec.id)
    assert got is not None and got.content == "launch is Monday"

    # Rewriting back to the original text lands again (a fresh revision key, not the retired one).
    backend.update(rec.model_copy(update={"content": "launch is Friday"}))
    got = backend.get_record(rec.id)
    assert got is not None and got.content == "launch is Friday"


def test_delete_by_ids_scope_and_age(db: StandardDatabase) -> None:
    backend = _backend(db)
    old = datetime.utcnow() - timedelta(days=10)  # noqa: DTZ003 — crewai uses naive UTC
    a, b, c = (
        _record("a", scope="/x"),
        _record("b", scope="/x/y"),
        _record("c", scope="/z", created_at=old),
    )
    _save(backend, a, b, c)
    assert backend.delete(older_than=datetime.utcnow() - timedelta(days=1)) == 1  # noqa: DTZ003
    assert backend.delete(record_ids=[a.id]) == 1
    assert backend.get_record(a.id) is None
    assert backend.count() == 1
    backend.reset(scope_prefix="/x")
    assert backend.count() == 0


def test_scope_introspection(db: StandardDatabase) -> None:
    backend = _backend(db)
    _save(
        backend,
        _record("1", scope="/crew/a", categories=["p"]),
        _record("2", scope="/crew/a/deep", categories=["p", "q"]),
        _record("3", scope="/crew/b", categories=["q"]),
    )
    assert backend.list_scopes("/") == ["/crew"]
    assert backend.list_scopes("/crew") == ["/crew/a", "/crew/b"]
    info = backend.get_scope_info("/crew/a")
    assert info.record_count == 2 and info.categories == ["p", "q"]
    assert info.child_scopes == ["/crew/a/deep"]
    assert backend.list_categories() == {"p": 2, "q": 2}
    assert backend.count("/crew/a") == 2
    assert [r.content for r in backend.list_records(limit=2, offset=0)][:2] == ["3", "2"]


def test_tenants_are_isolated_and_tenant_forget_hides_records(db: StandardDatabase) -> None:
    from arango_memory.security.forget import forget

    mine, theirs = _backend(db), _backend(db, tenant_id="cw1_other")
    _save(mine, _record("private note"))
    assert theirs.count() == 0 and theirs.list_records() == []
    forget(db, tenant_id=TENANT)
    assert mine.count() == 0


def test_touch_records_refreshes_access(db: StandardDatabase) -> None:
    backend = _backend(db)
    rec = _record("touch me", last_accessed=datetime(2020, 1, 1))  # noqa: DTZ001
    _save(backend, rec)
    row = db.collection("crewai_records").get(backend._row_key(rec.id))
    before = db.collection("memories").get(row["memory_key"])["access_count"]
    backend.touch_records([rec.id])
    got = backend.get_record(rec.id)
    assert got is not None and got.last_accessed.year > 2020
    assert db.collection("memories").get(row["memory_key"])["access_count"] == before + 1
