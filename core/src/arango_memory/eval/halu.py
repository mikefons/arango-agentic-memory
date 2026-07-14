"""Hallucination / Noise-Reduction eval harness (DESIGN.md §23).

The LoCoMo runner ([locomo.py]) scores the *retrieval* side deterministically
(Recall@k, token-F1, tokens-injected). The two §23 metrics it can't cover need the
**full agent loop** — a generated answer, then an LLM judge:

  - **Hallucination Rate** — fraction of answers the judge marks as containing a
    claim **not supported** by the retrieved context.
  - **Noise-Reduction Rate** — fraction of answers that stayed **focused**: relied
    only on the relevant fact and ignored irrelevant retrieved memories.

Both the answer generator and the judge are injectable `Generator`s, so CI runs
**keyless** on `FakeGenerator` and a real run uses Haiku. Report-only by default
(§23 sets no numeric targets for these two); optional thresholds can gate a run.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field

from arango.database import StandardDatabase

from ..client import ArangoMemoryClient
from ..generation import Generator, get_generator
from ..retrieve.search import retrieve
from ..schema.collections import ensure_schema
from ..telemetry.logging import configure_logging
from .locomo import QA, Sample, load_dataset

_ANSWER_SYSTEM = (
    "Answer the user's question using ONLY the provided memory context. If the context "
    "does not contain the answer, say you don't know. Be concise; add no facts of your own."
)

_JUDGE_SYSTEM = (
    "You judge an assistant's answer against the memory context it was given. Reply with "
    "two space-separated labels:\n"
    "1. SUPPORTED if every claim in the answer is backed by the context, else HALLUCINATED.\n"
    "2. FOCUSED if the answer used only context relevant to the question (ignored "
    "irrelevant memories), else NOISY.\n"
    "Reply with exactly two words, e.g. 'SUPPORTED FOCUSED'."
)


@dataclass(frozen=True)
class Verdict:
    supported: bool
    focused: bool


@dataclass(frozen=True)
class HaluScore:
    question: str
    answer: str
    verdict: Verdict
    category: str | None = None


@dataclass
class HaluReport:
    n_questions: int = 0
    hallucination_rate: float = 0.0
    noise_reduction_rate: float = 0.0
    scores: list[HaluScore] = field(default_factory=list)
    passed: bool = True
    failures: list[str] = field(default_factory=list)


def generate_answer(question: str, context: str, *, generator: Generator) -> str:
    """The agent loop's answer step: respond from the retrieved memory context only."""
    prompt = f"Memory context:\n{context or '(none)'}\n\nQuestion: {question}"
    return generator.complete(prompt, system=_ANSWER_SYSTEM).strip()


def judge_answer(
    question: str, context: str, answer: str, *, judge: Generator
) -> Verdict:
    """LLM judge → (supported, focused). Unparseable output is treated as a failure."""
    prompt = f"Question: {question}\nContext:\n{context or '(none)'}\nAnswer: {answer}"
    labels = judge.complete(prompt, system=_JUDGE_SYSTEM).strip().upper().split()
    supported = "SUPPORTED" in labels
    focused = "FOCUSED" in labels
    return Verdict(supported=supported, focused=focused)


def _score_question(
    db: StandardDatabase,
    qa: QA,
    *,
    tenant_id: str,
    agent_id: str,
    generator: Generator,
    judge: Generator,
    mode: str,
    k: int,
) -> HaluScore:
    retrieved = retrieve(
        db, query=qa.question, tenant_id=tenant_id, agent_id=agent_id, mode=mode, k=k
    )
    answer = generate_answer(qa.question, retrieved.context, generator=generator)
    verdict = judge_answer(qa.question, retrieved.context, answer, judge=judge)
    return HaluScore(question=qa.question, answer=answer, verdict=verdict, category=qa.category)


def run_halu_eval(
    db: StandardDatabase,
    samples: Sequence[Sample],
    *,
    generator: Generator | None = None,
    judge: Generator | None = None,
    agent_id: str = "assistant",
    mode: str = "lite",
    k: int = 10,
    max_hallucination: float | None = None,
    min_nrr: float | None = None,
) -> HaluReport:
    """Generate + judge an answer per QA; aggregate hallucination + noise-reduction rates.

    Assumes the samples' sessions are already ingested (e.g. via `locomo.run_eval`);
    this harness only exercises the answer/judge loop over each question.
    """
    gen = generator or get_generator()
    jdg = judge or gen
    scores: list[HaluScore] = []
    for sample in samples:
        for qa in sample.qa:
            scores.append(
                _score_question(
                    db, qa, tenant_id=sample.sample_id, agent_id=agent_id,
                    generator=gen, judge=jdg, mode=mode, k=k,
                )
            )

    n = len(scores)
    hallucination = sum(not s.verdict.supported for s in scores) / n if n else 0.0
    nrr = sum(s.verdict.focused for s in scores) / n if n else 0.0

    failures: list[str] = []
    if max_hallucination is not None and hallucination > max_hallucination:
        failures.append(f"hallucination {hallucination:.3f} > {max_hallucination}")
    if min_nrr is not None and nrr < min_nrr:
        failures.append(f"noise-reduction {nrr:.3f} < {min_nrr}")

    return HaluReport(
        n_questions=n,
        hallucination_rate=hallucination,
        noise_reduction_rate=nrr,
        scores=scores,
        passed=not failures,
        failures=failures,
    )


def _format(report: HaluReport, *, gated: bool) -> str:
    lines = [
        f"questions:            {report.n_questions}",
        f"Hallucination Rate:   {report.hallucination_rate:.3f}  (lower is better)",
        f"Noise-Reduction Rate: {report.noise_reduction_rate:.3f}  (higher is better)",
    ]
    if gated:
        lines.append("PASS" if report.passed else "FAIL: " + "; ".join(report.failures))
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.eval.halu")
    parser.add_argument("dataset", help="path to a LoCoMo-style dataset JSON")
    parser.add_argument("--mode", choices=["lite", "full"], default="lite")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--max-hallucination", type=float, default=None)
    parser.add_argument("--min-nrr", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    from .locomo import run_eval

    # Surface real degradation reasons during the run (MA-8).
    configure_logging()
    args = _build_parser().parse_args(argv)
    db = ArangoMemoryClient().connect()
    ensure_schema(db)
    samples = load_dataset(args.dataset)
    for sample in samples:  # ingest sessions first (reuses the LoCoMo runner)
        run_eval(db, sample, mode=args.mode, k=args.k)
    report = run_halu_eval(
        db, samples, mode=args.mode, k=args.k,
        max_hallucination=args.max_hallucination, min_nrr=args.min_nrr,
    )
    gated = args.max_hallucination is not None or args.min_nrr is not None
    print(_format(report, gated=gated))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
