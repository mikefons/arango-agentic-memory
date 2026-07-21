"""RT-1: candidate_pool is a config + API knob; None falls back to settings."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from arango.database import StandardDatabase

from arango_memory.api.app import RetrieveOptions
from arango_memory.config import settings
from arango_memory.ingest.store import store
from arango_memory.retrieve.search import RetrieveResult, retrieve


def test_retrieve_options_defaults_to_settings_candidate_pool() -> None:
    assert RetrieveOptions().candidate_pool == settings.candidate_pool


def test_retrieve_honours_settings_pool_when_arg_is_none(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
    monkeypatch: Any,
) -> None:
    # A tiny settings pool must bound the fused candidates when candidate_pool is not passed.
    captured: dict[str, int] = {}
    from arango_memory.retrieve import search as search_mod

    real_run = search_mod._run

    def spy(db_: StandardDatabase, query: str, bind_vars: dict[str, Any]) -> Any:
        if "pool" in bind_vars:
            captured["pool"] = bind_vars["pool"]
        return real_run(db_, query, bind_vars)

    monkeypatch.setattr(settings, "candidate_pool", 3)
    monkeypatch.setattr(search_mod, "_run", spy)

    ctx = {"tenant_id": "t_pool_knob", "agent_id": "a"}
    for i in range(5):
        store(db, content=f"memory {i} about pools", turn_index=i, **ctx)
    wait_for_searchable(db, query="pools", **ctx)  # uses default pool (no candidate_pool arg)

    assert captured.get("pool") == 3  # the AQL arms were bound with the settings pool


def test_explicit_candidate_pool_overrides_settings(
    db: StandardDatabase,
    wait_for_searchable: Callable[..., RetrieveResult],
    monkeypatch: Any,
) -> None:
    from arango_memory.retrieve import search as search_mod

    real_run = search_mod._run
    seen: list[int] = []

    def spy(db_: StandardDatabase, query: str, bind_vars: dict[str, Any]) -> Any:
        if "pool" in bind_vars:
            seen.append(bind_vars["pool"])
        return real_run(db_, query, bind_vars)

    monkeypatch.setattr(settings, "candidate_pool", 3)
    monkeypatch.setattr(search_mod, "_run", spy)

    ctx = {"tenant_id": "t_pool_override", "agent_id": "a"}
    store(db, content="one memory here", turn_index=0, **ctx)
    wait_for_searchable(db, query="memory", **ctx)
    seen.clear()
    retrieve(db, query="memory", **ctx, candidate_pool=50)  # explicit wins over settings
    assert seen and all(p == 50 for p in seen)
