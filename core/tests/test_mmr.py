"""MMR re-rank: relevance = fused score, diversity configurable (DESIGN.md §9). No DB."""

from __future__ import annotations

from arango_memory.retrieve.search import _Candidate, _mmr


def _cand(key: str, emb: list[float], fused: float) -> _Candidate:
    return _Candidate(key=key, text=key, embedding=emb, type="episodic", fused_score=fused)


# A has the highest fusion score (e.g. a strong BM25 lexical hit), B is a near-duplicate
# of A with a lower score, C is orthogonal with a middling score.
_A = _cand("A", [1.0, 0.0], fused=1.0)
_B = _cand("B", [0.98, 0.02], fused=0.4)
_C = _cand("C", [0.0, 1.0], fused=0.6)


def test_pure_relevance_ranks_by_fusion_score() -> None:
    # lambda_=1.0 → fusion order (A > C > B). This is the recall-favouring setting: the
    # top-fused candidate is never dropped for a vector-cosine reason (the MMR bug).
    order = [c.key for c in _mmr([_A, _B, _C], k=3, lambda_=1.0)]
    assert order == ["A", "C", "B"]


def test_diversity_demotes_the_near_duplicate() -> None:
    # lambda_=0.0 → diversity only: after A, the orthogonal C beats the near-duplicate B.
    order = [c.key for c in _mmr([_A, _B, _C], k=2, lambda_=0.0)]
    assert order[0] == "A"  # first pick has no diversity term → highest is arbitrary-stable
    assert order[1] == "C"


def test_top_pick_is_the_highest_fused_regardless_of_lambda() -> None:
    # The single most-relevant candidate must survive at any lambda (the recall guarantee).
    for lam in (0.0, 0.5, 1.0):
        assert _mmr([_A, _B, _C], k=1, lambda_=lam)[0].key == "A"


def test_lambda_defaults_to_settings(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from arango_memory.config import settings

    monkeypatch.setattr(settings, "mmr_lambda", 1.0)
    assert [c.key for c in _mmr([_A, _B, _C], k=3)] == ["A", "C", "B"]
