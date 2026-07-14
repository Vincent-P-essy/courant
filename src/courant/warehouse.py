"""The DuckDB warehouse: raw landing table, idempotent upsert, run ledger.

One file holds everything — raw history, marts, and the operational ledger —
which makes the whole pipeline state portable, cacheable in CI, and queryable
with any DuckDB client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

import duckdb

from courant.errors import WarehouseError
from courant.schema import FIELD_NAMES, FIELDS

_SQL_TYPES = {"timestamp": "TIMESTAMPTZ", "integer": "INTEGER", "text": "VARCHAR"}


def _raw_ddl() -> str:
    columns = []
    for spec in FIELDS:
        suffix = " PRIMARY KEY" if spec.name == "date_heure" else ""
        columns.append(f"{spec.name} {_SQL_TYPES[spec.kind]}{suffix}")
    columns.append("ingested_at TIMESTAMPTZ NOT NULL")
    return f"CREATE TABLE IF NOT EXISTS raw_eco2mix ({', '.join(columns)})"


_RUNS_DDL = """
CREATE SEQUENCE IF NOT EXISTS runs_seq;
CREATE TABLE IF NOT EXISTS runs (
    run_id BIGINT PRIMARY KEY DEFAULT nextval('runs_seq'),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    window_start TIMESTAMPTZ,
    window_end TIMESTAMPTZ,
    rows_fetched INTEGER,
    rows_rejected INTEGER,
    rows_inserted INTEGER,
    rows_updated INTEGER,
    status VARCHAR,
    quality VARCHAR,
    error VARCHAR
)
"""


class Warehouse:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.con = duckdb.connect(str(path))
        except duckdb.Error as exc:  # pragma: no cover - filesystem dependent
            raise WarehouseError(f"could not open warehouse at {path}: {exc}") from exc
        self.con.execute(_raw_ddl())
        self.con.execute(_RUNS_DDL)

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- raw data ---------------------------------------------------------------

    def upsert(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        """Insert-or-update by date_heure; returns (inserted, updated)."""
        if not rows:
            return (0, 0)
        before = self.count_raw()
        placeholders = ", ".join("?" for _ in range(len(FIELD_NAMES) + 1))
        updates = ", ".join(
            f"{name} = excluded.{name}" for name in FIELD_NAMES if name != "date_heure"
        )
        sql = (
            f"INSERT INTO raw_eco2mix ({', '.join(FIELD_NAMES)}, ingested_at) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (date_heure) DO UPDATE SET {updates}, "
            "ingested_at = excluded.ingested_at"
        )
        now = datetime.now(UTC)
        params = [(*(row[name] for name in FIELD_NAMES), now) for row in rows]
        try:
            self.con.executemany(sql, params)
        except duckdb.Error as exc:
            raise WarehouseError(f"upsert failed: {exc}") from exc
        inserted = self.count_raw() - before
        return inserted, len(rows) - inserted

    def count_raw(self) -> int:
        return int(self.con.execute("SELECT COUNT(*) FROM raw_eco2mix").fetchone()[0])

    def watermark(self) -> datetime | None:
        """Latest observed quarter-hour — the incremental ingestion cursor."""
        value = self.con.execute(
            "SELECT max(date_heure) FROM raw_eco2mix WHERE consommation IS NOT NULL"
        ).fetchone()[0]
        if value is None:
            return None
        moment: datetime = value
        return moment if moment.tzinfo else moment.replace(tzinfo=UTC)

    # -- run ledger ---------------------------------------------------------------

    def start_run(self, started_at: datetime, window_start: datetime, window_end: datetime) -> int:
        row = self.con.execute(
            "INSERT INTO runs (started_at, window_start, window_end, status) "
            "VALUES (?, ?, ?, 'running') RETURNING run_id",
            [started_at, window_start, window_end],
        ).fetchone()
        return int(row[0])

    def finish_run(
        self,
        run_id: int,
        status: str,
        rows_fetched: int = 0,
        rows_rejected: int = 0,
        rows_inserted: int = 0,
        rows_updated: int = 0,
        quality: str = "",
        error: str = "",
    ) -> None:
        self.con.execute(
            "UPDATE runs SET finished_at = ?, status = ?, rows_fetched = ?, "
            "rows_rejected = ?, rows_inserted = ?, rows_updated = ?, quality = ?, error = ? "
            "WHERE run_id = ?",
            [
                datetime.now(UTC),
                status,
                rows_fetched,
                rows_rejected,
                rows_inserted,
                rows_updated,
                quality,
                error,
                run_id,
            ],
        )

    def last_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.rows(
            "SELECT run_id, started_at, finished_at, window_start, window_end, "
            "rows_fetched, rows_rejected, rows_inserted, rows_updated, status, error "
            "FROM runs ORDER BY run_id DESC LIMIT ?",
            [limit],
        )

    # -- generic helpers ------------------------------------------------------------

    def rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        cursor = self.con.execute(sql, params or [])
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, record, strict=True)) for record in cursor.fetchall()]
