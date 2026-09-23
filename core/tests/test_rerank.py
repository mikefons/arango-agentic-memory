"""Reranker protocol + FakeReranker + get_reranker dispatch (RQ-2b). No DB, no model."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from arango_memory.config import Settings
from arango_memory.retrieve.rerank import FakeReranker, Reranker, get_reranker
from arango_memory.retrieve.search import _Candidate, _rerank


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
