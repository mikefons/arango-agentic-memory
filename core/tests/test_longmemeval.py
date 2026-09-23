"""LongMemEval accuracy harness (HX-1, DESIGN.md §23)."""

from __future__ import annotations

from pathlib import Path

import pytest
from arango.database import StandardDatabase

from arango_memory.eval.locomo import QA, Sample, Turn, load_dataset
from arango_memory.eval.longmemeval import (
    _build_parser,
    _evidence_metrics,
    _mcnemar,
    _parse_variant,
    judge_correct,
    run_longmemeval,
)
from arango_memory.eval.longmemeval_convert import _stratified_sample, convert
from arango_memory.generation import FakeGenerator
from arango_memory.security.redact import redact

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


def test_convert_evidence_carries_has_answer_turns_with_dates() -> None:
    raw = [
        {
            "question_id": "ku1",
            "question_type": "knowledge-update",
            "question": "Where do I live now?",
            "answer": "Denver",
            "haystack_session_ids": ["old", "noise", "new"],
            "haystack_dates": ["2023/01/10 (Tue) 09:00", "2023/03/01 (Wed) 12:00",
                               "2023/06/02 (Fri) 18:30"],
            "haystack_sessions": [
                [{"role": "user", "content": " I live in Boston. ", "has_answer": True}],
                [{"role": "user", "content": "I like tea."}],
                [{"role": "user", "content": "I just moved to Denver.", "has_answer": True},
                 {"role": "assistant", "content": "Congrats!", "has_answer": False}],
            ],
        }
    ]
    dataset, stats = convert(raw, evidence=True)
    assert stats["evidence_turns"] == 2
    qa = load_dataset(_write_tmp(dataset))[0].qa[0]  # QA(**item) accepts the new field
    assert qa.evidence == [
        {"text": "I live in Boston.", "event_time": "2023/01/10 (Tue) 09:00", "session_id": "old"},
        {"text": "I just moved to Denver.", "event_time": "2023/06/02 (Fri) 18:30",
         "session_id": "new"},
    ]
    # Off by default: LongMemEval stays answer-scored unless evidence is asked for.
    assert "evidence" not in convert(raw)[0]["samples"][0]["qa"][0]


def test_convert_cli_types_offset_limit_make_disjoint_splits(tmp_path: Path) -> None:
    import json

    from arango_memory.eval.longmemeval_convert import main as convert_main

    raw = [
        {"question_id": f"{t}-{i}", "question_type": t, "question": "q", "answer": "a",
         "haystack_sessions": [[{"role": "user", "content": "x"}]]}
        for t in ("knowledge-update", "temporal-reasoning") for i in range(4)
    ]
    src = tmp_path / "raw.json"
    src.write_text(json.dumps(raw))

    def ids(*args: str) -> list[str]:
        out = tmp_path / "out.json"
        assert convert_main([str(src), str(out), *args]) == 0
        return [s["sample_id"] for s in json.loads(out.read_text())["samples"]]

    dev = ids("--types", "knowledge-update", "--limit", "1")
    test = ids("--types", "knowledge-update", "--offset", "1")
    assert dev == ["knowledge-update-0"]
    assert test == ["knowledge-update-1", "knowledge-update-2", "knowledge-update-3"]
    assert ids("--types", "temporal-reasoning")[0] == "temporal-reasoning-0"


# ── RQ-3 evidence metrics + paired stats (no DB) ──────────
_KU_EVIDENCE = [
    {"text": "I live in Boston.", "event_time": "2023/01/10 (Tue) 09:00", "session_id": "old"},
    {"text": "I just moved to Denver.", "event_time": "2023/06/02 (Fri) 18:30",
     "session_id": "new"},
]


def test_evidence_metrics_newest_above_stale() -> None:
    hits = ["user: I just moved to Denver.", "user: I like tea.", "user: I live in Boston."]
    ev = _evidence_metrics(hits, _KU_EVIDENCE, ordering=True)
    assert ev.recall == 1.0 and ev.newest_in_topk is True and ev.newest_above_stale is True


def test_evidence_metrics_stale_above_newest() -> None:
    hits = ["user: I live in Boston.", "user: I just moved to Denver."]
    ev = _evidence_metrics(hits, _KU_EVIDENCE, ordering=True)
    assert ev.newest_in_topk is True and ev.newest_above_stale is False


def test_evidence_metrics_newest_missing_and_ordering_off() -> None:
    hits = ["user: I live in Boston.", "user: I like tea."]
    ev = _evidence_metrics(hits, _KU_EVIDENCE, ordering=True)
    assert ev.recall == 0.5 and ev.newest_in_topk is False and ev.newest_above_stale is False
    # ordering=False (non-KU question types): recall only.
    ev = _evidence_metrics(hits, _KU_EVIDENCE, ordering=False)
    assert ev.recall == 0.5 and ev.newest_above_stale is None


def test_evidence_metrics_matches_redacted_memories() -> None:
    # Stored memories are PII-redacted at ingest; evidence must match that stored form.
    text = "email me at sam.lee@example.com about the move"
    hits = [redact(f"user: {text}", mode="lite", generator=None)]
    ev = _evidence_metrics(hits, [{"text": text, "event_time": "", "session_id": "s"}],
                           ordering=False)
    assert ev.recall == 1.0


def test_mcnemar_exact_values() -> None:
    # 5 discordant pairs all favouring the variant: p = 2 × (1/2)^5 = 0.0625.
    r = _mcnemar([False] * 5 + [True] * 5, [True] * 5 + [True] * 5)
    assert (r["gain"], r["loss"], r["n"]) == (5, 0, 10) and r["p"] == pytest.approx(0.0625)
    assert _mcnemar([True, False], [False, True])["p"] == 1.0  # balanced → no evidence
    assert _mcnemar([True, True], [True, True])["p"] == 1.0  # no discordant pairs


def test_parse_variant() -> None:
    assert _parse_variant("") == (None, None)
    assert _parse_variant("rrf") == ("rrf", None)
    assert _parse_variant("event_time:0.25") == ("event_time", 0.25)
    with pytest.raises(ValueError, match="unknown rerank scoring"):
        _parse_variant("bogus")


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


def test_concurrency_matches_serial(db: StandardDatabase) -> None:
    # Cross-question parallelism: each question is an isolated tenant, so running them concurrently
    # must produce the same aggregate as the serial path. db_factory reuses the fixture connection
    # (the workers would otherwise open their own, off the test's settings).
    samples = load_dataset(_SMOKE)
    gen = _mixed({"currently live": "CORRECT", "sister": "INCORRECT"})
    serial = run_longmemeval(db, samples, generator=gen, judge=gen, k=10)
    parallel = run_longmemeval(
        db, samples, generator=gen, judge=gen, k=10,
        concurrency=2, db_factory=lambda: db,
    )
    assert parallel.n_questions == serial.n_questions
    assert parallel.accuracy == serial.accuracy
    assert parallel.per_type == serial.per_type


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


def _sam_sample(sample_id: str) -> Sample:
    """A knowledge-update question whose stale statement out-scores the update on relevance
    (FakeReranker token coverage: stale 4/5, update 1/5), so only a content-time prior can put
    the update first."""
    old_t, new_t = "2023/01/10 (Tue) 09:00", "2023/06/02 (Fri) 18:30"
    evidence = [
        {"text": "Sam does live in Boston now.", "event_time": old_t, "session_id": "old"},
        {"text": "Sam moved to Denver.", "event_time": new_t, "session_id": "new"},
    ]
    return Sample(
        sample_id=sample_id,
        sessions=[
            [Turn("user", "Sam does live in Boston now.", old_t),
             Turn("assistant", "Got it, Boston.", old_t)],
            [Turn("user", "I had pasta for lunch.", "2023/03/01 (Wed) 12:00")],
            [Turn("user", "Sam moved to Denver.", new_t)],
        ],
        qa=[QA(question="Where does Sam live now?", answer="Denver",
               category="knowledge-update", evidence=evidence)],
    )


def test_rerank_scoring_variants_are_paired_on_one_ingest(db: StandardDatabase) -> None:
    sample = _sam_sample("lme-rq3-sam")
    report = run_longmemeval(
        db, [sample], generator=FakeGenerator(), k=10,
        rerank_scorings=["replace", "event_time:0.5"], judge_answers=False,
    )
    assert set(report.by_variant) == {"replace", "event_time:0.5"}
    assert report.judged is False and report.passed  # retrieval-only: no accuracy gate
    # Replace ranks the stale (more query-relevant) statement first; the time prior flips it.
    assert report.by_variant["replace"].evidence["newest_above_stale"] == 0.0
    assert report.by_variant["event_time:0.5"].evidence["newest_above_stale"] == 1.0
    (row,) = [r for r in report.paired if r["metric"] == "newest_above_stale"]
    assert (row["variant"], row["gain"], row["loss"]) == ("event_time:0.5", 1, 0)
    # Variants are read-only probes: no spaced-repetition refresh, so every variant saw the
    # same state (store_many writes access_count=1; a recorded access would bump it).
    counts = list(db.aql.execute(
        "FOR m IN memories FILTER m.tenant_id == @t RETURN m.access_count",
        bind_vars={"t": sample.sample_id},
    ))
    assert counts and all(c == 1 for c in counts)


def test_single_configuration_report_is_unchanged(db: StandardDatabase) -> None:
    # No --rerank-scoring: the legacy single-variant report shape (no by_variant/paired).
    samples = load_dataset(_SMOKE)
    gen = _mixed({"currently live": "CORRECT", "sister": "INCORRECT"})
    report = run_longmemeval(db, samples, generator=gen, judge=gen, k=10)
    assert report.by_variant == {} and report.paired == [] and report.variant == ""
    assert report.judged is True and report.accuracy == 0.5
