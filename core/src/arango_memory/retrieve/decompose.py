"""Multi-hop query decomposition (RQ-1, DESIGN.md §9).

Splits a query into the minimal set of *independent* sub-lookups so the multihop
retrieve mode can gather a cross-turn evidence chain a single top-k pass can't.
One LLM call, cached per query like HyDE / the adaptive gate (enrich.py).

Fails safe: a decomposition into 0 or 1 lookups (or a generator error) returns the
original query unchanged — the caller reads `len <= 1` as "run the single-shot path",
so a mis-fire never scores below single-shot retrieval.
"""

from __future__ import annotations

from ..config import settings
from ..generation import Generator
from ..telemetry import metrics
from .enrich import QueryCache

_DECOMPOSE_SYSTEM = (
    "Break the user's question into the minimal set of independent factual lookups "
    "needed to answer it. Each lookup must stand alone — a self-contained question that "
    "can be searched on its own, with no pronouns referring to the others. Output one "
    "lookup per line, nothing else. If the question is already a single lookup, return "
    "it unchanged on one line."
)

# Leading list markers a model may emit despite the "one per line" instruction.
_MARKERS = ("- ", "* ", "• ", "· ")


def _clean(line: str) -> str:
    """Strip whitespace and a leading bullet / "1." style enumerator from one line."""
    text = line.strip()
    for marker in _MARKERS:
        if text.startswith(marker):
            text = text[len(marker) :].strip()
            break
    else:
        head, sep, rest = text.partition(". ")
        if sep and head.isdigit():
            text = rest.strip()
    return text


def _parse(raw: str, cap: int) -> list[str]:
    """Parse the generator output into de-duplicated, capped sub-queries."""
    seen: set[str] = set()
    out: list[str] = []
    for line in raw.splitlines():
        sub = _clean(line)
        if not sub:
            continue
        key = sub.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(sub)
        if len(out) >= cap:
            break
    return out


def decompose(
    query: str, *, generator: Generator, cache: QueryCache | None = None
) -> list[str]:
    """Return independent sub-queries for `query`, or `[query]` to signal single-shot.

    The list has 2+ entries only when the model found genuinely separable lookups;
    otherwise it is `[query]` (the transparent-fallback signal). Capped at
    `settings.decompose_max_subqueries`.
    """
    if cache is not None and (cached := cache.get_decompose(query)) is not None:
        return cached
    try:
        raw = generator.complete(query, system=_DECOMPOSE_SYSTEM)
    except Exception as exc:  # noqa: BLE001 — a decompose hiccup falls back, never breaks retrieval
        metrics.emit("degraded", op="decompose", reason=type(exc).__name__)
        return [query]
    subs = _parse(raw, settings.decompose_max_subqueries)
    result = subs if len(subs) > 1 else [query]
    metrics.emit("decompose", subqueries=len(result), decomposed=len(result) > 1)
    if cache is not None:
        cache.set_decompose(query, result)
    return result
