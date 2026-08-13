"""LongMemEval benchmark runner (HX-1, DESIGN.md §23).

Where `benchmark.py` scores *retrieval* (Recall@k) on LoCoMo/MuSiQue, this harness scores
**end-to-end answer accuracy** on LongMemEval — the metric the long-term-memory field (and
competing products) actually report. For each question it ingests the multi-session history
(tenant = question, so distractors are bounded), retrieves + answers from memory, and an
**LLM judge** grades the answer against the gold answer → accuracy, overall and per
question-type. Abstention questions (unanswerable by construction) are judged for whether
the model correctly *declines*.

Both the answerer and the judge are injectable `Generator`s, so CI runs **keyless** on
`FakeGenerator` and a real run uses the configured provider (Haiku by default). The full
scored run is a bring-your-own dataset (large, externally licensed); the runner is tested on
the smoke slice.

Ingestion skips entity extraction by default (`--extract` to opt in): a LongMemEval history is
hundreds of turns and per-turn entity resolution over the growing tenant is ~O(n²) (the BX-2
wall) — the dominant cost of a real run — while the entity graph adds ~nothing to answer
accuracy. Skipping it turns a many-hour run into a tractable one.

CLI: `python -m arango_memory.eval.longmemeval <lme.json> [--mode] [--k] [--rerank] [--extract]
[--min-accuracy X]` (exits nonzero below a gate, so a nightly run can fail the build).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from arango.database import StandardDatabase

from ..client import ArangoMemoryClient
from ..generation import Generator, get_generator
from ..ingest.store import store
from ..retrieve.search import retrieve
from ..schema.collections import ensure_schema
from ..telemetry.logging import configure_logging
from .halu import generate_answer
from .locomo import Sample, load_dataset

_JUDGE_SYSTEM = (
    "You grade a model's answer against the gold answer for a question. Reply with exactly "
    "one word: CORRECT if the model's answer conveys the same information as the gold answer "
    "(paraphrase and extra correct detail are fine), or INCORRECT if it is wrong, missing, or "
    "contradicts the gold answer."
)

_ABSTAIN_JUDGE_SYSTEM = (
    "The question cannot be answered from the memory provided to the model. Reply with exactly "
    "one word: CORRECT if the model's answer declines — it says it does not know or that the "
    "information is not available — or INCORRECT if it asserts a specific answer anyway."
)


def judge_correct(
    question: str, gold: str, answer: str, *, judge: Generator, abstention: bool = False
) -> bool:
    """LLM judge → is the answer correct? Abstention questions grade a correct *decline*.

    Parsed by token so the INCORRECT⊃CORRECT substring never yields a false positive;
    unparseable output scores as incorrect (never breaks the run)."""
    if abstention:
        prompt = f"Question: {question}\nModel answer: {answer}"
        system = _ABSTAIN_JUDGE_SYSTEM
    else:
        prompt = f"Question: {question}\nGold answer: {gold}\nModel answer: {answer}"
        system = _JUDGE_SYSTEM
    try:
        labels = judge.complete(prompt, system=system).strip().upper().split()
    except Exception:  # noqa: BLE001 — a judge hiccup scores 0, never breaks the run
        return False
    return "CORRECT" in labels and "INCORRECT" not in labels


@dataclass(frozen=True)
class LongMemScore:
    question_id: str
    question_type: str
    correct: bool
    abstention: bool
    answer: str


@dataclass
class LongMemReport:
    n_questions: int
    accuracy: float
    per_type: dict[str, dict[str, float]] = field(default_factory=dict)
    abstention_accuracy: float | None = None  # over abstention questions only, if any
    scores: list[LongMemScore] = field(default_factory=list)
    passed: bool = True
    failures: list[str] = field(default_factory=list)


def _ingest_sample(
    db: StandardDatabase, sample: Sample, agent_id: str, *, attempts: int, delay: float,
    extract: bool,
) -> None:
    """Ingest a question's sessions (tenant = sample_id), then wait for search visibility.

    Ingest-only (unlike `locomo.run_eval`, which also scores + generates) so a real run
    doesn't pay a second, throwaway answer per question. `extract=False` (default) skips
    entity extraction + resolution: a LongMemEval history is hundreds of turns, and per-turn
    resolution against the growing tenant is ~O(n²) (the BX-2 wall) — the dominant cost of a
    real run. LongMemEval scores *answers* via BM25+vector retrieval, so the entity graph adds
    ~nothing here; skipping it is both correct and a large speedup (§23)."""
    turn_index = 0
    for session in sample.sessions:
        for turn in session:
            store(
                db,
                content=f"{turn.speaker}: {turn.text}",
                tenant_id=sample.sample_id,
                agent_id=agent_id,
                turn_index=turn_index,
                extract=extract,
            )
            turn_index += 1
    if not sample.qa:
        return
    probe = sample.qa[0].question
    for _ in range(attempts):
        if retrieve(db, query=probe, tenant_id=sample.sample_id, agent_id=agent_id).hits:
            return
        time.sleep(delay)


def run_longmemeval(
    db: StandardDatabase,
    samples: Sequence[Sample],
    *,
    generator: Generator | None = None,
    judge: Generator | None = None,
    agent_id: str = "assistant",
    mode: str = "lite",
    k: int = 10,
    rerank: bool = False,
    extract: bool = False,
    min_accuracy: float | None = None,
    consistency_attempts: int = 30,
    consistency_delay: float = 0.25,
    progress: bool = False,
) -> LongMemReport:
    """Ingest each question's history, answer from memory, judge accuracy; aggregate.

    `extract=False` (default) skips the ~O(n²) entity resolution over each question's long
    history — the dominant cost of a real run (see `_ingest_sample`)."""
    gen = generator or get_generator()
    jdg = judge or gen
    scores: list[LongMemScore] = []
    total = len(samples)
    for i, sample in enumerate(samples, 1):
        turns = sum(len(session) for session in sample.sessions)
        if progress:
            print(
                f"[{i}/{total}] {sample.sample_id}: ingesting {turns} turns "
                f"({len(sample.sessions)} sessions)…",
                file=sys.stderr, flush=True,
            )
        _ingest_sample(
            db, sample, agent_id, attempts=consistency_attempts, delay=consistency_delay,
            extract=extract,
        )
        for qa in sample.qa:
            retrieved = retrieve(
                db, query=qa.question, tenant_id=sample.sample_id,
                agent_id=agent_id, mode=mode, k=k, rerank=rerank,
            )
            answer = generate_answer(qa.question, retrieved.context, generator=gen)
            correct = judge_correct(
                qa.question, qa.answer, answer, judge=jdg, abstention=qa.abstention
            )
            scores.append(
                LongMemScore(
                    question_id=sample.sample_id,
                    question_type=qa.category or "unknown",
                    correct=correct,
                    abstention=qa.abstention,
                    answer=answer,
                )
            )
        if progress:
            running = sum(s.correct for s in scores) / len(scores)
            print(
                f"[{i}/{total}] {sample.sample_id}: done — "
                f"accuracy={running:.2f} ({len(scores)} scored so far)",
                file=sys.stderr, flush=True,
            )

    return _aggregate(scores, min_accuracy=min_accuracy)


def _aggregate(
    scores: list[LongMemScore], *, min_accuracy: float | None
) -> LongMemReport:
    n = len(scores)
    accuracy = sum(s.correct for s in scores) / n if n else 0.0

    per_type: dict[str, dict[str, float]] = {}
    for qtype in sorted({s.question_type for s in scores}):
        bucket = [s for s in scores if s.question_type == qtype]
        per_type[qtype] = {
            "accuracy": sum(s.correct for s in bucket) / len(bucket),
            "n": float(len(bucket)),
        }

    abstention = [s for s in scores if s.abstention]
    abstention_accuracy = (
        sum(s.correct for s in abstention) / len(abstention) if abstention else None
    )

    failures: list[str] = []
    if min_accuracy is not None and accuracy < min_accuracy:
        failures.append(f"accuracy {accuracy:.3f} < {min_accuracy}")

    return LongMemReport(
        n_questions=n,
        accuracy=accuracy,
        per_type=per_type,
        abstention_accuracy=abstention_accuracy,
        scores=scores,
        passed=not failures,
        failures=failures,
    )


def _format(report: LongMemReport, *, gated: bool) -> str:
    lines = [
        f"questions:   {report.n_questions}",
        f"Accuracy:    {report.accuracy:.3f}",
    ]
    if report.abstention_accuracy is not None:
        lines.append(f"abstention:  {report.abstention_accuracy:.3f}  (correct-decline rate)")
    for qtype, m in report.per_type.items():
        lines.append(f"  [{qtype}] accuracy={m['accuracy']:.3f} n={m['n']:.0f}")
    if gated:
        lines.append("PASS" if report.passed else "FAIL: " + "; ".join(report.failures))
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.eval.longmemeval")
    parser.add_argument("dataset", help="path to a converted LongMemEval dataset JSON")
    parser.add_argument("--mode", choices=["lite", "full", "multihop"], default="lite")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--rerank", action="store_true",
                        help="cross-encoder rerank the fused pool (RQ-2b; needs a reranker "
                             "provider — set RERANKER_PROVIDER=local + the 'rerank' extra)")
    parser.add_argument("--min-accuracy", type=float, default=None,
                        help="fail (nonzero exit) if overall accuracy is below this")
    parser.add_argument("--extract", action="store_true",
                        help="build the entity graph while ingesting (default off). LongMemEval "
                             "scores answers, so the graph adds little here and per-turn "
                             "resolution over a long history is ~O(n²) — leave off unless testing")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Surface real degradation reasons during the run (MA-8).
    configure_logging()
    args = _build_parser().parse_args(argv)
    db = ArangoMemoryClient().connect()
    ensure_schema(db)
    report = run_longmemeval(
        db, load_dataset(args.dataset), mode=args.mode, k=args.k,
        rerank=args.rerank, extract=args.extract, min_accuracy=args.min_accuracy, progress=True,
    )
    print(_format(report, gated=args.min_accuracy is not None))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
