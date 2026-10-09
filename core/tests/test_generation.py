"""Unit tests for the pluggable generator (no container)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from arango_memory.config import Settings
from arango_memory.generation import AnthropicGenerator, FakeGenerator, get_generator


def test_fake_generator_default_is_empty() -> None:
    gen = FakeGenerator()
    assert gen.complete("hi", system="s") == ""
    assert gen.model == "fake-llm"


def test_fake_generator_handler_receives_prompt_and_system() -> None:
    gen = FakeGenerator(handler=lambda prompt, system: f"{system}|{prompt}")
    assert gen.complete("q", system="sys") == "sys|q"


def test_get_generator_fake_from_config() -> None:
    assert get_generator(Settings(generation_provider="fake")).model == "fake-llm"


def test_get_generator_anthropic_requires_key() -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        get_generator(Settings(generation_provider="anthropic", anthropic_api_key=None))


class _StubMessages:
    def __init__(self, content: list[object]) -> None:
        self.calls: list[dict[str, object]] = []
        self._content = content

    def create(self, **kw: object) -> object:
        self.calls.append(kw)
        return SimpleNamespace(content=self._content)


def _stubbed(thinking: bool, content: list[object]) -> tuple[AnthropicGenerator, _StubMessages]:
    gen = AnthropicGenerator(api_key="test-not-a-key", thinking=thinking)
    stub = _StubMessages(content)
    gen._client = SimpleNamespace(messages=stub)  # type: ignore[assignment]
    return gen, stub


def test_anthropic_defaults_to_haiku_5_5_with_thinking_off() -> None:
    cfg = Settings(generation_provider="anthropic", anthropic_api_key="test-not-a-key")
    gen = get_generator(cfg)
    assert gen.model == "claude-haiku-5-5"
    gen, stub = _stubbed(False, [SimpleNamespace(type="text", text="YES")])
    assert gen.complete("q", system="s", max_tokens=32) == "YES"
    assert stub.calls[0]["thinking"] == {"type": "disabled"}
    assert stub.calls[0]["model"] == "claude-haiku-5-5"
    assert "temperature" not in stub.calls[0]


def test_anthropic_thinking_on_omits_the_param_and_reads_text_blocks_only() -> None:
    blocks = [SimpleNamespace(type="thinking", thinking=""),
              SimpleNamespace(type="text", text="ok")]
    gen, stub = _stubbed(True, blocks)
    assert gen.complete("q") == "ok"
    assert "thinking" not in stub.calls[0]
