from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from conftest import OBSERVED_RECORDS
from courant.config import CourantConfig
from courant.errors import SourceError
from courant.pipeline import compute_window, run_once
from courant.warehouse import Warehouse

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


class FakeSource:
    def __init__(self, batches: list[list[dict[str, Any]] | Exception]) -> None:
        self.batches = list(batches)
        self.calls: list[tuple[datetime, datetime]] = []

    def fetch_window(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        self.calls.append((start, end))
        item = self.batches.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def raw_records(start: datetime, count: int) -> list[dict[str, Any]]:
    """API-shaped records (raw JSON values) at consecutive quarter-hours."""
    records = []
    for index in range(count):
        record = dict(OBSERVED_RECORDS[0])
        record["date_heure"] = (start + timedelta(minutes=15 * index)).isoformat()
        records.append(record)
    return records


def _cfg(tmp_path: Path) -> CourantConfig:
    return CourantConfig.load(warehouse_path=tmp_path / "wh.duckdb")


def test_first_run_bootstraps_loads_and_builds_marts(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    source = FakeSource([raw_records(NOW - timedelta(hours=2), 8)])

    outcome = run_once(cfg, source=source, now=NOW)  # type: ignore[arg-type]

    assert outcome.status == "pass"
    assert outcome.rows_fetched == 8
    assert outcome.rows_inserted == 8
    assert outcome.marts == ["01_mart_daily", "02_mart_latest", "03_mart_mix_7d"]

    start, end = source.calls[0]
    assert start == NOW - timedelta(hours=cfg.bootstrap_hours)
    assert end == NOW + timedelta(hours=cfg.horizon_hours)

    with Warehouse(cfg.warehouse_path) as wh:
        assert wh.count_raw() == 8
        (run,) = wh.last_runs(1)
        assert run["status"] == "pass"
        assert wh.rows("SELECT * FROM mart_latest")


def test_second_run_is_incremental_from_the_watermark(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    first_start = NOW - timedelta(hours=2)
    source = FakeSource(
        [
            raw_records(first_start, 8),
            raw_records(first_start, 10),  # revisions + two new quarters
        ]
    )
    run_once(cfg, source=source, now=NOW)  # type: ignore[arg-type]
    watermark = first_start + timedelta(minutes=15 * 7)

    outcome = run_once(cfg, source=source, now=NOW + timedelta(hours=1))  # type: ignore[arg-type]

    start, _ = source.calls[1]
    assert start == watermark - timedelta(hours=cfg.overlap_hours)
    assert outcome.rows_inserted == 2
    assert outcome.rows_updated == 8


def test_failing_batch_is_never_loaded(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    records = raw_records(NOW - timedelta(hours=1), 10)
    for record in records[:2]:  # 2/10 rejected > 5% -> coercion check fails
        del record["consommation"]

    outcome = run_once(cfg, source=FakeSource([records]), now=NOW)  # type: ignore[arg-type]

    assert outcome.status == "fail"
    assert outcome.rows_rejected == 2
    assert outcome.rows_inserted == 0
    with Warehouse(cfg.warehouse_path) as wh:
        assert wh.count_raw() == 0
        (run,) = wh.last_runs(1)
        assert run["status"] == "fail"


def test_warning_batch_still_loads(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    records = raw_records(NOW - timedelta(hours=8), 32)
    del records[0]["consommation"]  # 1/32 rejected: warn, below the fail ratio

    outcome = run_once(cfg, source=FakeSource([records]), now=NOW)  # type: ignore[arg-type]

    assert outcome.status == "warn"
    assert outcome.rows_inserted == 31
    failed = {check.name for check in outcome.report.failures()}
    assert "contract_coercion" in failed


def test_source_error_is_recorded_in_the_ledger(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    source = FakeSource([SourceError("ODRE API error: down")])

    with pytest.raises(SourceError):
        run_once(cfg, source=source, now=NOW)  # type: ignore[arg-type]

    with Warehouse(cfg.warehouse_path) as wh:
        (run,) = wh.last_runs(1)
        assert run["status"] == "error"
        assert "down" in run["error"]


def test_since_overrides_the_window_start(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    since = NOW - timedelta(days=30)
    source = FakeSource([raw_records(NOW - timedelta(hours=1), 4)])

    run_once(cfg, source=source, now=NOW, since=since)  # type: ignore[arg-type]

    assert source.calls[0][0] == since


def test_compute_window_shapes() -> None:
    cfg = CourantConfig()
    bootstrap_start, end = compute_window(None, NOW, cfg)
    assert bootstrap_start == NOW - timedelta(hours=cfg.bootstrap_hours)
    assert end == NOW + timedelta(hours=cfg.horizon_hours)

    watermark = NOW - timedelta(hours=3)
    incremental_start, _ = compute_window(watermark, NOW, cfg)
    assert incremental_start == watermark - timedelta(hours=cfg.overlap_hours)
