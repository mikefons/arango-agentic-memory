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
[--concurrency N] [--min-accuracy X]` (exits nonzero below a gate, so a nightly run can fail the
build). `--concurrency N` processes N questions at once — each is an isolated tenant, so the
overlap hides per-question LLM latency (the wall of a graph-on/haiku run).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arango.database import StandardDatabase

from ..client import ArangoMemoryClient
from ..config import settings
from ..generation import Generator, get_generator
from ..ingest.store import StoreItem, store_many
from ..retrieve.search import _event_sort_key, force_view_sync, retrieve
from ..schema.collections import ensure_schema
from ..security.redact import redact
from ..telemetry.logging import configure_logging, logger
from .halu import generate_answer
from .locomo import Sample, _normalize, load_dataset

#: RQ-3 rerank scoring modes a `--rerank-scoring` variant may name (see `search._head_scores`).
SCORINGS = ("replace", "rrf", "event_time")

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
    variant: str = ""  # RQ-3 rerank-scoring variant ("" = the single legacy configuration)
    # Evidence metrics (RQ-3; None when the dataset carries no `qa.evidence`):
    evidence_recall: float | None = None  # fraction of evidence sessions with a turn in top-k
    newest_in_topk: bool | None = None  # KU: the newest evidence session reached top-k
    newest_above_stale: bool | None = None  # KU: …and ranks above every stale evidence session
    answer_error: bool = False  # the answer LLM call failed (scored incorrect, unpaired)


@dataclass
class LongMemReport:
    n_questions: int
    accuracy: float
    per_type: dict[str, dict[str, float]] = field(default_factory=dict)
    abstention_accuracy: float | None = None  # over abstention questions only, if any
    scores: list[LongMemScore] = field(default_factory=list)
    passed: bool = True
    failures: list[str] = field(default_factory=list)
    variant: str = ""
    judged: bool = True  # False for a --retrieval-only run: accuracy is not measured
    evidence: dict[str, float] = field(default_factory=dict)  # summary of the evidence metrics
    answer_errors: int = 0  # answer calls that failed (transient LLM faults) — see LongMemScore
    by_variant: dict[str, LongMemReport] = field(default_factory=dict)  # RQ-3 multi-variant run
    paired: list[dict[str, Any]] = field(default_factory=list)  # each variant vs the first


@dataclass(frozen=True)
class _Evidence:
    recall: float | None = None
    newest_in_topk: bool | None = None
    newest_above_stale: bool | None = None


def _parse_variant(spec: str) -> tuple[str | None, float | None]:
    """`"event_time:0.2"` → ("event_time", 0.2); `"rrf"` → ("rrf", None); `""` → (None, None)."""
    if not spec:
        return None, None
    name, _, weight = spec.partition(":")
    if name not in SCORINGS:
        raise ValueError(f"unknown rerank scoring {name!r} (choose from {', '.join(SCORINGS)})")
    return name, float(weight) if weight else None


def _evidence_metrics(
    hit_texts: Sequence[str], evidence: Sequence[dict[str, str]], *, ordering: bool
) -> _Evidence:
    """Score which labelled evidence retrieval surfaced (RQ-3). A turn is retrieved at rank r if
    its (redacted, normalized) text is contained in hit r — the same substring rule as
    `locomo._recall_hit`. Sessions take their best turn rank.

    `ordering` (knowledge-update only — there the newest evidence session holds the current
    fact): is the newest session in top-k, and ranked above every stale evidence session? Left
    None when fewer than two evidence sessions carry distinct parseable times."""
    hits = [" ".join(_normalize(t)) for t in hit_texts]
    ranks: dict[str, list[int]] = {}
    times: dict[str, str] = {}
    for ev in evidence:
        text = ev.get("text", "")
        if settings.redact_pii:  # stored memories are redacted; match against the same form
            text = redact(text, mode="lite", generator=None)
        needle = " ".join(_normalize(text))
        sid = ev.get("session_id", "")
        times[sid] = ev.get("event_time", "")
        found = ranks.setdefault(sid, [])
        rank = next((i for i, h in enumerate(hits) if needle and needle in h), None)
        if rank is not None:
            found.append(rank)
    if not ranks:
        return _Evidence()
    best = {sid: min(r) if r else None for sid, r in ranks.items()}
    recall = sum(r is not None for r in best.values()) / len(best)
    if not ordering:
        return _Evidence(recall=recall)

    keyed = [(k, sid) for sid in best if (k := _event_sort_key(times[sid])) is not None]
    if len({k for k, _ in keyed}) < 2:
        return _Evidence(recall=recall)
    newest_key = max(k for k, _ in keyed)
    newest = [r for k, sid in keyed if k == newest_key and (r := best[sid]) is not None]
    stale = [r for k, sid in keyed if k != newest_key and (r := best[sid]) is not None]
    if not newest:
        return _Evidence(recall=recall, newest_in_topk=False, newest_above_stale=False)
    above = not stale or min(newest) < min(stale)
    return _Evidence(recall=recall, newest_in_topk=True, newest_above_stale=above)


def _mcnemar(base: Sequence[bool], other: Sequence[bool]) -> dict[str, float]:
    """Exact (binomial) McNemar test on paired outcomes. `gain` = base wrong / other right,
    `loss` = base right / other wrong; only discordant pairs carry information."""
    gain = sum(1 for b, o in zip(base, other, strict=True) if not b and o)
    loss = sum(1 for b, o in zip(base, other, strict=True) if b and not o)
    n = gain + loss
    tail = sum(math.comb(n, i) for i in range(min(gain, loss) + 1)) / 2**n if n else 1.0
    return {"gain": gain, "loss": loss, "n": len(base), "p": min(1.0, 2 * tail) if n else 1.0}


def _ingest_sample(
    db: StandardDatabase, sample: Sample, agent_id: str, *, extract: bool,
) -> None:
    """Ingest a question's whole history in one batched `store_many` call (IN-5), then force the
    search view consistent so retrieval sees the writes.

    One bulk call replaces the per-turn `store()` loop + the old embedding pre-warm: `store_many`
    batch-embeds and bulk-inserts the record (IN-1), and — when `extract=True` — runs the batched
    graph pass (IN-2), which makes the entity graph affordable at LongMemEval's 500-turn histories
    (per-turn `extract=True` was the ~O(n²) BX-2 wall). Each turn's session date rides as
    `event_time` (IN-4), surfaced in the retrieved context without diluting the matched text.

    `store_many` writes synchronously; the only gap to retrieval is the ArangoSearch view's
    eventual consistency, so one `force_view_sync` makes the batch visible deterministically —
    versus the old sleep-poll that cost up to attempts×delay per question of pure waiting."""
    items = [
        StoreItem(content=f"{turn.speaker}: {turn.text}", turn_index=i,
                  event_time=turn.event_time)
        for i, turn in enumerate(t for session in sample.sessions for t in session)
    ]
    if items:
        store_many(db, items, tenant_id=sample.sample_id, agent_id=agent_id, extract=extract)
        force_view_sync(db, sample.sample_id)


def _process_sample(
    db: StandardDatabase,
    sample: Sample,
    *,
    gen: Generator,
    jdg: Generator,
    agent_id: str,
    mode: str,
    k: int,
    rerank: bool,
    extract: bool,
    variants: Sequence[str] = ("",),
    judge_answers: bool = True,
) -> list[LongMemScore]:
    """Ingest one question's history ONCE, then retrieve (+ answer + judge) each QA under every
    rerank-scoring variant. Self-contained on the given `db` and the question's own tenant
    (`sample.sample_id`), so distinct samples never touch shared state — the unit of
    cross-question parallelism (`concurrency`).

    Variants are evaluated against the same ingest — the paired design RQ-3 needs, and required
    anyway: re-ingesting into the same DB would double-count graph beliefs (`store_many` has no
    replay guard). The default `("",)` is the single legacy configuration."""
    _ingest_sample(db, sample, agent_id, extract=extract)
    out: list[LongMemScore] = []
    for qa in sample.qa:
        for variant in variants:
            scoring, weight = _parse_variant(variant)
            retrieved = retrieve(
                db, query=qa.question, tenant_id=sample.sample_id,
                agent_id=agent_id, mode=mode, k=k, rerank=rerank or scoring is not None,
                rerank_scoring=scoring, rerank_time_weight=weight,
                # Variants compare scorings on identical state: a read-only probe, so one
                # variant's spaced-repetition refresh can't shift the decay the next one sees.
                record_access=not variant,
            )
            ev = (
                _evidence_metrics(
                    [h.text for h in retrieved.hits], qa.evidence,
                    ordering=qa.category == "knowledge-update",
                )
                if qa.evidence else _Evidence()
            )
            answer, correct, answer_error = "", False, False
            if judge_answers:
                try:
                    answer = generate_answer(qa.question, retrieved.context, generator=gen)
                except Exception as exc:  # noqa: BLE001 — one transient LLM fault (a 5xx that
                    # outlived the SDK's retries) must not kill an hour-long run whose scores are
                    # only written at the end. Scored incorrect, counted, and dropped from the
                    # paired tests so it can't read as a variant effect.
                    answer_error = True
                    logger.warning("answer generation failed; scoring this answer as an error",
                                   extra={"reason": type(exc).__name__, "detail": str(exc)})
                else:
                    correct = judge_correct(
                        qa.question, qa.answer, answer, judge=jdg, abstention=qa.abstention
                    )
            out.append(
                LongMemScore(
                    question_id=sample.sample_id,
                    question_type=qa.category or "unknown",
                    correct=correct,
                    abstention=qa.abstention,
                    answer=answer,
                    variant=variant,
                    evidence_recall=ev.recall,
                    newest_in_topk=ev.newest_in_topk,
                    newest_above_stale=ev.newest_above_stale,
                    answer_error=answer_error,
                )
            )
    return out


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
    concurrency: int = 1,
    db_factory: Callable[[], StandardDatabase] | None = None,
    rerank_scorings: Sequence[str] | None = None,
    judge_answers: bool = True,
    progress: bool = False,
) -> LongMemReport:
    """Ingest each question's history, answer from memory, judge accuracy; aggregate.

    `extract=False` (default) skips the ~O(n²) entity resolution over each question's long
    history — the dominant cost of a real run (see `_ingest_sample`).

    `concurrency` > 1 runs that many questions at once. Each question is a fully isolated tenant,
    so they're embarrassingly parallel and the overlap hides the per-question LLM latency (the
    real wall of a graph-on / haiku run). Each worker thread gets its OWN DB connection from
    `db_factory` (default: a fresh `ArangoMemoryClient`), because a python-arango handle is not
    safe to share across threads. `concurrency=1` (default) keeps the exact serial path on the
    passed `db` — byte-identical results, and what CI/tests use.

    `rerank_scorings` (RQ-3), e.g. `["replace", "rrf", "event_time:0.2"]`, evaluates every
    rerank-scoring variant against ONE ingest per question and reports each, plus paired tests
    of each variant against the first. `judge_answers=False` (`--retrieval-only`) skips the
    answer + judge LLM calls, reporting only the deterministic evidence metrics."""
    gen = generator or get_generator()
    jdg = judge or gen
    total = len(samples)
    variants: tuple[str, ...] = tuple(rerank_scorings) if rerank_scorings else ("",)
    for spec in variants:
        _parse_variant(spec)  # validate up front — never fail minutes into a paid run

    def _turns(sample: Sample) -> int:
        return sum(len(session) for session in sample.sessions)

    def _process(conn: StandardDatabase, sample: Sample) -> list[LongMemScore]:
        return _process_sample(
            conn, sample, gen=gen, jdg=jdg, agent_id=agent_id, mode=mode, k=k,
            rerank=rerank, extract=extract, variants=variants, judge_answers=judge_answers,
        )

    def _done(done: int, sample: Sample, scores: list[LongMemScore]) -> None:
        if not progress:
            return
        base = [s for s in scores if s.variant == variants[0]]
        tail = (
            f"accuracy={sum(s.correct for s in base) / len(base):.2f} ({len(base)} scored so far)"
            if judge_answers and base else f"{len(base)} scored so far"
        )
        print(f"[{done}/{total}] {sample.sample_id}: done — {tail}", file=sys.stderr, flush=True)

    scores: list[LongMemScore] = []
    if concurrency <= 1:
        for i, sample in enumerate(samples, 1):
            if progress:
                print(
                    f"[{i}/{total}] {sample.sample_id}: ingesting {_turns(sample)} turns "
                    f"({len(sample.sessions)} sessions)…",
                    file=sys.stderr, flush=True,
                )
            scores.extend(_process(db, sample))
            _done(i, sample, scores)
        return _aggregate_all(scores, variants, min_accuracy=min_accuracy, judged=judge_answers)

    # Parallel: one DB connection per worker thread (a python-arango handle isn't thread-safe).
    factory = db_factory or (lambda: ArangoMemoryClient().connect())
    local = threading.local()

    def _task(sample: Sample) -> list[LongMemScore]:
        conn = getattr(local, "db", None)
        if conn is None:
            conn = local.db = factory()
        return _process(conn, sample)

    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_task, sample): sample for sample in samples}
        for future in as_completed(futures):
            scores.extend(future.result())
            done += 1
            _done(done, futures[future], scores)
    return _aggregate_all(scores, variants, min_accuracy=min_accuracy, judged=judge_answers)


def _aggregate_all(
    scores: list[LongMemScore], variants: Sequence[str], *, min_accuracy: float | None,
    judged: bool,
) -> LongMemReport:
    """One report per variant; the top level mirrors the first (baseline) variant, so a
    single-variant run looks exactly as before, and `min_accuracy` gates on the baseline."""
    reports = {
        v: _aggregate([s for s in scores if s.variant == v], min_accuracy=min_accuracy,
                      judged=judged, variant=v)
        for v in variants
    }
    base = reports[variants[0]]
    if len(variants) == 1:
        return base
    return dataclasses.replace(
        base, by_variant=reports, paired=_paired(scores, variants, judged=judged)
    )


def _paired(
    scores: list[LongMemScore], variants: Sequence[str], *, judged: bool
) -> list[dict[str, Any]]:
    """Each variant vs the first on the same questions: judged accuracy (when judged) and the
    knowledge-update ordering metric, with an exact McNemar p-value."""
    by_key = {(s.question_id, s.variant): s for s in scores}
    qids = sorted({s.question_id for s in scores})
    base_v = variants[0]
    rows: list[dict[str, Any]] = []
    metrics: list[tuple[str, Callable[[LongMemScore], bool | None]]] = [
        ("newest_above_stale", lambda s: s.newest_above_stale),
    ]
    if judged:
        metrics.insert(0, ("accuracy", lambda s: None if s.answer_error else s.correct))
    for v in variants[1:]:
        for name, get in metrics:
            pairs = [
                (b, o)
                for q in qids
                if (b := get(by_key[(q, base_v)])) is not None
                and (o := get(by_key[(q, v)])) is not None
            ]
            if not pairs:
                continue
            base_vals = [b for b, _ in pairs]
            other_vals = [o for _, o in pairs]
            rows.append({
                "variant": v, "metric": name,
                "base": sum(base_vals) / len(base_vals),
                "value": sum(other_vals) / len(other_vals),
                **_mcnemar(base_vals, other_vals),
            })
    return rows


def _evidence_summary(scores: list[LongMemScore]) -> dict[str, float]:
    """Mean evidence recall (all questions carrying evidence) + the knowledge-update ordering
    rates over questions where ordering is defined. Empty when no evidence was scored."""
    out: dict[str, float] = {}
    recalls = [s.evidence_recall for s in scores if s.evidence_recall is not None]
    if recalls:
        out["evidence_recall"] = sum(recalls) / len(recalls)
        out["n_evidence"] = float(len(recalls))
    ordered = [s for s in scores if s.newest_above_stale is not None]
    if ordered:
        out["newest_in_topk"] = sum(bool(s.newest_in_topk) for s in ordered) / len(ordered)
        out["newest_above_stale"] = sum(bool(s.newest_above_stale) for s in ordered) / len(ordered)
        out["n_ordering"] = float(len(ordered))
    return out


def _aggregate(
    scores: list[LongMemScore], *, min_accuracy: float | None, judged: bool = True,
    variant: str = "",
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
    if judged and min_accuracy is not None and accuracy < min_accuracy:
        failures.append(f"accuracy {accuracy:.3f} < {min_accuracy}")

    return LongMemReport(
        n_questions=n,
        accuracy=accuracy,
        per_type=per_type,
        abstention_accuracy=abstention_accuracy,
        scores=scores,
        passed=not failures,
        failures=failures,
        variant=variant,
        judged=judged,
        evidence=_evidence_summary(scores),
        answer_errors=sum(s.answer_error for s in scores),
    )


def _format_one(report: LongMemReport) -> list[str]:
    lines = [f"questions:   {report.n_questions}"]
    if report.judged:
        lines.append(f"Accuracy:    {report.accuracy:.3f}")
        if report.abstention_accuracy is not None:
            lines.append(f"abstention:  {report.abstention_accuracy:.3f}  (correct-decline rate)")
        for qtype, m in report.per_type.items():
            lines.append(f"  [{qtype}] accuracy={m['accuracy']:.3f} n={m['n']:.0f}")
        if report.answer_errors:
            lines.append(f"answer errors: {report.answer_errors}  (scored incorrect; unpaired)")
    ev = report.evidence
    if "evidence_recall" in ev:
        lines.append(
            f"evidence recall@k: {ev['evidence_recall']:.3f}  (n={ev['n_evidence']:.0f})"
        )
    if "newest_above_stale" in ev:
        lines.append(
            f"KU newest-in-top-k: {ev['newest_in_topk']:.3f}   newest-above-stale: "
            f"{ev['newest_above_stale']:.3f}  (n={ev['n_ordering']:.0f})"
        )
    return lines


def _format(report: LongMemReport, *, gated: bool) -> str:
    if report.by_variant:
        lines: list[str] = []
        for name, sub in report.by_variant.items():
            lines += [f"== rerank scoring: {name} ==", *_format_one(sub), ""]
        lines.append(f"paired vs {report.variant!r} (exact McNemar):")
        for row in report.paired:
            lines.append(
                f"  {row['variant']:<16} {row['metric']:<19} {row['base']:.3f} → "
                f"{row['value']:.3f}  (+{row['gain']:.0f}/−{row['loss']:.0f} of "
                f"{row['n']:.0f}, p={row['p']:.3f})"
            )
    else:
        lines = _format_one(report)
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
    parser.add_argument("--concurrency", type=int, default=1,
                        help="questions to process at once (default 1). Each is an isolated "
                             "tenant, so >1 overlaps per-question LLM latency — the wall of a "
                             "graph-on/haiku run. Each worker opens its own DB connection; raise "
                             "with the API rate limit in mind")
    parser.add_argument("--rerank-scoring", default=None,
                        help="RQ-3: comma-separated rerank-scoring variants to compare on ONE "
                             "ingest, e.g. replace,rrf,event_time or event_time:0.2 (weight). "
                             "The first is the baseline for the paired tests. Implies --rerank")
    parser.add_argument("--retrieval-only", action="store_true",
                        help="skip answer + judge (no LLM calls): report only the deterministic "
                             "evidence metrics (needs a dataset converted with --evidence)")
    parser.add_argument("--scores-out", default=None,
                        help="also write every per-question, per-variant score as JSON — for "
                             "re-analysis or merging runs split across processes")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Surface real degradation reasons during the run (MA-8).
    configure_logging()
    args = _build_parser().parse_args(argv)
    db = ArangoMemoryClient().connect()
    ensure_schema(db)
    scorings = (
        [v.strip() for v in args.rerank_scoring.split(",") if v.strip()]
        if args.rerank_scoring else None
    )
    report = run_longmemeval(
        db, load_dataset(args.dataset), mode=args.mode, k=args.k,
        rerank=args.rerank, extract=args.extract, min_accuracy=args.min_accuracy,
        concurrency=args.concurrency, rerank_scorings=scorings,
        judge_answers=not args.retrieval_only, progress=True,
    )
    print(_format(report, gated=args.min_accuracy is not None))
    if args.scores_out:
        subs = report.by_variant.values() if report.by_variant else [report]
        rows = [dataclasses.asdict(s) for sub in subs for s in sub.scores]
        Path(args.scores_out).write_text(json.dumps(rows, indent=1))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
