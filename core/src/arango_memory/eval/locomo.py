"""LoCoMo-style runner: ingest multi-session conversations, query, score.

Deliberately small and dependency-free. Scoring is lite-mode-appropriate:
Recall@k (did retrieval surface the supporting fact) is the primary metric;
token-level F1 of the top hit against the gold answer is a secondary signal.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from arango.database import StandardDatabase

from ..ingest.store import store
from ..retrieve.search import retrieve


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str


@dataclass(frozen=True)
class QA:
    question: str
    answer: str
    gold_fact: str  # substring expected to appear in a retrieved memory


@dataclass(frozen=True)
class Sample:
    sample_id: str
    sessions: list[list[Turn]]
    qa: list[QA]


@dataclass(frozen=True)
class QuestionScore:
    question: str
    recall_hit: bool
    f1: float


@dataclass
class EvalResult:
    sample_id: str
    k: int
    questions: list[QuestionScore] = field(default_factory=list)

    @property
    def recall_at_k(self) -> float:
        if not self.questions:
            return 0.0
        return sum(q.recall_hit for q in self.questions) / len(self.questions)

    @property
    def mean_f1(self) -> float:
        if not self.questions:
            return 0.0
        return sum(q.f1 for q in self.questions) / len(self.questions)


def load_dataset(path: str | Path) -> list[Sample]:
    """Load a LoCoMo-style dataset from JSON."""
    raw = json.loads(Path(path).read_text())
    samples: list[Sample] = []
    for s in raw["samples"]:
        sessions = [[Turn(**turn) for turn in session] for session in s["sessions"]]
        qa = [QA(**item) for item in s["qa"]]
        samples.append(Sample(sample_id=s["sample_id"], sessions=sessions, qa=qa))
    return samples


def _normalize(text: str) -> list[str]:
    return "".join(c.lower() if c.isalnum() else " " for c in text).split()


def _token_f1(predicted: str, gold: str) -> float:
    pred_tokens = _normalize(predicted)
    gold_tokens = _normalize(gold)
    if not pred_tokens or not gold_tokens:
        return 0.0
    common = 0
    remaining = list(gold_tokens)
    for tok in pred_tokens:
        if tok in remaining:
            common += 1
            remaining.remove(tok)
    if common == 0:
        return 0.0
    precision = common / len(pred_tokens)
    recall = common / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def _recall_hit(hit_texts: Sequence[str], gold_fact: str) -> bool:
    needle = " ".join(_normalize(gold_fact))
    return any(needle in " ".join(_normalize(text)) for text in hit_texts)


def run_eval(
    db: StandardDatabase,
    sample: Sample,
    *,
    agent_id: str = "assistant",
    k: int = 10,
    max_memory_tokens: int = 1500,
    consistency_attempts: int = 30,
    consistency_delay: float = 0.25,
) -> EvalResult:
    """Ingest a sample's sessions (tenant = sample_id), then score its QA pairs."""
    turn_index = 0
    for session in sample.sessions:
        for turn in session:
            store(
                db,
                content=f"{turn.speaker}: {turn.text}",
                tenant_id=sample.sample_id,
                agent_id=agent_id,
                turn_index=turn_index,
            )
            turn_index += 1

    _await_consistency(
        db, sample, agent_id, attempts=consistency_attempts, delay=consistency_delay
    )

    result = EvalResult(sample_id=sample.sample_id, k=k)
    for qa in sample.qa:
        retrieved = retrieve(
            db,
            query=qa.question,
            tenant_id=sample.sample_id,
            agent_id=agent_id,
            k=k,
            max_memory_tokens=max_memory_tokens,
        )
        hit_texts = [h.text for h in retrieved.hits]
        top = hit_texts[0] if hit_texts else ""
        result.questions.append(
            QuestionScore(
                question=qa.question,
                recall_hit=_recall_hit(hit_texts, qa.gold_fact),
                f1=_token_f1(top, qa.answer),
            )
        )
    return result


def _await_consistency(
    db: StandardDatabase, sample: Sample, agent_id: str, *, attempts: int, delay: float
) -> None:
    """Wait for the ArangoSearch view to reflect the last ingested turn."""
    if not sample.qa:
        return
    probe = sample.qa[0].question
    for _ in range(attempts):
        if retrieve(db, query=probe, tenant_id=sample.sample_id, agent_id=agent_id).hits:
            return
        time.sleep(delay)
