"""Simulation scenario model + loader (DESIGN.md §22).

A scenario is a multi-session conversation where turns may carry tool calls
(which become procedural memory) plus QA probes that test cross-session recall.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    arguments: dict[str, Any]
    outcome: str  # "success" | "failure"


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str
    tools: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class QA:
    question: str
    answer: str
    gold_fact: str


@dataclass(frozen=True)
class Scenario:
    scenario_id: str  # also used as the tenant id
    sessions: list[list[Turn]]
    qa: list[QA]


def load_scenario(path: str | Path) -> Scenario:
    raw = json.loads(Path(path).read_text())
    sessions = [
        [
            Turn(
                speaker=t["speaker"],
                text=t["text"],
                tools=[ToolCall(**tc) for tc in t.get("tools", [])],
            )
            for t in session
        ]
        for session in raw["sessions"]
    ]
    return Scenario(
        scenario_id=raw["scenario_id"],
        sessions=sessions,
        qa=[QA(**item) for item in raw["qa"]],
    )
