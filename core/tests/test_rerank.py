"""Reranker protocol + FakeReranker + get_reranker dispatch (RQ-2b). No DB, no model."""

from __future__ import annotations

import pytest

from arango_memory.config import Settings
from arango_memory.retrieve.rerank import FakeReranker, Reranker, get_reranker


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
