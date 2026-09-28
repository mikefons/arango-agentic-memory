"""Optional `crewai.Storage` shim (DESIGN.md §21) — requires the `crewai` extra.

Adapts an `ArangoCrewStorage` to CrewAI's legacy text-based `Storage` interface
so it can be wired via `Crew(external_memory=ExternalMemory(storage=...))`. The
`crewai` import is lazy (resolved only when this is called), keeping `crewai` out
of the core's hot path and out of CI — the storage logic itself is crewai-free
and fully tested in `storage.py`.

CrewAI's per-call `score_threshold` is deliberately *not* forwarded: our hybrid
RRF-fused scores live on a different scale than CrewAI's cosine scores, so a
crewai default threshold would drop every hit. We rely on `limit` for cutoff.

CrewAI 1.10.0 removed the legacy `Storage` interface (and `ExternalMemory`) in
favour of its unified memory, so the shim works with `crewai<1.10` only; on newer
crewai it raises a clear `ImportError` pointing to `arango_crewai_memory()` (unified.py).
"""

from __future__ import annotations

from typing import Any

from .storage import ArangoCrewStorage


def _storage_base() -> type:
    try:
        from crewai.memory.storage.interface import Storage  # lazy: needs the extra
    except ImportError as exc:
        try:
            import crewai
        except ImportError:
            raise ImportError(
                "to_crewai_storage() needs the `crewai` extra: pip install 'arango-memory[crewai]'"
            ) from exc
        raise ImportError(
            f"crewai {getattr(crewai, '__version__', '?')} has no legacy "
            "`crewai.memory.storage.interface.Storage` (removed in crewai 1.10.0); "
            "to_crewai_storage() supports crewai<1.10 only. On crewai>=1.10 use "
            "arango_crewai_memory() (unified memory): Crew(memory=arango_crewai_memory(...))."
        ) from exc

    return Storage  # type: ignore[no-any-return]


def to_crewai_storage(storage: ArangoCrewStorage) -> Any:
    """Wrap an `ArangoCrewStorage` as a CrewAI `Storage` instance."""
    base = _storage_base()

    class _ArangoCrewAIStorage(base):  # type: ignore[misc, valid-type]
        def save(self, value: Any, metadata: dict[str, Any] | None = None) -> None:
            storage.save(value, metadata)

        def search(
            self, query: str, limit: int = 3, score_threshold: float = 0.35
        ) -> list[dict[str, Any]]:
            return storage.search(query, limit=limit)

        def reset(self) -> None:
            storage.reset()

    return _ArangoCrewAIStorage()
