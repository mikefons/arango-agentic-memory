"""HX-2 recall-vs-corpus-size curve harness + the rrf_bm25_weight arm knob."""

from __future__ import annotations

import tempfile
from pathlib import Path

from arango.database import StandardDatabase

from arango_memory.config import settings
from arango_memory.eval.locomo import load_dataset
from arango_memory.eval.plot_recall_curve import load_series
from arango_memory.eval.recall_curve import (
    ARM_WEIGHTS,
    _arm,
    run_curve,
    split_probe_distractor,
    write_csv,
)
from arango_memory.retrieve.search import _arm_weight

_SMOKE = Path(__file__).parent / "data" / "recall_curve_smoke.json"


# ── the arm knob (no DB) ──────────────────────────────────
def test_bm25_arm_weight_is_configurable() -> None:
    assert _arm_weight("bm25") == 1.0  # default reference arm
    with _arm(0.0, 1.0):  # vector-only: bm25 zeroed
        assert _arm_weight("bm25") == 0.0
        assert _arm_weight("vector") == 1.0
    assert _arm_weight("bm25") == 1.0  # restored on exit
    # unchanged default for the other arms
    assert settings.rrf_bm25_weight == 1.0


# ── probe/distractor split (no DB) ────────────────────────
def test_split_keeps_gold_and_separates_distractors() -> None:
    sample = load_dataset(_SMOKE)[0]
    probe_qa, gold_turns, distractor_turns = split_probe_distractor(sample, n_probes=2)
    assert len(probe_qa) == 2
    gold_texts = {t.text for t in gold_turns}
    assert "Paris is the capital of France." in gold_texts
    assert "Mount Everest is the tallest mountain on Earth." in gold_texts
    # the two gold paragraphs are held out of the distractor pool
    assert len(gold_turns) == 2
    assert all(t not in gold_turns for t in distractor_turns)
    assert len(distractor_turns) == 6  # 8 paragraphs − 2 gold


def test_probe_cap_limits_scored_questions() -> None:
    sample = load_dataset(_SMOKE)[0]
    probe_qa, _, _ = split_probe_distractor(sample, n_probes=1)
    assert len(probe_qa) == 1


# ── CSV round-trip (no DB) ────────────────────────────────
def test_csv_round_trips_series() -> None:
    from arango_memory.eval.recall_curve import CurvePoint

    points = [
        CurvePoint(2, "fused", 1.0, 1.0),
        CurvePoint(2, "vector", 0.5, 0.0),
        CurvePoint(5, "fused", 0.9, 0.5),
    ]
    out = Path(tempfile.mkdtemp()) / "curve.csv"
    write_csv(points, str(out))
    series = load_series(out)
    assert series["fused"] == [(2, 1.0), (5, 0.9)]
    assert series["vector"] == [(2, 0.5)]


# ── end-to-end sweep (DB) ─────────────────────────────────
def test_curve_sweep_produces_rows_per_arm(db: StandardDatabase) -> None:
    sample = load_dataset(_SMOKE)[0]
    points = run_curve(db, sample, n_probes=2, step=3, k=10)

    sizes = sorted({p.corpus_size for p in points})
    assert len(sizes) >= 2  # at least an initial + one grown checkpoint
    assert sizes == sorted(sizes) and sizes[0] < sizes[-1]  # corpus grows monotonically
    # every checkpoint reports all three arms, recall in [0, 1]
    for size in sizes:
        arms = {p.arm for p in points if p.corpus_size == size}
        assert arms == set(ARM_WEIGHTS)
    assert all(0.0 <= p.recall_frac <= 1.0 for p in points)
    # BM25 is lexical (embedding-independent), so it should find at least one gold on the
    # smallest corpus even keyless — sanity that the plumbing retrieves, not just runs.
    smallest = min(sizes)
    bm25_small = next(p for p in points if p.corpus_size == smallest and p.arm == "bm25")
    assert bm25_small.recall_frac > 0.0
