"""Full-mode retrieval enrichment: adaptive gate + HyDE (DESIGN.md §9 stages 1–2).

Both involve an LLM call and are cached per query so repeats are free (§9, §16).
Lite mode skips this module entirely. The cache is in-process for now; a durable
cache is an ops concern for later.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ..embedding import Embedder
from ..generation import Generator
from ..telemetry import metrics

_HYDE_SYSTEM = (
    "Generate a concise, plausible answer to the user's question as if recalling it "
    "from memory. Write 1-2 sentences. Do not hedge or add caveats; the text is used "
    "only to retrieve relevant stored memories."
)

_GATE_SYSTEM = (
    "Decide whether answering the user's message needs stored personal/contextual "
    "memory. Reply with exactly 'SKIP' if it can be answered from general knowledge "
    "alone, or 'RETRIEVE' if stored memory would help."
)


@dataclass(frozen=True)
class HydeResult:
    hypothetical: str
    embedding: list[float]


class QueryCache:
    """In-process cache for HyDE results, adaptive-gate, and decomposition, keyed by query."""

    def __init__(self) -> None:
        self._hyde: dict[str, HydeResult] = {}
        self._gate: dict[str, bool] = {}
        self._decompose: dict[str, list[str]] = {}
        self._hits = 0
        self._lookups = 0

    @property
    def hit_rate(self) -> float:
        return self._hits / self._lookups if self._lookups else 0.0

    def _record(self, hit: bool) -> None:  # noqa: FBT001
        self._lookups += 1
        self._hits += int(hit)
        metrics.emit("cache", hit=hit, hit_rate=self.hit_rate)

    def get_hyde(self, query: str) -> HydeResult | None:
        value = self._hyde.get(query)
        self._record(value is not None)
        return value

    def set_hyde(self, query: str, result: HydeResult) -> None:
        self._hyde[query] = result

    def get_gate(self, query: str) -> bool | None:
        value = self._gate.get(query)
        self._record(value is not None)
        return value

    def set_gate(self, query: str, skip: bool) -> None:
        self._gate[query] = skip

    def get_decompose(self, query: str) -> list[str] | None:
        value = self._decompose.get(query)
        self._record(value is not None)
        return value

    def set_decompose(self, query: str, subqueries: list[str]) -> None:
        self._decompose[query] = subqueries


def should_skip_retrieval(
    query: str, *, generator: Generator, cache: QueryCache | None = None
) -> bool:
    """Adaptive gate: True if the model is confident no stored memory is needed.

    The gate is a *cost* optimization — it spends an LLM call to avoid a retrieval. With
    `adaptive_gate=false` it never skips and makes no call, which is what you want when
    every turn needs memory (QA/eval workloads), or when a wrong SKIP is more expensive
    than the retrieval it saves: a skip returns an empty result, so a false SKIP is an
    unrecoverable miss.
    """
    if not settings.adaptive_gate:
        return False
    if cache is not None and (cached := cache.get_gate(query)) is not None:
        return cached
    verdict = generator.complete(query, system=_GATE_SYSTEM).strip().upper()
    skip = verdict.startswith("SKIP")
    if cache is not None:
        cache.set_gate(query, skip)
    return skip


def hyde(
    query: str, *, generator: Generator, embedder: Embedder, cache: QueryCache | None = None
) -> HydeResult:
    """Embed a hypothetical answer instead of the question (§9 stage 2).

    If the generator returns nothing, fall back to embedding the raw query — so
    full mode degrades gracefully to the lite vector path.
    """
    if cache is not None and (cached := cache.get_hyde(query)) is not None:
        return cached
    hypothetical = generator.complete(query, system=_HYDE_SYSTEM).strip() or query
    result = HydeResult(hypothetical=hypothetical, embedding=embedder.embed(hypothetical))
    if cache is not None:
        cache.set_hyde(query, result)
    return result
