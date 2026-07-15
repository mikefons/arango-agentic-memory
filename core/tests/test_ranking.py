"""Unit tests for RRF fusion, MMR diversity, and tiered budget assembly (no container)."""

from __future__ import annotations

from typing import Any

from arango_memory.retrieve.search import (
    _assemble_tiered,
    _Candidate,
    _mmr,
    _rrf_fuse,
)


def _row(key: str, text: str = "", emb: list[float] | None = None) -> dict[str, Any]:
    return {"key": key, "text": text or key, "embedding": emb or [], "type": "episodic"}


def test_rrf_rewards_documents_present_in_both_lists() -> None:
    bm25 = [_row("a"), _row("b"), _row("c")]
    vector = [_row("a")]  # 'a' is top in both signals
    fused = _rrf_fuse([bm25, vector], ["bm25", "vector"])

    assert fused[0].key == "a"
    assert fused[0].signals == {"bm25", "vector"}
    # 'a' (in both) outranks 'b'/'c' (bm25 only)
    assert fused[0].fused_score > fused[1].fused_score


def test_mmr_selects_most_relevant_first_caps_k_and_dedupes() -> None:
    # Relevance is the fused score (not query cosine); b has the highest → picked first.
    cands = [
        _Candidate("a", "a", [0.2, 0.98], "episodic", fused_score=0.3),
        _Candidate("b", "b", [1.0, 0.0], "episodic", fused_score=1.0),
        _Candidate("c", "c", [0.0, 1.0], "episodic", fused_score=0.5),
    ]
    selected = _mmr(cands, k=2)
    assert len(selected) == 2
    assert selected[0].key == "b"
    assert len({c.key for c in selected}) == 2


def test_assemble_tiered_respects_token_budget() -> None:
    cands = [
        _Candidate(f"k{i}", "word " * 50, [], "episodic", fused_score=1.0 - i * 0.01)
        for i in range(20)
    ]
    context, tokens = _assemble_tiered(cands, max_tokens=60)
    assert 0 < tokens <= 60
    assert context.startswith("- ")
