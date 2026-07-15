"""MMR relevance↔diversity balance is configurable (DESIGN.md §9). Pure, no DB."""

from __future__ import annotations

from arango_memory.retrieve.search import _Candidate, _mmr


def _cand(key: str, emb: list[float]) -> _Candidate:
    return _Candidate(key=key, text=key, embedding=emb, type="episodic")


# query points along +x; A is most relevant, B is a near-duplicate of A, C is orthogonal.
_QUERY = [1.0, 0.0]
_A = _cand("A", [1.0, 0.0])
_B = _cand("B", [0.98, 0.02])
_C = _cand("C", [0.0, 1.0])


def test_pure_relevance_keeps_the_near_duplicate_second() -> None:
    # lambda_=1.0 → relevance only: the two on-query vectors (A, B) rank above the
    # orthogonal C. This is the recall-favouring setting.
    order = [c.key for c in _mmr(_QUERY, [_A, _B, _C], k=2, lambda_=1.0)]
    assert order == ["A", "B"]


def test_diversity_promotes_the_orthogonal_candidate() -> None:
    # lambda_=0.0 → diversity only: after picking one, the far-apart C beats the near
    # duplicate B for the second slot.
    order = [c.key for c in _mmr(_QUERY, [_A, _B, _C], k=2, lambda_=0.0)]
    assert order[1] == "C"


def test_lambda_defaults_to_settings(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from arango_memory.config import settings

    monkeypatch.setattr(settings, "mmr_lambda", 1.0)
    order = [c.key for c in _mmr(_QUERY, [_A, _B, _C], k=2)]
    assert order == ["A", "B"]  # picks up the configured value with no explicit arg
