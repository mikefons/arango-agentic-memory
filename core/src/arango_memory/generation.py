"""Pluggable text generation for full-mode enrichment (DESIGN.md §9, §16).

Used by HyDE and the adaptive gate (Step 2b). Sync, matching the rest of the
core path. Two implementations:
  - `FakeGenerator`     — deterministic; an optional `handler(prompt, system)`
                          lets tests script responses. Default returns "", which
                          the enrichment layer treats as "no opinion" (gate →
                          retrieve, HyDE → fall back to the raw query). No key.
  - `AnthropicGenerator`— real completions via `claude-haiku-4-5`, with prompt
                          caching on the system block (background work, §16).

`get_generator(settings)` selects one; "anthropic" without a key is a hard error.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .config import Settings, settings


@runtime_checkable
class Generator(Protocol):
    model: str

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 512) -> str: ...


class FakeGenerator:
    """Deterministic generator. `handler` (if given) maps (prompt, system) → text."""

    def __init__(
        self,
        handler: Callable[[str, str | None], str] | None = None,
        model: str = "fake-llm",
    ) -> None:
        self._handler = handler
        self.model = model

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 512) -> str:
        return self._handler(prompt, system) if self._handler else ""


class AnthropicGenerator:
    """Real completions via the Anthropic API (default `claude-haiku-4-5`)."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5") -> None:
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self.model = model

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 512) -> str:
        system_blocks = (
            [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if system
            else []
        )
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_blocks,  # type: ignore[arg-type]
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [block.text for block in resp.content if block.type == "text"]
        return "".join(parts)


def get_generator(config: Settings | None = None) -> Generator:
    """Build the configured generator. Selecting Anthropic without a key is an error."""
    cfg = config or settings
    if cfg.generation_provider == "fake":
        return FakeGenerator()
    if not cfg.anthropic_api_key:
        raise RuntimeError(
            "generation_provider='anthropic' but ANTHROPIC_API_KEY is unset; "
            "set the key or use generation_provider='fake'."
        )
    return AnthropicGenerator(api_key=cfg.anthropic_api_key, model=cfg.background_model)
