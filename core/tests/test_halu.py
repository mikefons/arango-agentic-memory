"""Hallucination / Noise-Reduction eval harness (DESIGN.md §23)."""

from __future__ import annotations

from arango.database import StandardDatabase

from arango_memory.eval.halu import (
    Verdict,
    _build_parser,
    generate_answer,
    judge_answer,
    run_halu_eval,
)
from arango_memory.eval.locomo import QA, Sample, Turn, run_eval
from arango_memory.generation import FakeGenerator


def _judge(labels: str) -> FakeGenerator:
    return FakeGenerator(handler=lambda prompt, system: labels)


# ── unit (no DB) ──────────────────────────────────────────
def test_judge_parses_both_labels() -> None:
    assert judge_answer("q", "ctx", "a", judge=_judge("SUPPORTED FOCUSED")) == Verdict(True, True)


def test_judge_negative_and_unparseable() -> None:
    assert judge_answer("q", "c", "a", judge=_judge("HALLUCINATED NOISY")).supported is False
    bad = judge_answer("q", "c", "a", judge=_judge("???"))
    assert bad.supported is False and bad.focused is False  # unparseable → failure


def test_generate_answer_uses_context() -> None:
    captured: dict[str, str] = {}

    def handler(prompt: str, system: str | None) -> str:
        captured["prompt"] = prompt
        return "the answer"

    out = generate_answer("Who?", "Alice is here", generator=FakeGenerator(handler=handler))
    assert out == "the answer"
    assert "Alice is here" in captured["prompt"] and "Who?" in captured["prompt"]


# ── aggregation (DB) ──────────────────────────────────────
def _sample() -> Sample:
    return Sample(
        sample_id="halu1",
        sessions=[[Turn(speaker="User", text="Alice adopted a dog named Rex.")]],
        qa=[
            QA(question="good question", answer="Rex", gold_fact="dog named Rex"),
            QA(question="bad question", answer="Rex", gold_fact="dog named Rex"),
        ],
    )


def _mixed_judge() -> FakeGenerator:
    # SUPPORTED+FOCUSED for the "good" question; HALLUCINATED+NOISY for the "bad" one.
    def handler(prompt: str, system: str | None) -> str:
        if system and "judge" in system.lower():
            return "SUPPORTED FOCUSED" if "good question" in prompt else "HALLUCINATED NOISY"
        return "an answer"

    return FakeGenerator(handler=handler)


def test_rates_aggregate_over_questions(db: StandardDatabase) -> None:
    sample = _sample()
    run_eval(db, sample)  # ingest the sessions first
    gen = _mixed_judge()
    report = run_halu_eval(db, [sample], generator=gen, judge=gen, k=10)
    assert report.n_questions == 2
    assert report.hallucination_rate == 0.5   # 1 of 2 unsupported
    assert report.noise_reduction_rate == 0.5  # 1 of 2 focused
    assert report.passed is True               # no thresholds → never fails


def test_thresholds_gate(db: StandardDatabase) -> None:
    sample = _sample()
    run_eval(db, sample)
    gen = _mixed_judge()
    report = run_halu_eval(
        db, [sample], generator=gen, judge=gen, k=10, max_hallucination=0.0, min_nrr=0.9
    )
    assert report.passed is False
    assert len(report.failures) == 2  # hallucination and NRR both miss


def test_cli_parser() -> None:
    args = _build_parser().parse_args(["d.json", "--mode", "full", "--k", "5", "--min-nrr", "0.8"])
    assert args.dataset == "d.json" and args.mode == "full" and args.min_nrr == 0.8
