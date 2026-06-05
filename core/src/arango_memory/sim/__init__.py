"""Deterministic agentic simulation harness (DESIGN.md §22, Step 3.5a).

Plays a realistic agent loop — multi-session conversations with interleaved tool
calls — against the core's HTTP surface (the same endpoints the Vercel adapter
calls), with stubbed models so runs are reproducible and keyless. It is the
real-data regression gate for memory *and* actions. The reference Vercel app
(Step 3.5b) covers the live, non-deterministic end-to-end path.
"""

from .runner import SimResult, run_scenario
from .scenario import QA, Scenario, ToolCall, Turn, load_scenario

__all__ = ["QA", "Scenario", "SimResult", "ToolCall", "Turn", "load_scenario", "run_scenario"]
