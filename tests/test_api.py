from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import quarter_hours
from courant.api import create_app
from courant.transform import run_transforms
from courant.warehouse import Warehouse


def _quarter_aligned_now() -> datetime:
    now = datetime.now(UTC)
    return now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)


@pytest.fixture
def served(tmp_path: Path) -> TestClient:
    path = tmp_path / "wh.duckdb"
    with Warehouse(path) as wh:
        start = _quarter_aligned_now() - timedelta(hours=25)
        wh.upsert(quarter_hours(start, 100))  # ends within the last hour: fresh
        run_transforms(wh.con)
        run_id = wh.start_run(datetime.now(UTC), start, datetime.now(UTC))
        wh.finish_run(run_id, "pass", rows_fetched=100, rows_inserted=100)
    return TestClient(create_app(path))


def test_health_ok(served: TestClient) -> None:
    response = served.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["lag_hours"] < 2
    assert body["last_run"]["status"] == "pass"


def test_health_reports_stale_data(tmp_path: Path) -> None:
    path = tmp_path / "wh.duckdb"
    with Warehouse(path) as wh:
        wh.upsert(quarter_hours(datetime.now(UTC) - timedelta(days=10), 4))
    response = TestClient(create_app(path)).get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "stale"


def test_health_on_empty_warehouse(tmp_path: Path) -> None:
    path = tmp_path / "wh.duckdb"
    Warehouse(path).close()
    response = TestClient(create_app(path)).get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "empty"


def test_health_when_warehouse_is_missing(tmp_path: Path) -> None:
    response = TestClient(create_app(tmp_path / "nope.duckdb")).get("/health")
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


def test_latest(served: TestClient) -> None:
    body = served.get("/latest").json()
    assert body["consumption_mw"] > 0
    assert body["production_total_mw"] > 0
    assert 0 <= body["renewable_share_pct"] <= 100


def test_daily_is_chronological(served: TestClient) -> None:
    days = served.get("/daily", params={"days": 5}).json()
    assert 1 <= len(days) <= 5
    assert days == sorted(days, key=lambda row: row["day"])
    assert {"avg_consumption_mw", "renewable_share_pct"} <= set(days[0])


def test_daily_validates_bounds(served: TestClient) -> None:
    assert served.get("/daily", params={"days": 0}).status_code == 422


def test_mix(served: TestClient) -> None:
    rows = served.get("/mix").json()
    assert len(rows) == 8
    assert rows[0]["mwh"] >= rows[-1]["mwh"]


def test_runs(served: TestClient) -> None:
    rows = served.get("/runs").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "pass"


def test_unbuilt_marts_get_a_helpful_error(tmp_path: Path) -> None:
    path = tmp_path / "wh.duckdb"
    Warehouse(path).close()  # raw schema exists, marts don't
    response = TestClient(create_app(path)).get("/latest")
    assert response.status_code == 409
    assert "courant run" in response.json()["detail"]
