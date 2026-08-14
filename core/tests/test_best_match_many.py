"""Unit tests for the vectorized entity resolver (IN-6) — no container.

`_best_match_many` must return exactly what calling `_best_match` per query returns: same
chosen row and (approximately) the same cosine. It's the batched matmul that replaces the
per-entity Python cosine loop in `write_entities_many`, so equivalence is the whole point."""

from __future__ import annotations

import math
import random

from arango_memory.ingest.entities import _best_match, _best_match_many


def _row(key: str, name: str, label: str, vec: list[float]) -> dict[str, object]:
    return {"key": key, "name": name, "label": label, "embedding": vec}


def _reference(
    qvecs: list[list[float]], pool: list[dict[str, object]], exclude: list[tuple[str, str]]
) -> list[tuple[str | None, float]]:
    out: list[tuple[str | None, float]] = []
    for vec, nl in zip(qvecs, exclude, strict=True):
        match, sim = _best_match(vec, pool, exclude=nl)
        out.append((None if match is None else match["key"], sim))  # type: ignore[index]
    return out


def _actual(
    qvecs: list[list[float]], pool: list[dict[str, object]], exclude: list[tuple[str, str]]
) -> list[tuple[str | None, float]]:
    out: list[tuple[str | None, float]] = []
    for match, sim in _best_match_many(qvecs, pool, exclude=exclude):
        out.append((None if match is None else match["key"], sim))  # type: ignore[index]
    return out


def _assert_same(ref: list[tuple[str | None, float]], act: list[tuple[str | None, float]]) -> None:
    assert len(ref) == len(act)
    for (rk, rs), (ak, as_) in zip(ref, act, strict=True):
        assert rk == ak
        assert math.isclose(rs, as_, abs_tol=1e-9) or (rs == -1.0 and as_ == -1.0)


def test_empty_inputs() -> None:
    assert _best_match_many([], [], exclude=[]) == []
    # empty pool → (None, -1.0) for every query, exactly as _best_match
    res = _best_match_many([[1.0, 0.0]], [], exclude=[("a", "X")])
    assert res == [(None, -1.0)]


def test_matches_reference_on_random_batch() -> None:
    rng = random.Random(42)
    dim = 16
    pool = [
        _row(f"e{i}", f"name{i}", "Concept", [rng.gauss(0, 1) for _ in range(dim)])
        for i in range(25)
    ]
    qvecs = [[rng.gauss(0, 1) for _ in range(dim)] for _ in range(12)]
    exclude = [("q", "Concept")] * len(qvecs)  # no collision with pool names
    _assert_same(_reference(qvecs, pool, exclude), _actual(qvecs, pool, exclude))


def test_excludes_own_name_label() -> None:
    # A query whose (name, label) equals a pool row must not match that row — even at cos 1.0.
    v = [1.0, 2.0, 3.0]
    pool = [_row("self", "alice", "Person", v), _row("other", "bob", "Person", [3.0, 2.0, 1.0])]
    qvecs = [v]
    exclude = [("alice", "Person")]
    ref, act = _reference(qvecs, pool, exclude), _actual(qvecs, pool, exclude)
    _assert_same(ref, act)
    assert act[0][0] == "other"  # fell through to the non-excluded row


def test_zero_vector_scores_zero_like_cos() -> None:
    pool = [
        _row("e0", "x", "Concept", [0.0, 0.0, 0.0]),
        _row("e1", "y", "Concept", [1.0, 0.0, 0.0]),
    ]
    qvecs = [[1.0, 1.0, 0.0]]
    exclude = [("q", "Concept")]
    _assert_same(_reference(qvecs, pool, exclude), _actual(qvecs, pool, exclude))
