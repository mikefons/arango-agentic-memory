"""Multi-evidence recall metric + QA.support() (BX-1). No DB."""

from __future__ import annotations

from arango_memory.eval.locomo import QA, _recall_fraction, _recall_hit


def test_support_prefers_gold_facts_then_falls_back_to_gold_fact() -> None:
    assert QA("q", "a", gold_facts=["x", "y"]).support() == ["x", "y"]
    assert QA("q", "a", gold_fact="only").support() == ["only"]  # single-fact (LoCoMo)
    assert QA("q", "a").support() == []  # neither set


def test_single_fact_fraction_matches_recall_hit() -> None:
    # Backward-compat: for a 1-element support the fraction is exactly the old boolean.
    hits = ["Alice joined Acme in 2021"]
    present = QA("q", "a", gold_fact="joined Acme").support()
    absent = QA("q", "a", gold_fact="left Acme").support()
    assert _recall_fraction(hits, present) == 1.0
    assert _recall_fraction(hits, absent) == 0.0
    assert _recall_fraction(hits, present) == float(_recall_hit(hits, "joined Acme"))


def test_multi_evidence_fraction_grades_partial_retrieval() -> None:
    hits = ["Alice met Bob at the reunion", "Bob is an astronaut"]
    # both supporting turns present → 1.0
    assert _recall_fraction(hits, ["met Bob at the reunion", "Bob is an astronaut"]) == 1.0
    # one of two present → 0.5 (the multi-hop chain only half-retrieved)
    assert _recall_fraction(hits, ["met Bob at the reunion", "Bob works at Acme"]) == 0.5
    # none present → 0.0
    assert _recall_fraction(hits, ["Carol is a pilot", "Dave sails"]) == 0.0


def test_empty_support_is_zero() -> None:
    assert _recall_fraction(["anything"], []) == 0.0
