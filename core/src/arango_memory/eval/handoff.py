"""Multi-agent handoff eval (MA-5, §14/§22).

Proves the headline claim: agent A writes, agent B picks up the next job and gets the
context it needs. A writer ingests facts + tool runs under its agent_id; a reader
(different agent_id) `prime`s (or `retrieve`s) across `read_agent_ids` and we score
whether the briefing contains the gold facts + tool runs. Read-your-writes is forced
(no polling) via `force_view_sync`, so the run is deterministic and keyless.

CLI: `python -m arango_memory.eval.handoff <dataset.json> [--mode]` — exits nonzero
below the §23 targets, so it gates a nightly/BYO run; the smoke slice also runs in CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from arango.database import StandardDatabase

from ..client import ArangoMemoryClient
from ..ingest.procedural import record_step
from ..ingest.store import store
from ..retrieve.prime import prime
from ..retrieve.search import force_view_sync, retrieve
from ..schema.collections import ensure_schema
from .locomo import _recall_hit

# §23 handoff targets (keyless smoke slice; tighten with real data).
CONTEXT_RECALL_MIN = 0.8
PROCEDURAL_RECALL_MIN = 0.6


@dataclass(frozen=True)
class Writer:
    agent_id: str
    facts: list[str] = field(default_factory=list)
    steps: list[dict[str, str]] = field(default_factory=list)  # {tool_name, outcome}


@dataclass(frozen=True)
class Reader:
    agent_id: str
    task: str
    read_agent_ids: list[str] = field(default_factory=list)
    via: str = "prime"  # "prime" | "retrieve"
    max_memory_tokens: int = 1500


@dataclass(frozen=True)
class HandoffScenario:
    id: str
    tenant_id: str
    writers: list[Writer]
    reader: Reader
    gold_facts: list[str] = field(default_factory=list)
    gold_tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioScore:
    id: str
    context_recall: float
    procedural_recall: float | None  # None when the scenario asserts no tools
    tokens_injected: int


@dataclass
class HandoffReport:
    scores: list[ScenarioScore] = field(default_factory=list)
    passed: bool = False
    failures: list[str] = field(default_factory=list)

    @property
    def mean_context_recall(self) -> float:
        return _mean([s.context_recall for s in self.scores])

    @property
    def mean_procedural_recall(self) -> float:
        graded = [s.procedural_recall for s in self.scores if s.procedural_recall is not None]
        return _mean(graded) if graded else 1.0


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _context_recall(context: str, gold_facts: Sequence[str]) -> float:
    """Fraction of gold facts present in the assembled briefing (what B actually reads)."""
    if not gold_facts:
        return 1.0
    return sum(_recall_hit([context], fact) for fact in gold_facts) / len(gold_facts)


def _procedural_recall(
    steps: Sequence[dict[str, object]], gold_tools: Sequence[str]
) -> float | None:
    if not gold_tools:
        return None
    seen = {str(s.get("tool_name", "")) for s in steps}
    return sum(tool in seen for tool in gold_tools) / len(gold_tools)


def load_scenarios(path: str | Path) -> list[HandoffScenario]:
    raw = json.loads(Path(path).read_text())
    scenarios: list[HandoffScenario] = []
    for s in raw["scenarios"]:
        scenarios.append(
            HandoffScenario(
                id=s["id"],
                tenant_id=s["tenant_id"],
                writers=[Writer(**w) for w in s["writers"]],
                reader=Reader(**s["reader"]),
                gold_facts=s.get("gold_facts", []),
                gold_tools=s.get("gold_tools", []),
            )
        )
    return scenarios


def run_scenario(db: StandardDatabase, scenario: HandoffScenario) -> ScenarioScore:
    """Ingest the writers, force read-your-writes, then score the reader's briefing."""
    for i, writer in enumerate(scenario.writers):
        for j, fact in enumerate(writer.facts):
            store(db, content=fact, tenant_id=scenario.tenant_id,
                  agent_id=writer.agent_id, turn_index=i * 1000 + j)
        for step in writer.steps:
            record_step(db, tool_name=step["tool_name"], arguments={},
                        outcome=step.get("outcome", "success"),
                        tenant_id=scenario.tenant_id, agent_id=writer.agent_id)
    force_view_sync(db, scenario.tenant_id)  # MA-1 barrier — no polling

    r = scenario.reader
    if r.via == "retrieve":
        res = retrieve(db, query=r.task, tenant_id=scenario.tenant_id, agent_id=r.agent_id,
                       read_agent_ids=r.read_agent_ids, max_memory_tokens=r.max_memory_tokens)
        context, steps, tokens = res.context, [], res.tokens_injected
    else:
        brief = prime(db, task=r.task, tenant_id=scenario.tenant_id, agent_id=r.agent_id,
                      read_agent_ids=r.read_agent_ids, max_memory_tokens=r.max_memory_tokens)
        context, steps, tokens = brief.context, brief.steps, brief.tokens_injected

    return ScenarioScore(
        id=scenario.id,
        context_recall=_context_recall(context, scenario.gold_facts),
        procedural_recall=_procedural_recall(steps, scenario.gold_tools),
        tokens_injected=tokens,
    )


def run_handoff(
    db: StandardDatabase, scenarios: Sequence[HandoffScenario], *, progress: bool = False
) -> HandoffReport:
    scores: list[ScenarioScore] = []
    for i, scenario in enumerate(scenarios, 1):
        if progress:
            print(f"[{i}/{len(scenarios)}] {scenario.id}: {len(scenario.writers)} writer(s) "
                  f"→ {scenario.reader.agent_id} via {scenario.reader.via}…",
                  file=sys.stderr, flush=True)
        scores.append(run_scenario(db, scenario))

    report = HandoffReport(scores=scores)
    failures: list[str] = []
    if report.mean_context_recall < CONTEXT_RECALL_MIN:
        failures.append(f"context recall {report.mean_context_recall:.2f} < {CONTEXT_RECALL_MIN}")
    if report.mean_procedural_recall < PROCEDURAL_RECALL_MIN:
        failures.append(
            f"procedural recall {report.mean_procedural_recall:.2f} < {PROCEDURAL_RECALL_MIN}"
        )
    report.failures = failures
    report.passed = not failures
    return report


def _format(report: HandoffReport) -> str:
    ctx, proc = report.mean_context_recall, report.mean_procedural_recall
    lines = [
        f"scenarios:          {len(report.scores)}",
        f"context recall:     {ctx:.3f}  (target ≥ {CONTEXT_RECALL_MIN})",
        f"procedural recall:  {proc:.3f}  (target ≥ {PROCEDURAL_RECALL_MIN})",
    ]
    for s in report.scores:
        proc_str = "n/a" if s.procedural_recall is None else f"{s.procedural_recall:.2f}"
        lines.append(f"  [{s.id}] context={s.context_recall:.2f} procedural={proc_str} "
                     f"tokens={s.tokens_injected}")
    lines.append("PASS" if report.passed else "FAIL: " + "; ".join(report.failures))
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arango_memory.eval.handoff")
    parser.add_argument("dataset", help="path to a handoff-scenario dataset JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    db = ArangoMemoryClient().connect()
    ensure_schema(db)
    report = run_handoff(db, load_scenarios(args.dataset), progress=True)
    print(_format(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
