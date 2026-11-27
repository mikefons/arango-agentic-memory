"""A write the driver sends twice has the effect of one (`arango_memory.repeats`)."""

from __future__ import annotations

from typing import Any

import pytest
from arango.database import StandardDatabase

from arango_memory.ingest.procedural import get_steps, record_step
from arango_memory.ingest.queue import ArangoQueue, WriteIntent
from arango_memory.ingest.store import StoreItem, store, store_many
from arango_memory.transport import ObservedHTTPClient

# What a write leaves behind that must not depend on how often it was sent.
_STATE = {
    "entities": "[d.name, d.mention_count, d.reliability_sum, d.belief]",
    "relates_to": "[d.relationship, d.corroboration, d.reliability_sum, d.belief]",
    "steps": "[d.tool_name, d.use_count]",
    "mentions": "[1]",
    "produced_by": "[1]",
    "episodes": "[d._key]",
    "memories": "[d._key]",
}


def _state(db: StandardDatabase) -> dict[str, list[Any]]:
    return {
        name: sorted(
            db.aql.execute(f"FOR d IN {name} RETURN {fields}"),  # type: ignore[arg-type]
            key=repr,
        )
        for name, fields in _STATE.items()
    }


def _writes(db: StandardDatabase, tenant: str) -> list[list[str]]:
    turns = [
        "Dana Reyes met Marcus Cole at Northwind Robotics.",
        "Northwind Robotics signed Orion Retail with Dana Reyes.",
        "Marcus Cole left Orion Retail.",
    ]
    scope = {"tenant_id": tenant, "agent_id": "a"}
    singles = [
        store(db, content=text, turn_index=i, **scope).entity_ids  # type: ignore[arg-type]
        for i, text in enumerate(turns)
    ]
    batch = store_many(
        # known names (increments) and new ones (upserts)
        db, [StoreItem(content=text, turn_index=10 + i)
             for i, text in enumerate([*turns, "Halcyon Grocers hired Elena Voss."])],
        extract=True, **scope,
    )
    for _ in range(2):
        record_step(db, tool_name="search", arguments={}, outcome="ok", **scope)
    return [*singles, *(r.entity_ids for r in batch)]


@pytest.fixture
def sent_twice(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every request that opens a query or inserts a document goes to the database twice, and
    the caller sees the second answer, as after a connection lost behind the first."""
    repeated: list[str] = []
    real = ObservedHTTPClient.send_request

    def twice(self: ObservedHTTPClient, session: Any, method: str, url: str, *args: Any,
              **kwargs: Any) -> Any:
        if method.lower() == "post" and (url.endswith("/_api/cursor") or "/_api/document/" in url):
            real(self, session, method, url, *args, **kwargs)
            repeated.append(url)
        return real(self, session, method, url, *args, **kwargs)

    monkeypatch.setattr(ObservedHTTPClient, "send_request", twice)
    return repeated


def test_writes_sent_twice_count_once(
    db: StandardDatabase, request: pytest.FixtureRequest
) -> None:
    keys_once = _writes(db, "t")
    once = _state(db)
    assert once["entities"] and once["relates_to"] and all(keys_once[3:])  # the batch linked
    assert any(row[0] == "Elena Voss" for row in once["entities"])
    for name in _STATE:
        db.collection(name).truncate()

    repeated: list[str] = request.getfixturevalue("sent_twice")
    keys_twice = _writes(db, "t")
    assert len(repeated) > 20
    assert _state(db) == once
    assert keys_twice == keys_once


def test_a_claim_sent_twice_leases_the_same_intents(
    db: StandardDatabase, request: pytest.FixtureRequest
) -> None:
    queue = ArangoQueue(db, lease_seconds=60)
    request.getfixturevalue("sent_twice")
    for i in range(5):
        queue.enqueue(WriteIntent(content=f"turn {i}", tenant_id="t", agent_id="a"))
    first = queue.claim_many(2)
    assert [c.intent.content for c in first] == ["turn 0", "turn 1"]  # type: ignore[union-attr]
    one = queue.claim()
    assert one is not None and one.intent.content == "turn 2"  # type: ignore[union-attr]
    assert len(queue) == 2  # the other two are still free


def test_repeated_ids_are_capped_and_not_returned(db: StandardDatabase) -> None:
    for _ in range(20):
        key = record_step(db, tool_name="t", arguments={}, outcome="ok",
                          tenant_id="a", agent_id="b")
    stored = db.collection("steps").get(key)
    assert stored["use_count"] == 20 and len(stored["ops"]) == 16  # type: ignore[index]
    (step,) = get_steps(db, tenant_id="a", agent_id="b")
    assert "ops" not in step
