"""IN-4: memory provenance time is a *field* surfaced at assembly, never in the matched text."""

from __future__ import annotations

from arango.database import StandardDatabase

from arango_memory.ingest.store import StoreItem, store, store_many
from arango_memory.retrieve.search import _assemble_line, _Candidate, force_view_sync, retrieve


def test_assemble_line_prefixes_event_time() -> None:
    with_date = _Candidate(key="k", text="the rocket launched", embedding=[], type="episodic",
                           event_time="2023/05/20")
    without = _Candidate(key="k", text="the rocket launched", embedding=[], type="episodic")
    assert _assemble_line(with_date) == "- [2023/05/20] the rocket launched"
    assert _assemble_line(without) == "- the rocket launched"


def test_event_time_surfaced_in_context_not_in_text(db: StandardDatabase) -> None:
    store_many(
        db,
        [StoreItem(content="The rocket launched successfully.", turn_index=0,
                   event_time="2023/05/20 (Sat)")],
        tenant_id="t4", agent_id="a",
    )
    force_view_sync(db, "t4")
    r = retrieve(db, query="rocket launch", tenant_id="t4", agent_id="a")

    assert r.hits
    assert "[2023/05/20 (Sat)]" in r.context          # surfaced for the answerer
    assert "rocket launched successfully" in r.context
    # the date is NOT part of the retrievable/matched text (it's a separate field)
    assert all("2023/05/20" not in h.text for h in r.hits)


def test_store_single_carries_event_time(db: StandardDatabase) -> None:
    res = store(db, content="Quarterly revenue rose.", tenant_id="t4s", agent_id="a",
                turn_index=0, event_time="2024/01/31", extract=False)
    mem = db.collection("memories").get(res.memory_ids[0])
    assert mem is not None and mem["event_time"] == "2024/01/31"


def test_no_event_time_is_unchanged(db: StandardDatabase) -> None:
    store_many(db, [StoreItem(content="A plain memory.", turn_index=0)],
               tenant_id="t4n", agent_id="a")
    force_view_sync(db, "t4n")
    r = retrieve(db, query="plain memory", tenant_id="t4n", agent_id="a")
    assert r.hits
    assert r.context == "- A plain memory."  # no prefix when there's no event_time
