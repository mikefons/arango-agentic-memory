"""SC-1a scaling profiler: per-`store()` and per-`retrieve()` latency vs single-tenant size.

Baselines the two BX-2/BX-3 bottlenecks and their fixes (the summary line reports whether each
metric PLATEAUS — bounded — or is still rising in the tail):
  - **Ingestion (fixed by SC-1b).** Entity resolution once full-scanned every tenant entity per
    write; ANN top-k resolution bounds it → `store` p50 should climb then plateau.
  - **Retrieval fan-out (bounded by SC-1c + SC-1d).** The graph arm traverses a `relates_to`
    graph that densifies as the tenant fills; the neighbour cap (SC-1c) and per-entity memory
    cap (SC-1d) bound the work → `retrieve` p50 should plateau rather than keep rising.

Ingests synthetic, entity-rich memories into one tenant (keyless: the `fake` extractor turns
capitalized spans into entities) and reports windowed `store` latency + a `retrieve` sample at
each checkpoint.

    python -m arango_memory.eval.scaling_profile [--max 3000] [--step 500] [--probes 20]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

from arango.database import StandardDatabase

from ..client import ArangoMemoryClient
from ..ingest.store import store
from ..retrieve.search import retrieve
from ..schema.collections import ensure_schema
from ..telemetry.logging import configure_logging

_PROBE_QUERIES = [
    "who collaborated with Hub3 on Topic10",
    "recent work involving Hub7 and Topic42",
    "Ent collaboration on Topic100",
    "Hub20 project team",
]


@dataclass
class ProfileRow:
    size: int
    store_p50: float
    store_p99: float
    retrieve_p50: float
    retrieve_p99: float


def _pct(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in [0,1]); 0.0 for empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[idx]


def _content(i: int) -> str:
    """Entity-rich synthetic memory: unique `Ent{i}` grow the entity set; `Hub`/`Topic`
    repeat so resolution also exercises merges (capitalized spans → entities, fake extractor)."""
    return f"Ent{i} collaborated with Ent{i + 1} and Hub{i % 50} on Topic{i % 200}"


def profile(
    db: StandardDatabase,
    *,
    max_n: int,
    step: int,
    probes: int,
    tenant_id: str = "scaling_probe",
    agent_id: str = "a",
    progress: bool = False,
) -> list[ProfileRow]:
    """Ingest `max_n` memories into one tenant, sampling store + retrieve latency every `step`."""
    rows: list[ProfileRow] = []
    store_window_ms: list[float] = []
    for i in range(1, max_n + 1):
        t0 = time.perf_counter()
        store(db, content=_content(i), tenant_id=tenant_id, agent_id=agent_id, turn_index=i)
        store_window_ms.append((time.perf_counter() - t0) * 1000.0)
        if i % step == 0:
            retrieve_ms: list[float] = []
            for j in range(probes):
                t1 = time.perf_counter()
                retrieve(db, query=_PROBE_QUERIES[j % len(_PROBE_QUERIES)],
                         tenant_id=tenant_id, agent_id=agent_id)
                retrieve_ms.append((time.perf_counter() - t1) * 1000.0)
            row = ProfileRow(
                size=i,
                store_p50=_pct(store_window_ms, 0.5),
                store_p99=_pct(store_window_ms, 0.99),
                retrieve_p50=_pct(retrieve_ms, 0.5),
                retrieve_p99=_pct(retrieve_ms, 0.99),
            )
            rows.append(row)
            if progress:
                print(f"[{i}/{max_n}] store p50={row.store_p50:.0f}ms p99={row.store_p99:.0f}ms "
                      f"| retrieve p50={row.retrieve_p50:.0f}ms p99={row.retrieve_p99:.0f}ms",
                      file=sys.stderr, flush=True)
            store_window_ms = []
    return rows


def _format(rows: list[ProfileRow]) -> str:
    lines = ["size    store_p50  store_p99  ret_p50  ret_p99   (ms)"]
    for r in rows:
        lines.append(f"{r.size:<6}  {r.store_p50:>8.0f}  {r.store_p99:>8.0f}  "
                     f"{r.retrieve_p50:>7.0f}  {r.retrieve_p99:>7.0f}")
    if len(rows) >= 3 and rows[0].store_p50 > 0 and rows[0].retrieve_p50 > 0:
        # Shape matters more than the first/last ratio: a plateau (climb then flat) is the
        # bounded-cost signature, whereas a curve that keeps rising into the tail is unbounded.
        # Compare the second-half slope to the first-half slope for each metric.
        lines.append("")
        mid = len(rows) // 2
        metrics: list[tuple[str, list[float]]] = [
            ("store", [r.store_p50 for r in rows]),
            ("retrieve", [r.retrieve_p50 for r in rows]),
        ]
        for label, vals in metrics:
            head_slope = (vals[mid] - vals[0]) / max(rows[mid].size - rows[0].size, 1)
            tail_slope = (vals[-1] - vals[mid]) / max(rows[-1].size - rows[mid].size, 1)
            total = vals[-1] / vals[0]
            shape = (
                "PLATEAUS (bounded)"
                if tail_slope <= 0.5 * head_slope
                else "still rising (unbounded)"
            )
            lines.append(
                f"{label} p50: {vals[0]:.0f}→{vals[-1]:.0f}ms ({total:.1f}× over "
                f"{rows[0].size}→{rows[-1].size}) — tail slope {tail_slope:.2f} vs "
                f"head {head_slope:.2f} ms/doc ⇒ {shape}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="arango_memory.eval.scaling_profile")
    parser.add_argument("--max", type=int, default=3000, help="memories to ingest into one tenant")
    parser.add_argument("--step", type=int, default=500, help="checkpoint interval")
    parser.add_argument("--probes", type=int, default=20, help="retrieve samples per checkpoint")
    args = parser.parse_args(argv)
    db = ArangoMemoryClient().connect()
    ensure_schema(db)
    rows = profile(db, max_n=args.max, step=args.step, probes=args.probes, progress=True)
    print(_format(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
