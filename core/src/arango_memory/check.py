"""Connection/round-trip check for either target (local Docker or ArangoGraph).

Connects using the current settings, bootstraps the schema, runs a real
store -> BM25 retrieve probe under an isolated health-check tenant, then cleans
up. Switch targets purely via environment (.env), e.g.:

    # local Docker
    uv run python -m arango_memory.check

    # ArangoGraph
    ARANGO_TARGET=arangograph ARANGO_URL=https://<id>.arangodb.cloud:8529 \
      ARANGO_PASSWORD=... uv run python -m arango_memory.check
"""

from __future__ import annotations

import sys
import time

from arango.database import StandardDatabase

from .client import ArangoMemoryClient
from .ingest.store import store
from .retrieve.search import retrieve
from .schema.collections import ensure_schema

_PROBE_TENANT = "__healthcheck__"
_PROBE_AGENT = "__probe__"
_PROBE_TEXT = "arango-memory connectivity probe: the quick brown fox"


def main() -> int:
    client = ArangoMemoryClient()

    try:
        db = client.connect()
    except Exception as exc:  # noqa: BLE001 — surface any connection failure
        print(f"FAIL: could not connect — {exc}", file=sys.stderr)
        return 1

    info = client.describe()
    version = db.version()
    print(f"connected: target={info['target']} url={info['url']} "
          f"db={info['database']} auth={info['auth']} version={version}")

    ensure_schema(db)
    print("schema: ensured")

    result = store(
        db, content=_PROBE_TEXT, tenant_id=_PROBE_TENANT, agent_id=_PROBE_AGENT
    )
    print(f"store: episode={result.episode_id[:12]}… memory={result.memory_ids[0][:12]}…")

    # ArangoSearch view is eventually consistent — poll briefly for the hit.
    hit_found = False
    for _ in range(10):
        time.sleep(0.5)
        r = retrieve(db, query="quick brown fox", tenant_id=_PROBE_TENANT, agent_id=_PROBE_AGENT)
        if r.hits:
            print(f"retrieve: {len(r.hits)} hit(s), top score={r.hits[0].score:.3f}, "
                  f"tokens={r.tokens_injected}")
            hit_found = True
            break

    _cleanup(db, result.episode_id, result.memory_ids)
    print("cleanup: probe records removed")

    if not hit_found:
        print("FAIL: stored probe was not retrievable via BM25", file=sys.stderr)
        return 1

    print("PASS: store -> retrieve round-trip OK")
    return 0


def _cleanup(db: StandardDatabase, episode_id: str, memory_ids: list[str]) -> None:
    db.collection("episodes").delete(episode_id, ignore_missing=True)
    for mid in memory_ids:
        db.collection("memories").delete(mid, ignore_missing=True)


if __name__ == "__main__":
    raise SystemExit(main())
