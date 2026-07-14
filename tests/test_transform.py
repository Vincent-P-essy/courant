from __future__ import annotations

from datetime import UTC, datetime

from conftest import make_row, quarter_hours
from courant.transform import run_transforms
from courant.warehouse import Warehouse


def test_marts_are_built_in_order(warehouse: Warehouse) -> None:
    warehouse.upsert(quarter_hours(datetime(2026, 7, 13, 10, 0, tzinfo=UTC), 4))
    executed = run_transforms(warehouse.con)
    assert executed == ["01_mart_daily", "02_mart_latest", "03_mart_mix_7d"]


def test_daily_mart_math(warehouse: Warehouse) -> None:
    # Two quarter-hours, hand-checkable numbers.
    rows = [
        make_row(
            datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
            consommation=50_000,
            nucleaire=40_000,
            eolien=4_000,
            solaire=6_000,
            hydraulique=2_000,
            gaz=1_000,
            fioul=0,
            charbon=0,
            bioenergies=1_000,
            taux_co2=20,
        ),
        make_row(
            datetime(2026, 7, 13, 10, 15, tzinfo=UTC),
            consommation=54_000,
            nucleaire=40_000,
            eolien=4_000,
            solaire=6_000,
            hydraulique=2_000,
            gaz=1_000,
            fioul=0,
            charbon=0,
            bioenergies=1_000,
            taux_co2=30,
        ),
    ]
    warehouse.upsert(rows)
    run_transforms(warehouse.con)
    (daily,) = warehouse.rows("SELECT * FROM mart_daily")

    assert str(daily["day"]) == "2026-07-13"
    assert daily["quarters_observed"] == 2
    assert daily["avg_consumption_mw"] == 52_000
    assert daily["peak_consumption_mw"] == 54_000
    assert daily["energy_consumed_mwh"] == 26_000  # (50k + 54k) / 4
    assert daily["nuclear_mwh"] == 20_000
    # Renewables: (4k+6k+2k+1k)*2 = 26k of (54k)*2 = 108k total production
    assert daily["renewable_share_pct"] == 24.1
    assert daily["avg_co2_g_kwh"] == 25.0


def test_daily_mart_splits_days_in_paris_time(warehouse: Warehouse) -> None:
    # 21:45 UTC on July 13th is 23:45 in Paris; 22:00 UTC is 00:00 on the 14th.
    warehouse.upsert(
        [
            make_row(datetime(2026, 7, 13, 21, 45, tzinfo=UTC)),
            make_row(datetime(2026, 7, 13, 22, 0, tzinfo=UTC)),
        ]
    )
    run_transforms(warehouse.con)
    days = [str(row["day"]) for row in warehouse.rows("SELECT day FROM mart_daily ORDER BY day")]
    assert days == ["2026-07-13", "2026-07-14"]


def test_daily_mart_ignores_forecast_rows(warehouse: Warehouse) -> None:
    warehouse.upsert(
        [
            make_row(datetime(2026, 7, 13, 10, 0, tzinfo=UTC)),
            make_row(datetime(2026, 7, 13, 10, 15, tzinfo=UTC), consommation=None),
        ]
    )
    run_transforms(warehouse.con)
    (daily,) = warehouse.rows("SELECT * FROM mart_daily")
    assert daily["quarters_observed"] == 1


def test_latest_mart_derives_shares(warehouse: Warehouse) -> None:
    older = make_row(datetime(2026, 7, 13, 10, 0, tzinfo=UTC))
    newest = make_row(
        datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        consommation=50_000,
        nucleaire=30_000,
        eolien=5_000,
        solaire=10_000,
        hydraulique=4_000,
        gaz=500,
        fioul=250,
        charbon=250,
        bioenergies=0,
    )
    warehouse.upsert([older, newest])
    run_transforms(warehouse.con)
    (latest,) = warehouse.rows("SELECT * FROM mart_latest")
    assert latest["consumption_mw"] == 50_000
    assert latest["production_total_mw"] == 50_000
    assert latest["renewable_share_pct"] == 38.0  # 19k / 50k


def test_mix_mart_shares_sum_to_hundred(warehouse: Warehouse) -> None:
    warehouse.upsert(quarter_hours(datetime(2026, 7, 13, 10, 0, tzinfo=UTC), 8))
    run_transforms(warehouse.con)
    rows = warehouse.rows("SELECT * FROM mart_mix_7d")
    assert {row["source"] for row in rows} == {
        "nuclear",
        "wind",
        "solar",
        "hydro",
        "gas",
        "oil",
        "coal",
        "bioenergy",
    }
    assert abs(sum(row["share_pct"] for row in rows) - 100.0) < 1.0
    assert rows[0]["source"] == "nuclear"  # dominant source first


def test_transforms_are_idempotent(warehouse: Warehouse) -> None:
    warehouse.upsert(quarter_hours(datetime(2026, 7, 13, 10, 0, tzinfo=UTC), 4))
    run_transforms(warehouse.con)
    first = warehouse.rows("SELECT * FROM mart_daily")
    run_transforms(warehouse.con)
    assert warehouse.rows("SELECT * FROM mart_daily") == first
