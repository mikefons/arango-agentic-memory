"""Minimal LoCoMo-style eval harness (DESIGN.md §22, §23).

Step 1 scope: lite/BM25 only. Loads multi-session conversations, ingests them,
runs the QA queries, and scores Recall@k plus a token-level F1 on the top hit.
Full-vs-lite comparison lands once full mode exists (Step 2).
"""

from .locomo import EvalResult, QuestionScore, Sample, load_dataset, run_eval

__all__ = ["EvalResult", "QuestionScore", "Sample", "load_dataset", "run_eval"]
