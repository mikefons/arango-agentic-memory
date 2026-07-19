"""LoCoMo-style runner: ingest multi-session conversations, query, score.

Deliberately small and dependency-free. Two metrics:
  - **Recall@k** — did retrieval surface the supporting fact (retrieval quality).
  - **token-F1** — overlap of the *answer* with the gold answer (end-to-end quality).
    When a real generator is configured, the answer is generated from the retrieved
    context (the metric this benchmark is about). With the fake generator there is no
    answer to score, so F1 falls back to the top retrieved turn — a weak proxy that
    can't approach the §23 target (a full turn vs a short gold answer), so read F1 only
    on a real-generator run.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from arango.database import StandardDatabase

from ..config import settings
from ..generation import Generator, get_generator
from ..ingest.store import store
from ..retrieve.search import retrieve

_ANSWER_SYSTEM = (
    "You answer a question using ONLY the provided conversation memory. Reply with the "
    "shortest phrase that answers it — a name, date, place, or a few words. If the memory "
    "does not contain the answer, reply 'unknown'. Do not explain."
)


def _answer(question: str, context: str, generator: Generator) -> str:
    """Generate a concise answer from the retrieved context (end-to-end F1 input)."""
    prompt = f"Memory:\n{context}\n\nQuestion: {question}\nAnswer:"
    try:
        return generator.complete(prompt, system=_ANSWER_SYSTEM).strip()
    except Exception:  # noqa: BLE001 — a generation hiccup scores 0, never breaks the run
        return ""


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str


@dataclass(frozen=True)
class QA:
    question: str
    answer: str
    # Evidence retrieval must surface. `gold_fact` is the single-evidence form (LoCoMo);
    # `gold_facts` is the multi-evidence support set (MuSiQue, BX-1). `support()` unifies
    # them — recall is scored over that set, so single-fact runs are unchanged.
    gold_fact: str = ""  # substring expected to appear in a retrieved memory
    gold_facts: list[str] = field(default_factory=list)  # multi-evidence support set
    category: str | None = None  # e.g. single-hop | multi-hop | temporal (LoCoMo)

    def support(self) -> list[str]:
        """The evidence set retrieval is scored against (multi-evidence or single-fact)."""
        return self.gold_facts or ([self.gold_fact] if self.gold_fact else [])


@dataclass(frozen=True)
class Sample:
    sample_id: str
    sessions: list[list[Turn]]
    qa: list[QA]


@dataclass(frozen=True)
class QuestionScore:
    question: str
    recall_hit: bool  # all-hops-present: the *entire* support set was retrieved
    f1: float
    category: str | None = None
    tokens_injected: int = 0
    recall_fraction: float = 0.0  # fraction of the support set retrieved (BX-1)


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

    @property
    def mean_recall_fraction(self) -> float:
        """Graded recall: mean fraction of each question's support set retrieved (BX-1)."""
        if not self.questions:
            return 0.0
        return sum(q.recall_fraction for q in self.questions) / len(self.questions)


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


def _recall_fraction(hit_texts: Sequence[str], support: Sequence[str]) -> float:
    """Fraction of the support set retrieved (BX-1). For a single-fact support this is
    0.0/1.0 — identical to `_recall_hit` — so LoCoMo scoring is unchanged; for MuSiQue's
    multi-evidence support it grades partial retrieval of the chain."""
    if not support:
        return 0.0
    return sum(_recall_hit(hit_texts, fact) for fact in support) / len(support)


def run_eval(
    db: StandardDatabase,
    sample: Sample,
    *,
    agent_id: str = "assistant",
    mode: str = "lite",
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

    # Score F1 against a generated answer when a real generator is configured; otherwise
    # fall back to the top retrieved turn (a weak proxy — see the module docstring).
    generator = get_generator()
    answer_from_generation = settings.generation_provider != "fake"

    result = EvalResult(sample_id=sample.sample_id, k=k)
    for qa in sample.qa:
        retrieved = retrieve(
            db,
            query=qa.question,
            tenant_id=sample.sample_id,
            agent_id=agent_id,
            mode=mode,
            k=k,
            max_memory_tokens=max_memory_tokens,
        )
        hit_texts = [h.text for h in retrieved.hits]
        if answer_from_generation:
            predicted = _answer(qa.question, retrieved.context, generator)
        else:
            predicted = hit_texts[0] if hit_texts else ""
        support = qa.support()
        fraction = _recall_fraction(hit_texts, support)
        result.questions.append(
            QuestionScore(
                question=qa.question,
                # all-hops-present: for single-fact support this equals the old _recall_hit
                recall_hit=bool(support) and fraction == 1.0,
                f1=_token_f1(predicted, qa.answer),
                category=qa.category,
                tokens_injected=retrieved.tokens_injected,
                recall_fraction=fraction,
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
