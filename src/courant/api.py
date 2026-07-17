"""Read-only HTTP API over the marts.

Each request opens its own read-only DuckDB connection: the API can run while
a pipeline process owns the write lock elsewhere, and there is no connection
state to manage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from courant import __version__

UTC = timezone.utc

_DESCRIPTION = (
    "Quarter-hourly data for the French electricity grid (consumption, "
    "production mix, CO2 intensity), served from a DuckDB warehouse built by "
    "the courant pipeline."
)


def create_app(warehouse_path: Path, freshness_fail_hours: float = 48.0) -> FastAPI:
    app = FastAPI(title="courant", version=__version__, description=_DESCRIPTION)

    def fetch(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        try:
            con = duckdb.connect(str(warehouse_path), read_only=True)
        except duckdb.Error as exc:
            raise HTTPException(status_code=503, detail=f"warehouse unavailable: {exc}") from exc
        try:
            cursor = con.execute(sql, params or [])
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        except duckdb.Error as exc:
            message = str(exc)
            status = 409 if "does not exist" in message else 500
            detail = "mart not built yet — run `courant run` first" if status == 409 else message
            raise HTTPException(status_code=status, detail=detail) from exc
        finally:
            con.close()

    @app.get("/health", summary="Data freshness and last run status")
    def health() -> JSONResponse:
        observed = fetch(
            "SELECT max(date_heure) AS last FROM raw_eco2mix WHERE consommation IS NOT NULL"
        )
        last = observed[0]["last"] if observed else None
        runs = fetch("SELECT run_id, status, finished_at FROM runs ORDER BY run_id DESC LIMIT 1")
        if last is None:
            empty: dict[str, Any] = {"status": "empty", "detail": "no observed rows yet"}
            return JSONResponse(jsonable_encoder(empty), status_code=503)
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        lag_hours = max((datetime.now(UTC) - last).total_seconds() / 3600.0, 0.0)
        stale = lag_hours > freshness_fail_hours
        body = {
            "status": "stale" if stale else "ok",
            "last_observed": last,
            "lag_hours": round(lag_hours, 1),
            "last_run": runs[0] if runs else None,
        }
        return JSONResponse(jsonable_encoder(body), status_code=503 if stale else 200)

    @app.get("/latest", summary="Most recent observed quarter-hour")
    def latest() -> dict[str, Any]:
        rows = fetch("SELECT * FROM mart_latest")
        if not rows:
            raise HTTPException(status_code=404, detail="no observed data yet")
        return rows[0]

    @app.get("/daily", summary="Daily aggregates (local French days)")
    def daily(
        days: int = Query(default=30, ge=1, le=365, description="Most recent N days"),
    ) -> list[dict[str, Any]]:
        rows = fetch("SELECT * FROM mart_daily ORDER BY day DESC LIMIT ?", [days])
        return list(reversed(rows))

    @app.get("/mix", summary="Production mix over the last 7 observed days")
    def mix() -> list[dict[str, Any]]:
        return fetch("SELECT * FROM mart_mix_7d")

    @app.get("/runs", summary="Pipeline run ledger")
    def runs(limit: int = Query(default=20, ge=1, le=200)) -> list[dict[str, Any]]:
        return fetch(
            "SELECT run_id, started_at, finished_at, window_start, window_end, "
            "rows_fetched, rows_rejected, rows_inserted, rows_updated, status, error "
            "FROM runs ORDER BY run_id DESC LIMIT ?",
            [limit],
        )

    return app
