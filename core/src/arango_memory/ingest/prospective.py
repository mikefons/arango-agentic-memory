"""Prospective indexing (DESIGN.md §8 Stage 4) — full mode only.

At write time, generate a few hypothetical future questions this memory answers
and store them on the memory so the search view can match a later query against
them. With the default fake generator this returns nothing (full mode degrades
to plain indexing); a real/scripted generator drives it.
"""

from __future__ import annotations

from ..generation import Generator

_SYSTEM = (
    "List 2-3 short, distinct questions a user might later ask that the following "
    "text answers. One question per line, no numbering or extra commentary."
)


def generate_prospective(content: str, generator: Generator) -> list[str]:
    out = generator.complete(content, system=_SYSTEM).strip()
    if not out:
        return []
    questions = [line.strip("-•* \t") for line in out.splitlines() if line.strip()]
    return [q for q in questions if q][:3]
