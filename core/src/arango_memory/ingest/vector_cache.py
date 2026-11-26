"""Entity embeddings kept in memory between writes (PF-1h).

Without the entity vector index, resolution scores each new name against every entity of the
tenant. Downloading their embeddings for every batch dominated that step as a tenant grew, so
the core keeps them, and the scan returns an embedding only when the cached one is missing or
out of date. "Out of date" means the entity's stamp (model, version, re-embed time) changed;
an embedding edited in place without touching those goes unnoticed until restart.

The scan itself still decides which entities exist: a tenant's entry is replaced by what the
last scan returned, so deleted entities drop out.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

# entity key -> (stamp, embedding)
Entry = dict[str, tuple[str, Any]]
Scope = tuple[str, str]  # (database, tenant)

_OVERHEAD = 200  # rough bytes per entry beyond the vector itself


def _size(entry: Entry) -> int:
    return sum(vec.nbytes + _OVERHEAD for _, vec in entry.values())


class EntityVectorCache:
    """Least-recently-used tenants' vectors, bounded in total bytes. Thread-safe: an entry is
    never changed once stored, only replaced."""

    def __init__(self) -> None:
        self._tenants: OrderedDict[Scope, Entry] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    @property
    def size_bytes(self) -> int:
        return self._bytes

    def get(self, scope: Scope) -> Entry:
        with self._lock:
            entry = self._tenants.get(scope)
            if entry is None:
                return {}
            self._tenants.move_to_end(scope)
            return entry

    def put(self, scope: Scope, entry: Entry, max_bytes: int) -> None:
        """Replace a tenant's entry, keeping as much of it as the budget holds (in scan order,
        so the same entities stay cached from one write to the next)."""
        kept: Entry = {}
        used = 0
        for key, value in entry.items():
            used += value[1].nbytes + _OVERHEAD
            if used > max_bytes:
                break
            kept[key] = value
        with self._lock:
            self._remove(scope)
            if kept:
                self._tenants[scope] = kept
                self._bytes += _size(kept)
            while self._bytes > max_bytes:
                self._remove(next(iter(self._tenants)))

    def drop(self, scope: Scope) -> None:
        with self._lock:
            self._remove(scope)

    def clear(self) -> None:
        with self._lock:
            self._tenants.clear()
            self._bytes = 0

    def _remove(self, scope: Scope) -> None:
        entry = self._tenants.pop(scope, None)
        if entry is not None:
            self._bytes -= _size(entry)


entity_vectors = EntityVectorCache()
