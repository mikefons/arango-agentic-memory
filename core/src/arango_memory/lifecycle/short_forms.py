"""Merging short entity names into longer ones (off by default, `MERGE_SHORT_FORMS`).

"Ridgeline" and "Ridgeline Ventures" are two entities at write time, since which longer names
exist depends on what has arrived. A Dream State pass merges a one-word name into a longer
name that begins with it, when the tenant has exactly one live name that qualifies (the words
added are capitalised and not acronyms). Types are ignored, as they are when names are first
matched: one thing often comes back with different ones.

The merged entity stays, retired, with `merged_into` naming the longer one: that is what sends
later writes of the short name there (`ingest.entities`). Its counts and edges then move over.
Every statement does nothing when repeated, and a run finishes whatever an earlier one, or a
write that arrived during it, left behind.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ..config import settings
from ..ingest.conflicts import retry_on_conflict
from ..ingest.entities import name_key
from ..ingest.extract import DEFAULT_RELATION
from ..models import utcnow_iso

_ARTICLES = frozenset({"a", "an", "the"})

_LIVE = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id AND e.invalid_at == null
  RETURN { key: e._key, name: e.name }
"""

_RETIRE = """
FOR s IN entities
  FILTER s._key == @short AND s.tenant_id == @tenant_id AND s.invalid_at == null
  FOR t IN entities
    FILTER t._key == @target AND t.tenant_id == @tenant_id AND t.invalid_at == null
    UPDATE s WITH { invalid_at: @now, merged_into: t._key, needs_review: false,
                    merged_mentions: 0, merged_reliability: 0 } IN entities
    RETURN 1
"""

_POINTERS = """
FOR e IN entities
  FILTER e.tenant_id == @tenant_id AND e.merged_into != null
  RETURN [e._key, e.merged_into]
"""

# Retired entities that still hold counts or edges.
_UNSETTLED = """
FOR s IN entities
  FILTER s.tenant_id == @tenant_id AND s.merged_into != null
  FILTER s.mention_count > s.merged_mentions
    OR NOT_NULL(s.reliability_sum, 0) > s.merged_reliability
    OR LENGTH(FOR e IN mentions FILTER e._to == s._id LIMIT 1 RETURN 1) > 0
    OR LENGTH(FOR e IN produced_by FILTER e._from == s._id LIMIT 1 RETURN 1) > 0
    OR LENGTH(FOR e IN relates_to FILTER e._from == s._id LIMIT 1 RETURN 1) > 0
    OR LENGTH(FOR e IN relates_to FILTER e._to == s._id LIMIT 1 RETURN 1) > 0
  RETURN [s._key, s.merged_into]
"""

# One statement, so both change or neither: the retired entity records what it has handed
# over, and only the difference is ever added.
_MOVE_COUNTS = """
FOR s IN entities
  FILTER s._key == @short AND s.merged_into != null
  LET mentions_left = s.mention_count - s.merged_mentions
  LET reliability_left = NOT_NULL(s.reliability_sum, 0) - s.merged_reliability
  FILTER mentions_left > 0 OR reliability_left > 0
  FOR t IN entities
    FILTER t._key == @target AND t.merged_into == null
    LET total = NOT_NULL(t.reliability_sum, 0) + reliability_left
    FOR change IN [
      { _key: s._key, merged_mentions: s.mention_count,
        merged_reliability: NOT_NULL(s.reliability_sum, 0) },
      { _key: t._key, mention_count: t.mention_count + mentions_left, reliability_sum: total,
        belief: t.confidence * (1 - POW(1 - @base, total)) }
    ]
      UPDATE change IN entities
"""

_EDGES_OF = """
FOR e IN @@coll
  FILTER e._from == @id OR e._to == @id
  RETURN UNSET(e, "_id", "_rev")
"""

_ADD_EDGES = """
FOR doc IN @docs
  INSERT doc INTO @@coll OPTIONS { overwriteMode: "ignore" }
"""

_REMOVE_EDGES = """
FOR key IN @keys
  REMOVE { _key: key } IN @@coll OPTIONS { ignoreErrors: true }
"""

# Adds a moved relation to the one the longer entity already has, once: `merged_from`
# remembers which edges it has taken in.
_JOIN_RELATION = """
UPSERT { _key: @key }
INSERT MERGE(@doc, { merged_from: [@token] })
UPDATE ((@token IN NOT_NULL(OLD.merged_from, [])) ? {} : {
  corroboration: NOT_NULL(OLD.corroboration, 1) + NOT_NULL(@doc.corroboration, 1),
  reliability_sum: NOT_NULL(OLD.reliability_sum, 0) + NOT_NULL(@doc.reliability_sum, 0),
  belief: 1 - POW(1 - @base,
                  NOT_NULL(OLD.reliability_sum, 0) + NOT_NULL(@doc.reliability_sum, 0)),
  weight: MAX([OLD.weight, @doc.weight]),
  relationship: OLD.relationship == @untyped ? @doc.relationship : OLD.relationship,
  last_seen: MAX([OLD.last_seen, @doc.last_seen]),
  merged_from: PUSH(NOT_NULL(OLD.merged_from, []), @token)
})
IN relates_to
"""


# Merged pairs with the memories to judge them by. A pair is listed only while the caller can
# still read a memory using the short name, so forgotten or hidden text doesn't surface a name.
# `samples`: memories whose link the merge moved, or that use the short name without the longer
# one (written since). `into_samples`: what the longer name refers to.
_REPORT = """
FOR s IN entities
  FILTER s.tenant_id == @tenant_id AND s.merged_into != null
  LET t = DOCUMENT(entities, s.merged_into)
  FILTER t != null AND t.tenant_id == @tenant_id AND t.invalid_at == null
  LET samples = (
    FOR m, link IN 1..1 INBOUND t._id mentions
      FILTER m.tenant_id == @tenant_id AND m.invalid_at == null
      FILTER @agent_ids == null OR m.agent_id IN @agent_ids
        OR LENGTH(FOR p IN @agent_prefixes FILTER STARTS_WITH(m.agent_id, p) RETURN 1) > 0
      FILTER link.merged_short == s._key
        OR (CONTAINS(m.text, s.name) AND NOT CONTAINS(m.text, t.name))
      SORT NOT_NULL(m.event_time, m.created_at) DESC, m._key
      LIMIT @samples
      RETURN m.text)
  FILTER LENGTH(samples) > 0
  LET into_samples = (
    FOR m IN 1..1 INBOUND t._id mentions
      FILTER m.tenant_id == @tenant_id AND m.invalid_at == null
      FILTER @agent_ids == null OR m.agent_id IN @agent_ids
        OR LENGTH(FOR p IN @agent_prefixes FILTER STARTS_WITH(m.agent_id, p) RETURN 1) > 0
      FILTER CONTAINS(m.text, t.name)
      SORT NOT_NULL(m.event_time, m.created_at) DESC, m._key
      LIMIT @samples
      RETURN m.text)
  SORT s.invalid_at DESC, s._key
  LIMIT @limit
  RETURN { id: s._key, name: s.name, into_id: t._key, into_name: t.name,
           merged_at: s.invalid_at, samples, into_samples }
"""


def merge_report(
    db: StandardDatabase,
    *,
    tenant_id: str,
    agent_ids: Sequence[str] | None = None,
    limit: int = 50,
    samples: int = 3,
) -> list[dict[str, Any]]:
    """The tenant's merged short names, newest first, for judging whether each was right.
    `agent_ids` is a credential's allow-list (a trailing `*` matches by prefix); None reads
    every agent's memories."""
    patterns = None if agent_ids is None else list(agent_ids)
    return _query(
        db, _REPORT, tenant_id=tenant_id, limit=limit, samples=samples,
        agent_ids=None if patterns is None else [p for p in patterns if not p.endswith("*")],
        agent_prefixes=[p[:-1] for p in patterns or [] if p.endswith("*")],
    )


def _could_follow(word: str) -> bool:
    """Whether a word can be part of a fuller name: "Ventures" can, while "ARR" or "deal"
    make it the name of something else."""
    return word == "&" or (word[:1].isupper() and not (len(word) > 1 and word.isupper()))


def plan_merges(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """(short key, longer key) for each one-word name that begins exactly one qualifying
    longer name."""
    longer: dict[str, list[str]] = {}  # first word → the fuller names beginning with it
    for row in rows:
        count = len(name_key(row["name"]).split())  # leaves out a trailing legal suffix
        added = row["name"].split()[1:count]
        if count > 1 and all(_could_follow(word.strip(".,")) for word in added):
            longer.setdefault(name_key(row["name"]).split()[0], []).append(row["key"])
    planned = []
    for row in rows:
        words = name_key(row["name"]).split()
        if len(words) == 1 and words[0] not in _ARTICLES:
            targets = longer.get(words[0], [])
            if len(targets) == 1:
                planned.append((row["key"], targets[0]))
    return planned


def merge_short_forms(db: StandardDatabase, *, tenant_id: str) -> int:
    """Merge the tenant's short names and finish earlier merges. Returns how many entities
    were newly retired."""
    rows = _query(db, _LIVE, tenant_id=tenant_id)
    merged = 0
    now = utcnow_iso()
    for short, target in plan_merges(rows):
        merged += len(_query(
            db, _RETIRE, tenant_id=tenant_id, short=short, target=target, now=now))
    _settle(db, tenant_id)
    return merged


def _query(db: StandardDatabase, query: str, **bind: Any) -> list[Any]:
    return retry_on_conflict(
        lambda: list(cast(Cursor, db.aql.execute(query, bind_vars=bind))))


def _settle(db: StandardDatabase, tenant_id: str) -> None:
    unsettled: list[list[str]] = _query(db, _UNSETTLED, tenant_id=tenant_id)
    if not unsettled:
        return
    pointers: dict[str, str] = dict(_query(db, _POINTERS, tenant_id=tenant_id))
    for short, target in unsettled:
        _query(db, _MOVE_COUNTS, short=short, target=target, base=settings.corroboration_base)
        _move_links(db, "mentions", short, target)
        _move_links(db, "produced_by", short, target)
        _move_relations(db, short, target, pointers)


def _move_links(db: StandardDatabase, collection: str, short: str, target: str) -> None:
    """Point `mentions` (memory→entity) or `produced_by` (entity→episode) edges at `target`."""
    old = _query(db, _EDGES_OF, **{"@coll": collection, "id": f"entities/{short}"})
    if not old:
        return
    moved = []
    for edge in old:
        if collection == "mentions":
            key = f"{edge['_from'].split('/', 1)[1]}__{target}"
            # `merged_short` lets the merge report show which memories used the short name
            moved.append(
                {**edge, "_key": key, "_to": f"entities/{target}", "merged_short": short})
        else:
            key = f"{target}__{edge['_to'].split('/', 1)[1]}"
            moved.append({**edge, "_key": key, "_from": f"entities/{target}"})
    _query(db, _ADD_EDGES, **{"@coll": collection, "docs": moved})
    _query(db, _REMOVE_EDGES, **{"@coll": collection, "keys": [e["_key"] for e in old]})


def _move_relations(
    db: StandardDatabase, short: str, target: str, pointers: Mapping[str, str]
) -> None:
    old = _query(db, _EDGES_OF, **{"@coll": "relates_to", "id": f"entities/{short}"})
    if not old:
        return
    for edge in old:
        ends = [edge[end].split("/", 1)[1] for end in ("_from", "_to")]
        other = ends[1] if ends[0] == short else ends[0]
        other = pointers.get(other, other)  # it may be a merged short name too
        if other in (short, target):
            continue  # the two names of one entity: no relation to keep
        lo, hi = sorted((target, other))
        key = f"{lo}__{hi}"
        doc = {**edge, "_key": key, "_from": f"entities/{lo}", "_to": f"entities/{hi}"}
        # an edge written again later under the old key is new evidence, not a repeat
        token = f"{edge['_key']}@{edge.get('ingestion_time')}"
        _query(db, _JOIN_RELATION, key=key, doc=doc, token=token, untyped=DEFAULT_RELATION,
               base=settings.corroboration_base)
    _query(db, _REMOVE_EDGES, **{"@coll": "relates_to", "keys": [e["_key"] for e in old]})
