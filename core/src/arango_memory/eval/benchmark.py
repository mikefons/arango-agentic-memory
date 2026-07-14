"""Full LoCoMo benchmark runner (DESIGN.md §23, Step 7c).

Aggregates the per-sample eval into overall + per-category metrics and compares
them to the §23 targets. The real LoCoMo dataset is a manual/nightly BYO run
(large, externally licensed); the runner is tested on the smoke slice.

Covers the retrieval-side, deterministically computable metrics (token-F1,
Recall@k, Deducible/per-category, tokens-injected). Hallucination Rate and Noise
Reduction Rate need a generated answer + a judge (the full agent loop) — a
separate harness, out of scope here.

CLI: `python -m arango_memory.eval.benchmark <dataset.json> [--mode] [--k]`
(exits nonzero if below targets, so it can gate a nightly run).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from arango.database import StandardDatabase

from ..client import ArangoMemoryClient
from ..schema.collections import ensure_schema
from ..telemetry import latency
from ..telemetry.logging import configure_logging
from .locomo import QuestionScore, Sample, load_dataset, run_eval

# §23 targets.
F1_MIN = 0.65
TOKENS_MAX = 1500
RECALL_MIN = 0.6
# §23 retrieval p99 latency targets (ms), by recorder key. Informational on a manual
# run (wall-clock is environment-dependent) — quality metrics remain the pass/fail gate.
LATENCY_P99_TARGETS_MS = {"retrieval.lite": 250.0, "retrieval.full": 1500.0}


@dataclass
class BenchmarkReport:
    n_questions: int
    recall_at_k: float
    mean_f1: float
    mean_tokens: float
    per_category: dict[str, dict[str, float]] = field(default_factory=dict)
    latency_ms: dict[str, dict[str, float]] = field(default_factory=dict)
    passed: bool = False
    failures: list[str] = field(default_factory=list)


def _evaluate_targets(
    recall_at_k: float, mean_f1: float, mean_tokens: float
) -> tuple[bool, list[str]]:
    """Compare aggregates to the §23 targets. Returns (passed, failures)."""
    failures: list[str] = []
    if mean_f1 < F1_MIN:
        failures.append(f"token-F1 {mean_f1:.3f} < {F1_MIN}")
    if mean_tokens > TOKENS_MAX:
        failures.append(f"tokens/turn {mean_tokens:.0f} > {TOKENS_MAX}")
    if recall_at_k < RECALL_MIN:
        failures.append(f"Recall@k {recall_at_k:.3f} < {RECALL_MIN}")
    return not failures, failures


def _aggregate(scores: Sequence[QuestionScore], field_name: str) -> float:
    if not scores:
        return 0.0
    return sum(float(getattr(s, field_name)) for s in scores) / len(scores)


def run_benchmark(
    db: StandardDatabase,
    samples: Sequence[Sample],
    *,
    mode: str = "lite",
    k: int = 10,
    progress: bool = False,
) -> BenchmarkReport:
    """Run every sample, aggregate to overall + per-category metrics vs §23 targets.

    `progress=True` prints a per-sample line to stderr (the run is otherwise silent
    for minutes on real data) — stdout stays clean for the final report.
    """
    latency.clear()  # capture only this run's retrieval latency (§23)
    scores: list[QuestionScore] = []
    total = len(samples)
    for i, sample in enumerate(samples, 1):
        turns = sum(len(session) for session in sample.sessions)
        if progress:
            print(
                f"[{i}/{total}] {sample.sample_id}: ingesting {turns} turns, "
                f"scoring {len(sample.qa)} questions…",
                file=sys.stderr, flush=True,
            )
        result = run_eval(db, sample, mode=mode, k=k)
        scores.extend(result.questions)
        if progress:
            print(
                f"[{i}/{total}] {sample.sample_id}: done — "
                f"recall@k={result.recall_at_k:.2f} ({len(scores)} questions scored so far)",
                file=sys.stderr, flush=True,
            )

    recall = _aggregate(scores, "recall_hit")
    mean_f1 = _aggregate(scores, "f1")
    mean_tokens = _aggregate(scores, "tokens_injected")

    per_category: dict[str, dict[str, float]] = {}
    for category in sorted({s.category for s in scores if s.category}):
        bucket = [s for s in scores if s.category == category]
        per_category[category] = {
            "recall_at_k": _aggregate(bucket, "recall_hit"),
            "mean_f1": _aggregate(bucket, "f1"),
            "n": float(len(bucket)),
        }

    passed, failures = _evaluate_targets(recall, mean_f1, mean_tokens)
    return BenchmarkReport(
        n_questions=len(scores),
        recall_at_k=recall,
        mean_f1=mean_f1,
        mean_tokens=mean_tokens,
        per_category=per_category,
        latency_ms=latency.snapshot(),
        passed=passed,
        failures=failures,
    )


def _format(report: BenchmarkReport) -> str:
    lines = [
        f"questions:     {report.n_questions}",
        f"Recall@k:      {report.recall_at_k:.3f}  (target ≥ {RECALL_MIN})",
        f"token-F1:      {report.mean_f1:.3f}  (target ≥ {F1_MIN})",
        f"tokens/turn:   {report.mean_tokens:.0f}  (target ≤ {TOKENS_MAX})",
    ]
    for category, m in report.per_category.items():
        lines.append(
            f"  [{category}] recall={m['recall_at_k']:.3f} f1={m['mean_f1']:.3f} n={m['n']:.0f}"
        )
    if report.latency_ms:
        lines.append("latency (ms, informational):")
        for op, pcts in sorted(report.latency_ms.items()):
            target = LATENCY_P99_TARGETS_MS.get(op)
            note = (
                f"  (target p99 ≤ {target:.0f})" if target else ""
            )
            lines.append(
                f"  {op}: p50={pcts['p50']:.0f} p95={pcts['p95']:.0f} "
                f"p99={pcts['p99']:.0f} n={pcts['count']:.0f}{note}"
            )
    lines.append("PASS" if report.passed else "FAIL: " + "; ".join(report.failures))
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.eval.benchmark")
    parser.add_argument("dataset", help="path to a LoCoMo-style dataset JSON")
    parser.add_argument("--mode", choices=["lite", "full"], default="lite")
    parser.add_argument("--k", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Install the logging formatter (MA-8) so a `retrieve degraded` prints the real AQL
    # reason instead of vanishing behind Python's bare fallback handler.
    configure_logging()
    args = _build_parser().parse_args(argv)
    db = ArangoMemoryClient().connect()
    ensure_schema(db)
    report = run_benchmark(db, load_dataset(args.dataset), mode=args.mode, k=args.k, progress=True)
    print(_format(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
