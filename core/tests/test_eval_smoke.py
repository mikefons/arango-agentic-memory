"""Eval smoke test — regression gate on Recall@k for the lite/BM25 path (DESIGN.md §22)."""

from __future__ import annotations

from pathlib import Path

from arango.database import StandardDatabase

from arango_memory.eval import load_dataset, run_eval

DATASET = Path(__file__).parent / "data" / "locomo_smoke.json"

# Lite/BM25 floor for the smoke slice. Tighten as retrieval thickens (Step 2).
RECALL_FLOOR = 0.66


def test_locomo_smoke_recall(db: StandardDatabase) -> None:
    samples = load_dataset(DATASET)
    assert samples, "smoke dataset is empty"

    for sample in samples:
        result = run_eval(db, sample, k=10)
        assert result.questions, f"no QA scored for {sample.sample_id}"
        assert result.recall_at_k >= RECALL_FLOOR, (
            f"{sample.sample_id}: Recall@{result.k}={result.recall_at_k:.2f} "
            f"below floor {RECALL_FLOOR}"
        )
