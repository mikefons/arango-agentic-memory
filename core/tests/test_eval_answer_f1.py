"""The benchmark scores F1 on a *generated* answer, not the raw retrieved turn. No DB."""

from __future__ import annotations

from arango_memory.eval.locomo import _answer, _token_f1
from arango_memory.generation import FakeGenerator


def test_answer_generates_from_context_and_feeds_the_prompt() -> None:
    seen: dict[str, str | None] = {}

    def handler(prompt: str, system: str | None) -> str:
        seen["prompt"] = prompt
        seen["system"] = system
        return "March 2021"

    ans = _answer(
        "When did Alice join?",
        context="Alice: I joined in March 2021.",
        generator=FakeGenerator(handler=handler),
    )
    assert ans == "March 2021"
    assert "Alice: I joined in March 2021." in (seen["prompt"] or "")  # context is injected
    assert "When did Alice join?" in (seen["prompt"] or "")
    assert "unknown" in (seen["system"] or "").lower()  # answers the actual QA system prompt


def test_generated_answer_scores_far_higher_f1_than_the_raw_turn() -> None:
    # The core reason the old harness could never hit the F1 target: a long retrieved turn
    # vs a short gold answer has near-zero precision. A concise generated answer does not.
    gold = "March 2021"
    raw_turn = "Alice: honestly it's been ages, I think I joined the company in March 2021 or so"
    generated = "March 2021"
    assert _token_f1(generated, gold) == 1.0
    assert _token_f1(raw_turn, gold) < 0.3


def test_answer_returns_empty_on_generator_failure() -> None:
    def boom(prompt: str, system: str | None) -> str:
        raise RuntimeError("provider down")

    assert _answer("q", context="c", generator=FakeGenerator(handler=boom)) == ""
