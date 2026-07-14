from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import BASE_MOMENT, make_row, quarter_hours
from courant.warehouse import Warehouse


def test_upsert_counts_inserts_and_updates(warehouse: Warehouse) -> None:
    rows = quarter_hours(BASE_MOMENT, 4)
    assert warehouse.upsert(rows) == (4, 0)
    # Re-loading the same window updates in place — idempotent by design.
    assert warehouse.upsert(rows) == (0, 4)
    assert warehouse.count_raw() == 4


def test_upsert_revises_values(warehouse: Warehouse) -> None:
    warehouse.upsert([make_row(consommation=50_000)])
    warehouse.upsert([make_row(consommation=51_234)])
    value = warehouse.con.execute("SELECT consommation FROM raw_eco2mix").fetchone()[0]
    assert value == 51_234


def test_empty_upsert_is_a_noop(warehouse: Warehouse) -> None:
    assert warehouse.upsert([]) == (0, 0)


def test_watermark_ignores_forecast_rows(warehouse: Warehouse) -> None:
    assert warehouse.watermark() is None
    observed = make_row(BASE_MOMENT)
    forecast = make_row(BASE_MOMENT + timedelta(hours=6), consommation=None)
    warehouse.upsert([observed, forecast])
    assert warehouse.watermark() == BASE_MOMENT


def test_run_ledger_roundtrip(warehouse: Warehouse) -> None:
    now = datetime.now(UTC)
    run_id = warehouse.start_run(now, now - timedelta(hours=2), now)
    warehouse.finish_run(
        run_id,
        "warn",
        rows_fetched=10,
        rows_rejected=1,
        rows_inserted=8,
        rows_updated=1,
        quality='{"status": "warn"}',
    )
    (entry,) = warehouse.last_runs(1)
    assert entry["run_id"] == run_id
    assert entry["status"] == "warn"
    assert entry["rows_fetched"] == 10
    assert entry["finished_at"] is not None


def test_last_runs_orders_newest_first(warehouse: Warehouse) -> None:
    now = datetime.now(UTC)
    first = warehouse.start_run(now, now, now)
    second = warehouse.start_run(now, now, now)
    runs = warehouse.last_runs(5)
    assert [entry["run_id"] for entry in runs] == [second, first]


def test_context_manager_closes() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "wh.duckdb"
        with Warehouse(path) as wh:
            wh.upsert([make_row()])
        # Reopening proves the file was cleanly written and closed.
        with Warehouse(path) as wh:
            assert wh.count_raw() == 1
