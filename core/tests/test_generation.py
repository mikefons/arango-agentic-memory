"""Unit tests for the pluggable generator (no container)."""

from __future__ import annotations

import pytest

from arango_memory.config import Settings
from arango_memory.generation import FakeGenerator, get_generator


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
