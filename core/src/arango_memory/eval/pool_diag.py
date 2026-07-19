"""RQ-2a retrieval-miss diagnostic: split recall misses into *ranking* vs *first-stage*.

For every support item a question needs, classify it against the lite single-shot fused
candidate pool (`diagnose_pool`, pre-MMR/truncation):

    in the top-k hits?          → HIT
    else in the fused pool?     → RANKING miss   (the gold is retrieved but ranked below
                                                  top-k — a reranker can recover it)
    else (absent from the pool) → RECALL  miss   (no arm surfaced it — first-stage
                                                  retrieval must improve)

The ranking-vs-recall split of the misses is the whole point: it picks RQ-2b's lever
(cross-encoder reranker vs query expansion / `prospective_queries`). Read-only.

    python -m arango_memory.eval.pool_diag musique.json [--k 10] [--pool 100]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from arango.database import StandardDatabase

from ..client import ArangoMemoryClient
from ..ingest.store import store
from ..retrieve.search import diagnose_pool, retrieve
from ..schema.collections import ensure_schema
from ..telemetry.logging import configure_logging
from .locomo import Sample, _await_consistency, _recall_hit, load_dataset


@dataclass
class MissBreakdown:
    """Support-item counts by outcome (a question contributes one per gold fact)."""

    hit: int = 0
    ranking: int = 0  # in the fused pool but below top-k → a reranker helps
    recall: int = 0  # absent from the pool → first-stage retrieval must improve

    @property
    def items(self) -> int:
        return self.hit + self.ranking + self.recall

    @property
    def misses(self) -> int:
        return self.ranking + self.recall


def classify(
    top_texts: Sequence[str], pool_texts: Sequence[str], support: Sequence[str]
) -> list[str]:
    """Label each support item HIT / ranking / recall (see module docstring)."""
    labels: list[str] = []
    for item in support:
        if _recall_hit(top_texts, item):
            labels.append("hit")
        elif _recall_hit(pool_texts, item):
            labels.append("ranking")
        else:
            labels.append("recall")
    return labels


def diagnose(
    db: StandardDatabase,
    samples: Sequence[Sample],
    *,
    agent_id: str = "assistant",
    k: int = 10,
    pool: int = 100,
    progress: bool = False,
) -> dict[str, MissBreakdown]:
    """Ingest each sample, then classify every question's support items. Returns the
    breakdown keyed by category, plus `"__all__"` for the overall totals."""
    overall = MissBreakdown()
    by_category: dict[str, MissBreakdown] = {}
    total = len(samples)
    for i, sample in enumerate(samples, 1):
        turn_index = 0
        for session in sample.sessions:
            for turn in session:
                store(
                    db,
                    content=f"{turn.speaker}: {turn.text}",
                    tenant_id=sample.sample_id,
                    agent_id=agent_id,
                    turn_index=turn_index,
                )
                turn_index += 1
        _await_consistency(db, sample, agent_id, attempts=30, delay=0.25)
        if progress:
            print(f"[{i}/{total}] {sample.sample_id}: diagnosing {len(sample.qa)} questions…",
                  file=sys.stderr, flush=True)
        for qa in sample.qa:
            top = retrieve(db, query=qa.question, tenant_id=sample.sample_id,
                           agent_id=agent_id, k=k)
            pool_hits = diagnose_pool(db, query=qa.question, tenant_id=sample.sample_id,
                                      agent_id=agent_id, candidate_pool=pool)
            top_texts = [h.text for h in top.hits]
            pool_texts = [h.text for h in pool_hits]
            bucket = by_category.setdefault(qa.category or "uncategorized", MissBreakdown())
            for label in classify(top_texts, pool_texts, qa.support()):
                setattr(overall, label, getattr(overall, label) + 1)
                setattr(bucket, label, getattr(bucket, label) + 1)
    return {"__all__": overall, **by_category}


def _split(b: MissBreakdown) -> str:
    if not b.misses:
        return "no misses"
    return (f"ranking {b.ranking} ({b.ranking / b.misses:.0%}) / "
            f"recall {b.recall} ({b.recall / b.misses:.0%})")


def _format(result: dict[str, MissBreakdown]) -> str:
    overall = result["__all__"]
    lines = [
        f"support items: {overall.items}  (hit {overall.hit} / miss {overall.misses})",
        f"of misses:     {_split(overall)}",
    ]
    if overall.misses:
        lever = ("reranker (misses are mostly in-pool)"
                 if overall.ranking >= overall.recall
                 else "first-stage recall — query expansion / prospective_queries")
        lines.append(f"→ RQ-2b lever: {lever}")
    for category in sorted(k for k in result if k != "__all__"):
        b = result[category]
        lines.append(f"  [{category}] items={b.items} hit={b.hit} miss={b.misses} → {_split(b)}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.eval.pool_diag")
    parser.add_argument("dataset", help="path to a converted dataset JSON (e.g. musique.json)")
    parser.add_argument("--k", type=int, default=10, help="top-k the real retrieve returns")
    parser.add_argument("--pool", type=int, default=100, help="fused candidate pool size")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)
    db = ArangoMemoryClient().connect()
    ensure_schema(db)
    result = diagnose(db, load_dataset(args.dataset), k=args.k, pool=args.pool, progress=True)
    print(_format(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
