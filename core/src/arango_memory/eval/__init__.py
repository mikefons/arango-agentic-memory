"""LoCoMo-style eval + benchmark harness (DESIGN.md §22, §23).

`locomo` is the per-sample runner (ingest → query → Recall@k + token-F1);
`benchmark` aggregates across samples into overall + per-category metrics and
compares them to the §23 targets.
"""

from .benchmark import BenchmarkReport, run_benchmark
from .locomo import QA, EvalResult, QuestionScore, Sample, load_dataset, run_eval

__all__ = [
    "QA",
    "BenchmarkReport",
    "EvalResult",
    "QuestionScore",
    "Sample",
    "load_dataset",
    "run_benchmark",
    "run_eval",
]
