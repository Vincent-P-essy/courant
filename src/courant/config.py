"""Run configuration: defaults < COURANT_* environment variables < CLI flags."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

from courant.errors import ConfigError

_ENV_PREFIX = "COURANT_"


class CourantConfig(BaseModel):
    base_url: str = "https://odre.opendatasoft.com"
    dataset: str = "eco2mix-national-tr"
    warehouse_path: Path = Path("data/courant.duckdb")

    #: First run: how far back to bootstrap the warehouse.
    bootstrap_hours: int = 72
    #: Re-fetch this much before the watermark — the source revises recent rows.
    overlap_hours: int = 24
    #: Fetch this far ahead of now: forecast rows exist before observations.
    horizon_hours: int = 36

    freshness_warn_hours: float = 6.0
    freshness_fail_hours: float = 48.0
    balance_tolerance_pct: float = 5.0

    @field_validator("bootstrap_hours", "overlap_hours", "horizon_hours")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be >= 1")
        return value

    @classmethod
    def load(cls, **overrides: Any) -> CourantConfig:
        data: dict[str, Any] = {}
        for name in cls.model_fields:
            raw = os.environ.get(_ENV_PREFIX + name.upper())
            if raw is not None:
                data[name] = raw
        data.update({key: value for key, value in overrides.items() if value is not None})
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(piece) for piece in first["loc"])
            raise ConfigError(f"invalid configuration: {location}: {first['msg']}") from exc
