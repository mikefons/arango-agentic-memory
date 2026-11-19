"""Neighbour expansion: retrieval also injects the turns next to each hit, in the hit's session.

A retrieved question often has its answer in the reply that follows it. The neighbours only take
token budget the hits left, and sit next to their hit in turn order.
"""

from __future__ import annotations

import pytest
from arango.database import StandardDatabase
from fastapi.testclient import TestClient

from arango_memory.config import settings
from arango_memory.ingest.store import StoreItem, store, store_many
from arango_memory.retrieve.search import force_view_sync, retrieve

_CHAT = [
    "user: any tips for the marathon in Lisbon",
    "assistant: start slowly and drink at every station",
    "user: thanks, noted",
    "assistant: good luck on the day",
]


def _seed(db: StandardDatabase, tenant: str, *, session: str | None = "s1") -> None:
    store_many(
        db,
        [StoreItem(content=text, turn_index=i, session_id=session) for i, text in enumerate(_CHAT)]
        # Another chat, at the same positions: never a neighbour of the first.
        + [StoreItem(content=f"user: unrelated gardening note {i}", turn_index=i, session_id="s2")
           for i in range(4)],
        tenant_id=tenant, agent_id="a",
    )
    force_view_sync(db, tenant)


def test_off_by_default(db: StandardDatabase) -> None:
    _seed(db, "t_nb1")
    result = retrieve(db, query="marathon Lisbon tips", tenant_id="t_nb1", agent_id="a", k=1)
    assert [h.text for h in result.hits] == [_CHAT[0]]
    assert "drink at every station" not in result.context


def test_injects_the_adjacent_turns_of_the_same_session_in_turn_order(
    db: StandardDatabase,
) -> None:
    _seed(db, "t_nb2")
    result = retrieve(db, query="start slowly and drink at every station", tenant_id="t_nb2",
                      agent_id="a", k=1, neighbor_turns=1)
    assert result.context.splitlines() == [f"- {t}" for t in _CHAT[:3]]
    # The k hits come first; injected neighbours follow, labelled.
    assert [(h.text, h.source) for h in result.hits[1:]] == [
        (_CHAT[0], "neighbor"), (_CHAT[2], "neighbor"),
    ]
    assert "gardening" not in result.context


def test_window_and_setting(db: StandardDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(db, "t_nb3")
    monkeypatch.setattr(settings, "retrieve_neighbor_turns", 3)
    result = retrieve(db, query="marathon Lisbon tips", tenant_id="t_nb3", agent_id="a", k=1)
    assert result.context.splitlines() == [f"- {t}" for t in _CHAT]


def test_a_neighbour_that_is_already_a_hit_is_not_repeated(db: StandardDatabase) -> None:
    _seed(db, "t_nb4")
    result = retrieve(db, query="marathon Lisbon tips start slowly drink station",
                      tenant_id="t_nb4", agent_id="a", k=2, neighbor_turns=1)
    texts = [h.text for h in result.hits]
    assert set(texts[:2]) == set(_CHAT[:2])
    assert texts[2:] == [_CHAT[2]] and len(set(texts)) == len(texts)
    assert result.context.count(_CHAT[0]) == 1


def test_neighbours_never_displace_a_hit(db: StandardDatabase) -> None:
    _seed(db, "t_nb5")
    base = retrieve(db, query="marathon Lisbon tips", tenant_id="t_nb5", agent_id="a", k=1,
                    record_access=False)
    # Budget for the hit alone: the neighbours do not fit, and the hit stays.
    tight = retrieve(db, query="marathon Lisbon tips", tenant_id="t_nb5", agent_id="a", k=1,
                     max_memory_tokens=base.tokens_injected + 2, neighbor_turns=1)
    assert tight.context == base.context and len(tight.hits) == 1
    assert tight.tokens_injected <= base.tokens_injected + 2


def test_needs_a_session_and_a_position(db: StandardDatabase) -> None:
    # No session id: positions alone do not say which turns belong together.
    _seed(db, "t_nb6", session=None)
    result = retrieve(db, query="marathon Lisbon tips", tenant_id="t_nb6", agent_id="a", k=1,
                      neighbor_turns=1)
    assert len(result.hits) == 1
    # A caller that never sets turn_index leaves every memory at 0: none is a neighbour.
    for text in _CHAT:
        store(db, content=text, tenant_id="t_nb7", agent_id="a", session_id="s1")
    force_view_sync(db, "t_nb7")
    result = retrieve(db, query="marathon Lisbon tips", tenant_id="t_nb7", agent_id="a", k=1,
                      neighbor_turns=1)
    assert len(result.hits) == 1


def test_api_option(db: StandardDatabase, api: TestClient) -> None:
    _seed(db, "t_nb8")
    body = {"query": "start slowly and drink at every station",
            "ctx": {"tenant_id": "t_nb8", "agent_id": "a"}}
    r = api.post("/v1/retrieve", json={**body, "opts": {"k": 1, "neighbor_turns": 1}})
    assert r.status_code == 200
    assert r.json()["context"].splitlines() == [f"- {t}" for t in _CHAT[:3]]
    assert api.post("/v1/retrieve", json={**body, "opts": {"neighbor_turns": 9}}).status_code == 422
