"""Multi-agent handoff eval (MA-5, §14/§22).

The scenario the whole coordination layer exists for: agent A writes, agent B primes
across the shared tier and gets A's facts + tool runs. This is both a unit test of the
scorers and the CI gate over the smoke slice — reverting MA-2's cross-agent read (or
MA-1's barrier) turns it red.
"""

from __future__ import annotations

from pathlib import Path

from arango.database import StandardDatabase

from arango_memory.eval.handoff import (
    _context_recall,
    _procedural_recall,
    load_scenarios,
    run_handoff,
    run_scenario,
)

SMOKE = Path(__file__).parent / "data" / "handoff_smoke.json"


# ── pure scorers ──────────────────────────────────────────
def test_context_recall_counts_gold_facts_present() -> None:
    ctx = "## Relevant history\n- the cook was near the vault"
    assert _context_recall(ctx, ["cook", "vault"]) == 1.0
    assert _context_recall(ctx, ["cook", "dragon"]) == 0.5
    assert _context_recall("", ["anything"]) == 0.0
    assert _context_recall("", []) == 1.0  # nothing to find → trivially satisfied


def test_procedural_recall_matches_tool_names() -> None:
    steps = [{"tool_name": "confront"}, {"tool_name": "search"}]
    assert _procedural_recall(steps, ["confront"]) == 1.0
    assert _procedural_recall(steps, ["confront", "dig"]) == 0.5
    assert _procedural_recall(steps, []) is None  # scenario asserts no tools → not graded


# ── the money test: A writes → B reads ────────────────────
def test_clean_handoff_a_writes_b_reads(db: StandardDatabase) -> None:
    (clean,) = [s for s in load_scenarios(SMOKE) if s.id == "clean-handoff"]
    score = run_scenario(db, clean)
    assert score.context_recall == 1.0, "B's briefing is missing A's facts"
    assert score.procedural_recall == 1.0, "B's briefing is missing A's tool run"


def test_smoke_slice_meets_targets(db: StandardDatabase) -> None:
    report = run_handoff(db, load_scenarios(SMOKE))
    assert report.passed, f"handoff targets missed: {report.failures}"
    assert report.mean_context_recall >= 0.8
    assert report.mean_procedural_recall >= 0.6
