"""HX-2 recall-vs-corpus-size curve — does fusion hold recall as the corpus grows?

This is the project's thesis as a chart (and the direct answer to a graph-memory competitor's
headline "recall degrades with scale" figure): as an open corpus grows, **graph+vector+BM25
fusion holds recall while a pure-vector arm degrades**.

Honest design — *fix the scored probe set, grow the distractors*, so the x-axis isolates the
size effect (not the question mix):
  - Reserve a fixed set of `--probes` questions and their gold paragraphs; ingest those first
    so every probe's evidence is always present.
  - At each checkpoint ingest `--step` more **distractor** paragraphs (from other questions).
  - Re-measure the probe set's recall-frac under three arm configs at each checkpoint.

Three lines, isolated by RRF weights (retrieve reads `settings.rrf_*` live):
  - **fused** — bm25 + vector (the full stack; graph is off here, see below).
  - **bm25** — vector arm zeroed.
  - **vector** — bm25 arm zeroed (the "VectorDB" baseline; needs `rrf_bm25_weight`, HX-2).

Tractability: distractors are ingested with `extract=False` (the BX-3 lever) so entity
resolution doesn't go ~O(n²) and the graph arm is absent — the sweep runs in minutes, and the
"fused" line is BM25+vector fusion (the meaningful vector-vs-fusion contrast; a graph-on figure
on a small corpus is a separate exercise). Input is a **pooled** MuSiQue file
(`musique_convert --pooled`). Keyless CI proves the plumbing (FakeEmbedder has no real
semantics); the plotted curve is a bring-your-own real-embedding run.

    python -m arango_memory.eval.musique_convert musique.jsonl pooled.json --limit 500 --pooled
    python -m arango_memory.eval.recall_curve pooled.json --probes 50 --step 250 --csv curve.csv
    python -m arango_memory.eval.plot_recall_curve curve.csv curve.png
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from arango.database import StandardDatabase

from ..client import ArangoMemoryClient
from ..config import settings
from ..ingest.store import store
from ..retrieve.search import retrieve
from ..schema.collections import ensure_schema
from ..telemetry.logging import configure_logging
from .locomo import QA, Sample, Turn, _normalize, _recall_fraction, load_dataset

#: arm label → (rrf_bm25_weight, rrf_vector_weight). Graph is off throughout (extract=False).
ARM_WEIGHTS: dict[str, tuple[float, float]] = {
    "fused": (1.0, 1.0),
    "bm25": (1.0, 0.0),
    "vector": (0.0, 1.0),
}


@dataclass(frozen=True)
class CurvePoint:
    corpus_size: int
    arm: str
    recall_frac: float  # mean fraction of each probe's support set retrieved
    recall_hit: float  # all-hops-present rate over the probe set


@contextmanager
def _arm(bm25_weight: float, vector_weight: float) -> Iterator[None]:
    """Temporarily override the arm RRF weights (retrieve reads them live)."""
    saved = (settings.rrf_bm25_weight, settings.rrf_vector_weight)
    settings.rrf_bm25_weight, settings.rrf_vector_weight = bm25_weight, vector_weight
    try:
        yield
    finally:
        settings.rrf_bm25_weight, settings.rrf_vector_weight = saved


def _norm(text: str) -> str:
    return " ".join(_normalize(text))


def split_probe_distractor(
    sample: Sample, n_probes: int
) -> tuple[list[QA], list[Turn], list[Turn]]:
    """Fixed probe QAs + the turns that carry their gold (always ingested) vs the rest.

    A turn is 'gold' if any probe support fact is a substring of it (mirrors `_recall_hit`),
    so every probe's evidence is guaranteed present; all other turns are distractors."""
    probe_qa = [qa for qa in sample.qa if qa.support()][:n_probes]
    gold_norms = {_norm(fact) for qa in probe_qa for fact in qa.support()}
    turns = sample.sessions[0] if sample.sessions else []
    gold_turns: list[Turn] = []
    distractor_turns: list[Turn] = []
    for turn in turns:
        t_norm = _norm(turn.text)
        if any(g in t_norm for g in gold_norms):
            gold_turns.append(turn)
        else:
            distractor_turns.append(turn)
    return probe_qa, gold_turns, distractor_turns


def _ingest(
    db: StandardDatabase, turns: Sequence[Turn], *, tenant: str, agent_id: str, start: int
) -> int:
    """Ingest turns with extract=False (no entity resolution, no graph — BX-3). Returns the
    next turn_index."""
    ti = start
    for turn in turns:
        store(
            db,
            content=f"{turn.speaker}: {turn.text}",
            tenant_id=tenant,
            agent_id=agent_id,
            turn_index=ti,
            extract=False,
        )
        ti += 1
    return ti


def _await_consistency(
    db: StandardDatabase, probe: str, *, tenant: str, agent_id: str, attempts: int, delay: float
) -> None:
    for _ in range(attempts):
        if retrieve(
            db, query=probe, tenant_id=tenant, agent_id=agent_id, graph_hops=0
        ).hits:
            return
        time.sleep(delay)


def _measure(
    db: StandardDatabase, probe_qa: Sequence[QA], *, tenant: str, agent_id: str, k: int
) -> tuple[float, float]:
    """Mean recall-frac + all-hops-present rate of the probe set at the current corpus size."""
    if not probe_qa:
        return 0.0, 0.0
    fracs: list[float] = []
    hits: list[float] = []
    for qa in probe_qa:
        retrieved = retrieve(
            db, query=qa.question, tenant_id=tenant, agent_id=agent_id, k=k, graph_hops=0
        )
        support = qa.support()
        frac = _recall_fraction([h.text for h in retrieved.hits], support)
        fracs.append(frac)
        hits.append(1.0 if support and frac == 1.0 else 0.0)
    n = len(probe_qa)
    return sum(fracs) / n, sum(hits) / n


def run_curve(
    db: StandardDatabase,
    sample: Sample,
    *,
    n_probes: int = 50,
    step: int = 250,
    max_distractors: int | None = None,
    k: int = 10,
    agent_id: str = "recall-curve",
    consistency_attempts: int = 30,
    consistency_delay: float = 0.25,
    progress: bool = False,
) -> list[CurvePoint]:
    """Ingest gold first, then distractors in `step` increments; measure each arm per step."""
    tenant = sample.sample_id
    probe_qa, gold_turns, distractor_turns = split_probe_distractor(sample, n_probes)
    if max_distractors is not None:
        distractor_turns = distractor_turns[:max_distractors]

    ti = _ingest(db, gold_turns, tenant=tenant, agent_id=agent_id, start=0)

    # Checkpoints over the distractor count: 0, step, 2·step, …, then the exact total.
    total = len(distractor_turns)
    checkpoints = list(range(0, total + 1, max(step, 1)))
    if checkpoints[-1] != total:
        checkpoints.append(total)

    points: list[CurvePoint] = []
    prev = 0
    probe_query = probe_qa[0].question if probe_qa else ""
    for cp in checkpoints:
        ti = _ingest(
            db, distractor_turns[prev:cp], tenant=tenant, agent_id=agent_id, start=ti
        )
        prev = cp
        if probe_query:
            _await_consistency(
                db, probe_query, tenant=tenant, agent_id=agent_id,
                attempts=consistency_attempts, delay=consistency_delay,
            )
        corpus_size = len(gold_turns) + cp
        fracs: dict[str, float] = {}
        for arm, (bm25_w, vector_w) in ARM_WEIGHTS.items():
            with _arm(bm25_w, vector_w):
                frac, hit = _measure(db, probe_qa, tenant=tenant, agent_id=agent_id, k=k)
            fracs[arm] = frac
            points.append(CurvePoint(corpus_size, arm, frac, hit))
        if progress:
            print(
                f"corpus={corpus_size}: fused={fracs.get('fused', 0.0):.3f} "
                f"vector={fracs.get('vector', 0.0):.3f} ({len(probe_qa)} probes)",
                file=sys.stderr, flush=True,
            )
    return points


def write_csv(points: Sequence[CurvePoint], path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["corpus_size", "arm", "recall_frac", "recall_hit"])
        writer.writeheader()
        for p in points:
            writer.writerow(asdict(p))


def _format(points: Sequence[CurvePoint]) -> str:
    sizes = sorted({p.corpus_size for p in points})
    arms = list(ARM_WEIGHTS)
    lines = ["corpus_size  " + "  ".join(f"{a:>7}" for a in arms)]
    by_key = {(p.corpus_size, p.arm): p.recall_frac for p in points}
    for size in sizes:
        cells = "  ".join(f"{by_key.get((size, a), 0.0):7.3f}" for a in arms)
        lines.append(f"{size:>11}  {cells}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.eval.recall_curve")
    parser.add_argument("dataset", help="a POOLED MuSiQue dataset (musique_convert --pooled)")
    parser.add_argument("--probes", type=int, default=50, help="fixed scored question set size")
    parser.add_argument("--step", type=int, default=250, help="distractors added per checkpoint")
    parser.add_argument("--max-distractors", type=int, default=None, help="cap the corpus growth")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--csv", default=None, help="also write the raw rows to this CSV")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)
    db = ArangoMemoryClient().connect()
    ensure_schema(db)
    samples = load_dataset(args.dataset)
    if not samples:
        print("no samples in dataset", file=sys.stderr)
        return 1
    points = run_curve(
        db, samples[0], n_probes=args.probes, step=args.step,
        max_distractors=args.max_distractors, k=args.k, progress=True,
    )
    print(_format(points))
    if args.csv:
        write_csv(points, args.csv)
        print(f"\nrows → {args.csv}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
