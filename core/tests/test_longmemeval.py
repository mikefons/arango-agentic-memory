"""LongMemEval accuracy harness (HX-1, DESIGN.md §23)."""

from __future__ import annotations

from pathlib import Path

from arango.database import StandardDatabase

from arango_memory.eval.locomo import load_dataset
from arango_memory.eval.longmemeval import (
    _build_parser,
    judge_correct,
    run_longmemeval,
)
from arango_memory.eval.longmemeval_convert import _stratified_sample, convert
from arango_memory.generation import FakeGenerator

_SMOKE = Path(__file__).parent / "data" / "longmemeval_smoke.json"


def _judge(labels: str) -> FakeGenerator:
    return FakeGenerator(handler=lambda prompt, system: labels)


# ── judge parsing (no DB) ─────────────────────────────────
def test_judge_correct_parses_and_avoids_substring_trap() -> None:
    assert judge_correct("q", "gold", "a", judge=_judge("CORRECT")) is True
    # "INCORRECT" must NOT count as correct despite containing the substring "CORRECT".
    assert judge_correct("q", "gold", "a", judge=_judge("INCORRECT")) is False
    # Unparseable output scores as incorrect, never raises.
    assert judge_correct("q", "gold", "a", judge=_judge("maybe?")) is False


def test_judge_abstention_grades_a_decline() -> None:
    # For an abstention question, a correct *decline* is CORRECT.
    assert judge_correct(
        "What is X?", "", "I don't have that information.", judge=_judge("CORRECT"),
        abstention=True,
    ) is True
    assert judge_correct(
        "What is X?", "", "It is 42.", judge=_judge("INCORRECT"), abstention=True
    ) is False


# ── converter (no DB) ─────────────────────────────────────
def test_convert_maps_sessions_and_flags_abstention() -> None:
    raw = [
        {
            "question_id": "q1_abs",
            "question_type": "single-session-user",
            "question": "What is my sister's name?",
            "answer": "",
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I like hiking."},
                    {"role": "assistant", "content": ""},  # blank turn → dropped
                ]
            ],
        },
        {
            "question_id": "q2",
            "question_type": "knowledge-update",
            "question": "Where do I live?",
            "answer": "Munich",
            "haystack_sessions": [[{"role": "user", "content": "I moved to Munich."}]],
        },
    ]
    dataset, stats = convert(raw)
    assert stats == {"questions": 2, "abstention": 1}

    samples = load_dataset(_write_tmp(dataset))
    s1, s2 = samples
    assert s1.sample_id == "q1_abs"
    assert s1.qa[0].abstention is True and s1.qa[0].category == "single-session-user"
    assert len(s1.sessions[0]) == 1  # blank assistant turn dropped
    assert s1.sessions[0][0].speaker == "user"
    assert s2.qa[0].abstention is False and s2.qa[0].answer == "Munich"


def test_convert_injects_session_and_question_dates() -> None:
    raw = [
        {
            "question_id": "q1",
            "question_type": "temporal-reasoning",
            "question": "When did I move?",
            "answer": "May",
            "question_date": "2023/05/30 (Tue) 23:40",
            "haystack_dates": ["2023/05/20 (Sat) 02:21"],
            "haystack_sessions": [[{"role": "user", "content": "I moved to Munich."}]],
        }
    ]
    dataset, _ = convert(raw)
    samples = load_dataset(_write_tmp(dataset))
    turn = samples[0].sessions[0][0]
    # IN-4b/IN-5: session date is a *field*, not a text prefix (so it can't dilute retrieval).
    assert turn.text == "I moved to Munich."
    assert turn.event_time == "2023/05/20 (Sat) 02:21"
    assert samples[0].qa[0].question.startswith("[Today's date is 2023/05/30 (Tue) 23:40.]")


def test_stratified_sample_spreads_across_types() -> None:
    # 10 of type A, 10 of B, 2 of C — grouped (as LongMemEval-S is).
    raw = (
        [{"question_type": "A", "i": i} for i in range(10)]
        + [{"question_type": "B", "i": i} for i in range(10)]
        + [{"question_type": "C", "i": i} for i in range(2)]
    )
    picked = _stratified_sample(raw, 9)
    types = [item["question_type"] for item in picked]
    assert len(picked) == 9
    # every available type is represented (a plain raw[:9] would be all "A")
    assert set(types) == {"A", "B", "C"}
    # round-robin: C exhausts (only 2), A/B keep filling — balanced, not one category
    assert types.count("A") >= 3 and types.count("B") >= 3 and types.count("C") == 2


def _write_tmp(dataset: dict) -> Path:
    import json
    import tempfile

    p = Path(tempfile.mkdtemp()) / "lme.json"
    p.write_text(json.dumps(dataset))
    return p


# ── CLI ───────────────────────────────────────────────────
def test_cli_parser() -> None:
    args = _build_parser().parse_args(
        ["d.json", "--mode", "multihop", "--k", "5", "--rerank", "--min-accuracy", "0.5"]
    )
    assert args.dataset == "d.json" and args.mode == "multihop" and args.k == 5
    assert args.rerank is True and args.min_accuracy == 0.5


# ── end-to-end aggregation (DB) ───────────────────────────
def _mixed(judgements: dict[str, str]) -> FakeGenerator:
    """Answers every question; grades judge calls per a question→verdict map."""

    def handler(prompt: str, system: str | None) -> str:
        if "Model answer:" in prompt:  # a judge call (both judge prompts carry this)
            for needle, verdict in judgements.items():
                if needle in prompt:
                    return verdict
            return "CORRECT"
        return "some answer"  # an answer call

    return FakeGenerator(handler=handler)


def test_accuracy_aggregates_overall_and_per_type(db: StandardDatabase) -> None:
    samples = load_dataset(_SMOKE)
    gen = _mixed({"currently live": "CORRECT", "sister": "INCORRECT"})
    report = run_longmemeval(db, samples, generator=gen, judge=gen, k=10)

    assert report.n_questions == 2
    assert report.accuracy == 0.5  # one CORRECT, one INCORRECT
    assert report.abstention_accuracy == 0.0  # the abstention question was judged INCORRECT
    assert set(report.per_type) == {"knowledge-update", "single-session-user"}
    assert report.per_type["knowledge-update"]["accuracy"] == 1.0
    assert report.passed is True  # no gate set


def test_min_accuracy_gates(db: StandardDatabase) -> None:
    samples = load_dataset(_SMOKE)
    gen = _mixed({"currently live": "CORRECT", "sister": "INCORRECT"})
    report = run_longmemeval(db, samples, generator=gen, judge=gen, k=10, min_accuracy=0.9)
    assert report.passed is False and report.failures


def test_extract_true_builds_graph_via_store_many(db: StandardDatabase) -> None:
    # IN-5: the harness ingests each history through store_many(extract=True), so the entity
    # graph is built (batched) — affordable now that the record + graph passes are bulk.
    samples = load_dataset(_SMOKE)
    gen = _mixed({"currently live": "CORRECT", "sister": "INCORRECT"})
    run_longmemeval(db, samples, generator=gen, judge=gen, k=10, extract=True)
    tenant = samples[0].sample_id
    cur = db.aql.execute(
        "FOR e IN entities FILTER e.tenant_id == @t COLLECT WITH COUNT INTO c RETURN c",
        bind_vars={"t": tenant},
    )
    assert int(next(iter(cur), 0)) > 0
