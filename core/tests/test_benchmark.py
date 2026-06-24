"""Full benchmark runner: aggregation, per-category, target gating (§23, Step 7c)."""

from __future__ import annotations

from pathlib import Path

from arango.database import StandardDatabase

from arango_memory.eval.benchmark import (
    _build_parser,
    _evaluate_targets,
    run_benchmark,
)
from arango_memory.eval.locomo import QA, Sample, Turn, load_dataset

SMOKE = Path(__file__).parent / "data" / "locomo_smoke.json"


def test_evaluate_targets_pass_and_fail() -> None:
    passed, failures = _evaluate_targets(recall_at_k=0.9, mean_f1=0.7, mean_tokens=900)
    assert passed is True
    assert failures == []

    failed, reasons = _evaluate_targets(recall_at_k=0.4, mean_f1=0.3, mean_tokens=2000)
    assert failed is False
    assert len(reasons) == 3  # recall, f1, and tokens all miss


def test_run_benchmark_on_smoke_slice(db: StandardDatabase) -> None:
    report = run_benchmark(db, load_dataset(SMOKE), k=10)
    assert report.n_questions == 3
    assert report.recall_at_k >= 0.66          # smoke slice is highly retrievable
    assert report.mean_tokens <= 1500
    assert report.per_category == {}           # smoke QA is uncategorized
    # Real-data targets (F1 ≥ 0.65) aren't expected on a BM25/fake-embedder slice;
    # we only assert the gate ran and produced a verdict.
    assert isinstance(report.passed, bool)
    # Latency is captured from the run (lite mode → retrieval.lite key) for §23.
    assert "retrieval.lite" in report.latency_ms
    assert report.latency_ms["retrieval.lite"]["p99"] >= 0


def test_run_benchmark_breaks_down_by_category(db: StandardDatabase) -> None:
    sample = Sample(
        sample_id="bench_cat",
        sessions=[[Turn(speaker="User", text="Alice adopted a dog named Rex last spring.")]],
        qa=[
            QA(question="What is the dog's name?", answer="Rex",
               gold_fact="dog named Rex", category="single-hop"),
            QA(question="When did Alice adopt the dog?", answer="last spring",
               gold_fact="named Rex last spring", category="temporal"),
        ],
    )
    report = run_benchmark(db, [sample], k=10)
    assert set(report.per_category) == {"single-hop", "temporal"}
    assert report.per_category["single-hop"]["n"] == 1.0


def test_cli_parser() -> None:
    args = _build_parser().parse_args(["data.json", "--mode", "full", "--k", "5"])
    assert args.dataset == "data.json"
    assert args.mode == "full"
    assert args.k == 5
