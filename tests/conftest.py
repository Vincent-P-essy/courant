"""Shared fixtures: real API records, a balance-consistent row factory."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from courant.schema import parse_record
from courant.warehouse import Warehouse

DATA_DIR = Path(__file__).parent / "data"

#: Three real records captured from the live API (observed: consommation set).
OBSERVED_RECORDS: list[dict[str, Any]] = json.loads(
    (DATA_DIR / "observed.json").read_text(encoding="utf-8")
)

#: A real one-hour export window (4 quarter-hours).
WINDOW_RECORDS: list[dict[str, Any]] = json.loads(
    (DATA_DIR / "window.json").read_text(encoding="utf-8")
)

BASE_MOMENT = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def make_row(moment: datetime | None = None, **overrides: Any) -> dict[str, Any]:
    """A parsed, contract-valid, balance-consistent observed row."""
    raw = dict(OBSERVED_RECORDS[0])
    raw["date_heure"] = (moment or BASE_MOMENT).isoformat()
    raw.update(overrides)
    row, problem = parse_record(raw)
    assert row is not None, problem
    return row


def quarter_hours(start: datetime, count: int) -> list[dict[str, Any]]:
    """`count` consecutive observed quarter-hours starting at `start`."""
    return [make_row(start + timedelta(minutes=15 * index)) for index in range(count)]


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    wh = Warehouse(tmp_path / "test.duckdb")
    yield wh
    wh.close()
