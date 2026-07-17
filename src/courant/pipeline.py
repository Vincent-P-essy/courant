"""Orchestration of one ingestion cycle.

    window -> fetch -> contract -> quality gate -> upsert -> transforms -> checks

The gate is strict about ordering: a batch whose quality status is `fail`
never reaches the warehouse. Warehouse-level checks (freshness, completeness)
run after the load and can only downgrade the run status, not block data that
already passed the batch gate.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from courant.config import CourantConfig
from courant.errors import CourantError
from courant.quality import QualityReport, check_batch, check_warehouse
from courant.schema import parse_record
from courant.source import OdreClient
from courant.transform import run_transforms
from courant.warehouse import Warehouse

UTC = timezone.utc


@dataclass(slots=True)
class RunOutcome:
    run_id: int
    status: str  # pass | warn | fail
    report: QualityReport
    window_start: datetime
    window_end: datetime
    rows_fetched: int = 0
    rows_rejected: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    marts: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "rows_fetched": self.rows_fetched,
            "rows_rejected": self.rows_rejected,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "marts": self.marts,
            "duration_seconds": round(self.duration_seconds, 2),
            "quality": json.loads(self.report.to_json()),
        }


def compute_window(
    watermark: datetime | None,
    now: datetime,
    cfg: CourantConfig,
    since: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Incremental window: watermark minus overlap (the source revises recent
    rows), bootstrap on first run, extended past now to capture forecasts."""
    if since is not None:
        start = since
    elif watermark is not None:
        start = watermark - timedelta(hours=cfg.overlap_hours)
    else:
        start = now - timedelta(hours=cfg.bootstrap_hours)
    end = now + timedelta(hours=cfg.horizon_hours)
    return start, end


def run_once(
    cfg: CourantConfig,
    source: OdreClient | None = None,
    warehouse: Warehouse | None = None,
    now: datetime | None = None,
    since: datetime | None = None,
) -> RunOutcome:
    started = time.monotonic()
    now = now or datetime.now(UTC)
    own_warehouse = warehouse is None
    wh = warehouse or Warehouse(cfg.warehouse_path)
    try:
        src = source or OdreClient(cfg.base_url, cfg.dataset)
        window_start, window_end = compute_window(wh.watermark(), now, cfg, since)
        run_id = wh.start_run(now, window_start, window_end)
        try:
            raw = src.fetch_window(window_start, window_end)

            rows: list[dict[str, Any]] = []
            rejected: list[str] = []
            for record in raw:
                row, problem = parse_record(record)
                if row is None:
                    rejected.append(problem or "unknown parse error")
                else:
                    rows.append(row)

            checks = check_batch(rows, rejected, len(raw), cfg.balance_tolerance_pct)
            report = QualityReport(checks)
            if report.status == "fail":
                wh.finish_run(
                    run_id,
                    "fail",
                    rows_fetched=len(raw),
                    rows_rejected=len(rejected),
                    quality=report.to_json(),
                )
                return RunOutcome(
                    run_id=run_id,
                    status="fail",
                    report=report,
                    window_start=window_start,
                    window_end=window_end,
                    rows_fetched=len(raw),
                    rows_rejected=len(rejected),
                    duration_seconds=time.monotonic() - started,
                )

            inserted, updated = wh.upsert(rows)
            marts = run_transforms(wh.con)
            checks = checks + check_warehouse(
                wh.con, now, cfg.freshness_warn_hours, cfg.freshness_fail_hours
            )
            report = QualityReport(checks)
            wh.finish_run(
                run_id,
                report.status,
                rows_fetched=len(raw),
                rows_rejected=len(rejected),
                rows_inserted=inserted,
                rows_updated=updated,
                quality=report.to_json(),
            )
            return RunOutcome(
                run_id=run_id,
                status=report.status,
                report=report,
                window_start=window_start,
                window_end=window_end,
                rows_fetched=len(raw),
                rows_rejected=len(rejected),
                rows_inserted=inserted,
                rows_updated=updated,
                marts=marts,
                duration_seconds=time.monotonic() - started,
            )
        except CourantError as exc:
            wh.finish_run(run_id, "error", error=str(exc))
            raise
    finally:
        if own_warehouse:
            wh.close()
