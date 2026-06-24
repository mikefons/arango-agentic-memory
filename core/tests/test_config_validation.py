"""Startup cross-field config validation (DESIGN.md §17). No DB."""

from __future__ import annotations

import pytest

import arango_memory.api.app as app_mod
from arango_memory.config import settings


def test_oidc_without_audience_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(app_mod.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    monkeypatch.setattr(settings, "oidc_issuer", "https://issuer.test")
    monkeypatch.setattr(settings, "oidc_audience", None)

    app_mod._warn_on_risky_config()
    assert any("aud" in w.lower() for w in warnings)  # flagged: audience unverified


def test_oidc_with_audience_is_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(app_mod.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    monkeypatch.setattr(settings, "oidc_issuer", "https://issuer.test")
    monkeypatch.setattr(settings, "oidc_audience", "arango-memory")

    app_mod._warn_on_risky_config()
    assert warnings == []


def test_no_oidc_is_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(app_mod.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    monkeypatch.setattr(settings, "oidc_issuer", None)

    app_mod._warn_on_risky_config()
    assert warnings == []
