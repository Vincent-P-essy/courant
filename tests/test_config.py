from __future__ import annotations

from pathlib import Path

import pytest

from courant.config import CourantConfig
from courant.errors import ConfigError


def test_defaults() -> None:
    cfg = CourantConfig.load()
    assert cfg.dataset == "eco2mix-national-tr"
    assert cfg.warehouse_path == Path("data/courant.duckdb")
    assert cfg.overlap_hours == 24


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURANT_BOOTSTRAP_HOURS", "120")
    monkeypatch.setenv("COURANT_WAREHOUSE_PATH", "/tmp/elsewhere.duckdb")
    cfg = CourantConfig.load()
    assert cfg.bootstrap_hours == 120
    assert cfg.warehouse_path == Path("/tmp/elsewhere.duckdb")


def test_kwargs_beat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURANT_OVERLAP_HOURS", "48")
    cfg = CourantConfig.load(overlap_hours=6)
    assert cfg.overlap_hours == 6


def test_none_kwargs_are_ignored() -> None:
    cfg = CourantConfig.load(overlap_hours=None)
    assert cfg.overlap_hours == 24


def test_invalid_values_fail_loudly() -> None:
    with pytest.raises(ConfigError, match="bootstrap_hours"):
        CourantConfig.load(bootstrap_hours=0)
