"""Reranker protocol + FakeReranker + get_reranker dispatch (RQ-2b). No DB, no model."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from arango_memory.config import Settings
from arango_memory.retrieve.rerank import FakeReranker, Reranker, get_reranker
from arango_memory.retrieve.search import (
    _Candidate,
    _event_sort_key,
    _head_scores,
    _newness,
    _rerank,
)


class _LogitReranker:
    """Scores by a fixed per-text table — lets a test return cross-encoder-style negative
    logits, which are below any RRF score."""

    model = "logit-test"

    def __init__(self, table: dict[str, float]) -> None:
        self._table = table

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return [self._table[t] for t in texts]


def _fused(*texts: str) -> list[_Candidate]:
    """Candidates in descending fused (RRF-scale) order, as `_gather_fused` returns them."""
    return [
        _Candidate(key=t, text=t, embedding=[], type="episodic", fused_score=0.05 - i * 0.01)
        for i, t in enumerate(texts)
    ]


def test_fake_reranker_scores_by_query_coverage() -> None:
    r = FakeReranker()
    scores = r.score(
        "Portland reunion keynote coral",  # 4 distinct query tokens
        [
            "the keynote covered coral at the Portland reunion",  # all 4 → 1.0
            "Portland is a nice city",                             # 1 of 4 → 0.25
            "the weather was pleasant",                            # 0 → 0.0
        ],
    )
    assert scores == [1.0, 0.25, 0.0]


def test_fake_reranker_reorders_a_candidate_to_the_top() -> None:
    r = FakeReranker()
    texts = ["unrelated filler text", "the answer mentions Portland reunion"]
    scores = r.score("Portland reunion", texts)
    ranked = [t for _, t in sorted(zip(scores, texts, strict=True), reverse=True)]
    assert ranked[0] == "the answer mentions Portland reunion"


def test_fake_reranker_handles_empty() -> None:
    assert FakeReranker().score("q", []) == []
    assert FakeReranker().score("", ["anything"]) == [0.0]


def test_rerank_keeps_the_tail_below_the_reranked_head() -> None:
    # top_n=2 re-scores only a,b; c,d,e must follow in their fused order — not be dropped
    # (dropping silently truncated results whenever k > rerank_top_n).
    reranker = _LogitReranker({"a": 0.1, "b": 0.9})
    out = _rerank(_fused("a", "b", "c", "d", "e"), "q", reranker=reranker, top_n=2)
    assert [c.text for c in out] == ["b", "a", "c", "d", "e"]


def test_rerank_tail_stays_below_negative_logits() -> None:
    # Cross-encoder logits are often negative; the tail's raw RRF scores (~0.03) would then
    # outrank the reranked head if appended as-is. Every tail score must sit under the head.
    reranker = _LogitReranker({"a": -4.0, "b": -2.5})
    out = _rerank(_fused("a", "b", "c", "d"), "q", reranker=reranker, top_n=2)
    head, tail = out[:2], out[2:]
    assert [c.text for c in head] == ["b", "a"]
    assert max(c.fused_score for c in tail) < min(c.fused_score for c in head)
    assert [c.text for c in sorted(out, key=lambda c: -c.fused_score)] == ["b", "a", "c", "d"]


# ── RQ-3 scoring modes ────────────────────────────────────────────────────────────────────
def _timed(*pairs: tuple[str, str | None]) -> list[_Candidate]:
    """Fused-order candidates carrying an event_time each."""
    return [
        _Candidate(key=t, text=t, embedding=[], type="episodic", fused_score=0.05 - i * 0.01,
                   event_time=et)
        for i, (t, et) in enumerate(pairs)
    ]


def test_event_sort_key_parses_longmemeval_iso_and_rejects_garbage() -> None:
    lme = _event_sort_key("2023/05/20 (Sat) 02:21")
    assert lme is not None and (lme.year, lme.month, lme.day, lme.hour, lme.minute) == (
        2023, 5, 20, 2, 21)
    assert _event_sort_key("2023-05-20T14:05:00") == _event_sort_key("2023/05/20 (Sat) 14:05")
    assert _event_sort_key("2023-05-20") is not None  # date only → midnight
    assert _event_sort_key("sometime last spring") is None
    assert _event_sort_key("2023/13/40") is None  # not a real date
    assert _event_sort_key(None) is None


def test_newness_is_rank_scaled_with_a_neutral_default() -> None:
    assert _newness(["2023/01/01", "2023/06/01", "2023/03/01"]) == [0.0, 1.0, 0.5]
    assert _newness(["2023/01/01", None, "2023/06/01"]) == [0.0, 0.5, 1.0]  # missing → neutral
    assert _newness(["2023/01/01", "2023/01/01"]) == [0.5, 0.5]  # < 2 distinct → no push


def test_replace_scoring_is_the_cross_encoder_score() -> None:
    head = _timed(("a", None), ("b", None))
    assert _head_scores(head, [0.2, -1.5], scoring="replace", time_weight=0.1) == [0.2, -1.5]


def test_rrf_scoring_blends_cross_encoder_rank_with_fused_rank() -> None:
    # Fused order a,b,c; CE ranks b>c>a. RRF: b=1/61+1/62, a=1/63+1/61, c=1/62+1/63 → b, a, c —
    # a keeps some credit for its fused rank 1 instead of falling to last as it would on CE alone.
    reranker = _LogitReranker({"a": 0.1, "b": 0.9, "c": 0.5})
    out = _rerank(_fused("a", "b", "c"), "q", reranker=reranker, top_n=3, scoring="rrf")
    assert [c.text for c in out] == ["b", "a", "c"]
    replaced = _rerank(_fused("a", "b", "c"), "q", reranker=reranker, top_n=3)
    assert [c.text for c in replaced] == ["b", "c", "a"]


def test_event_time_scoring_prefers_the_newer_of_equally_relevant_statements() -> None:
    # The knowledge-update case: stale and updated statements score (almost) the same on
    # relevance, so the content-time prior decides — the update wins.
    head = [("stale", "2023/01/10 (Tue) 09:00"), ("update", "2023/06/02 (Fri) 18:30")]
    reranker = _LogitReranker({"stale": 2.05, "update": 2.0})
    out = _rerank(_timed(*head), "q", reranker=reranker, top_n=2, scoring="event_time",
                  time_weight=0.1)
    assert [c.text for c in out] == ["update", "stale"]
    # …but with no time weight it's the cross-encoder's call again.
    out = _rerank(_timed(*head), "q", reranker=reranker, top_n=2, scoring="event_time",
                  time_weight=0.0)
    assert [c.text for c in out] == ["stale", "update"]


def test_event_time_scoring_never_lets_newness_beat_clear_relevance() -> None:
    # A prior, not an override: a newer but irrelevant memory stays below a relevant old one.
    head = [("relevant-old", "2023/01/10"), ("irrelevant-new", "2023/06/02")]
    reranker = _LogitReranker({"relevant-old": 4.0, "irrelevant-new": -4.0})
    out = _rerank(_timed(*head), "q", reranker=reranker, top_n=2, scoring="event_time",
                  time_weight=0.1)
    assert [c.text for c in out] == ["relevant-old", "irrelevant-new"]


def test_unknown_scoring_degrades_to_fused_order() -> None:
    # §15: a bad scoring name is a rerank fault → fused order, never an empty retrieval.
    reranker = _LogitReranker({"a": 0.1, "b": 0.9})
    out = _rerank(_fused("a", "b"), "q", reranker=reranker, top_n=2, scoring="bogus")
    assert [c.text for c in out] == ["a", "b"]


def test_get_reranker_defaults_to_fake() -> None:
    r = get_reranker(Settings(reranker_provider="fake"))
    assert isinstance(r, FakeReranker)
    assert isinstance(r, Reranker)  # satisfies the runtime-checkable protocol


def test_get_reranker_caches_by_provider_and_model() -> None:
    # Built once and reused — the benchmark reranks 200 questions without reloading the model.
    a = get_reranker(Settings(reranker_provider="fake"))
    b = get_reranker(Settings(reranker_provider="fake"))
    assert a is b


def test_get_reranker_local_without_extra_is_a_clear_error() -> None:
    # sentence-transformers isn't a hard dep; selecting 'local' without it must fail loudly
    # with actionable guidance, not silently fall back to fake.
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="rerank"):
            get_reranker(Settings(reranker_provider="local"))
    else:
        pytest.skip("sentence-transformers is installed; the error path is not exercised")
