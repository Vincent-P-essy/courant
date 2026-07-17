"""The quality gate: named checks with severities, aggregated into a report.

Checks come in two waves. Batch checks run on the fetched records *before*
anything touches the warehouse — a failing batch is never loaded. Warehouse
checks (freshness, completeness) run after the load, on the full history.

`severity` is what a violation costs: "fail" blocks the load / breaks the run
status, "warn" loads the data but shows up in the report and the run ledger.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from courant.schema import BALANCE_FLOWS, FIELDS, PRODUCTION_SOURCES, is_observed

UTC = timezone.utc

#: A day has 96 quarter-hours; DST transition days have 92 or 100.
_VALID_DAY_QUARTERS = {92, 96, 100}


@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    severity: str  # "fail" | "warn" — the cost of a violation
    detail: str


@dataclass(slots=True)
class QualityReport:
    checks: list[CheckResult]

    @property
    def status(self) -> str:
        if any(not c.passed and c.severity == "fail" for c in self.checks):
            return "fail"
        if any(not c.passed for c in self.checks):
            return "warn"
        return "pass"

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def to_markdown(self) -> str:
        icon = {"pass": "✅", "warn": "⚠️", "fail": "🛑"}[self.status]
        lines = [
            f"## Data quality — {icon} {self.status.upper()}",
            "",
            "| check | result | detail |",
            "|---|---|---|",
        ]
        for check in self.checks:
            result = "ok" if check.passed else check.severity.upper()
            lines.append(f"| {check.name} | {result} | {check.detail} |")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "checks": [
                    {
                        "name": c.name,
                        "passed": c.passed,
                        "severity": c.severity,
                        "detail": c.detail,
                    }
                    for c in self.checks
                ],
            }
        )


def check_batch(
    rows: list[dict[str, Any]],
    rejected: list[str],
    fetched: int,
    balance_tolerance_pct: float,
) -> list[CheckResult]:
    results = [
        _coercion_check(rejected, fetched),
        CheckResult(
            name="window_not_empty",
            passed=fetched > 0,
            severity="warn",
            detail=f"{fetched} record(s) fetched",
        ),
        _primary_key_check(rows),
        _alignment_check(rows),
    ]
    observed = [row for row in rows if is_observed(row)]
    results.append(_range_check(observed))
    results.append(_balance_check(observed, balance_tolerance_pct))
    return results


def _coercion_check(rejected: list[str], fetched: int) -> CheckResult:
    ratio = len(rejected) / fetched if fetched else 0.0
    detail = f"{len(rejected)}/{fetched} record(s) rejected"
    if rejected:
        detail += f" — first: {rejected[0]}"
    return CheckResult(
        name="contract_coercion",
        passed=not rejected,
        severity="fail" if ratio > 0.05 else "warn",
        detail=detail,
    )


def _primary_key_check(rows: list[dict[str, Any]]) -> CheckResult:
    seen: set[datetime] = set()
    duplicates: set[datetime] = set()
    for row in rows:
        moment = row["date_heure"]
        if moment in seen:
            duplicates.add(moment)
        seen.add(moment)
    detail = f"{len(duplicates)} duplicated timestamp(s)" + (
        f" — e.g. {min(duplicates)}" if duplicates else ""
    )
    return CheckResult("primary_key_unique", not duplicates, "fail", detail)


def _alignment_check(rows: list[dict[str, Any]]) -> CheckResult:
    off_grid = [
        row["date_heure"]
        for row in rows
        if row["date_heure"].minute % 15 or row["date_heure"].second
    ]
    detail = f"{len(off_grid)} timestamp(s) off the quarter-hour grid"
    if off_grid:
        detail += f" — e.g. {off_grid[0]}"
    return CheckResult("quarter_hour_grid", not off_grid, "warn", detail)


def _range_check(observed: list[dict[str, Any]]) -> CheckResult:
    violations: list[str] = []
    counted = 0
    for spec in FIELDS:
        if spec.lo is None and spec.hi is None:
            continue
        for row in observed:
            value = row.get(spec.name)
            if value is None:
                continue
            lo = spec.lo if spec.lo is not None else float("-inf")
            hi = spec.hi if spec.hi is not None else float("inf")
            if not lo <= value <= hi:
                counted += 1
                if len(violations) < 3:
                    violations.append(f"{spec.name}={value} at {row['date_heure']}")
    detail = f"{counted} value(s) outside plausible bounds"
    if violations:
        detail += " — " + "; ".join(violations)
    return CheckResult("plausible_ranges", counted == 0, "warn", detail)


def _balance_check(observed: list[dict[str, Any]], tolerance_pct: float) -> CheckResult:
    """Physics: consumption ≈ Σ production + net exchanges + storage flows."""
    checked = 0
    beyond = 0
    worst = 0.0
    for row in observed:
        parts = [row.get(name) for name in (*PRODUCTION_SOURCES, *BALANCE_FLOWS)]
        consumption = row.get("consommation")
        if not consumption or any(part is None for part in parts):
            continue
        supply = sum(part for part in parts if part is not None)
        deviation = abs(supply - consumption) / consumption * 100.0
        checked += 1
        worst = max(worst, deviation)
        if deviation > tolerance_pct:
            beyond += 1
    detail = f"{checked} row(s) checked, {beyond} beyond ±{tolerance_pct:g}% (worst {worst:.1f}%)"
    return CheckResult("grid_balance", beyond == 0, "warn", detail)


def check_warehouse(
    con: Any,
    now: datetime,
    freshness_warn_hours: float,
    freshness_fail_hours: float,
) -> list[CheckResult]:
    return [
        _freshness_check(con, now, freshness_warn_hours, freshness_fail_hours),
        _completeness_check(con),
    ]


def _freshness_check(con: Any, now: datetime, warn_hours: float, fail_hours: float) -> CheckResult:
    last = con.execute(
        "SELECT max(date_heure) FROM raw_eco2mix WHERE consommation IS NOT NULL"
    ).fetchone()[0]
    if last is None:
        return CheckResult("freshness", False, "fail", "no observed rows in the warehouse")
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    lag_hours = max((now - last).total_seconds() / 3600.0, 0.0)
    return CheckResult(
        name="freshness",
        passed=lag_hours <= warn_hours,
        severity="fail" if lag_hours > fail_hours else "warn",
        detail=f"last observed quarter-hour {last:%Y-%m-%d %H:%M} UTC ({lag_hours:.1f} h ago)",
    )


def _completeness_check(con: Any) -> CheckResult:
    """The most recent *finished* local day must have all its quarter-hours."""
    row = con.execute(
        """
        SELECT CAST(timezone('Europe/Paris', date_heure) AS DATE) AS day, COUNT(*) AS quarters
        FROM raw_eco2mix WHERE consommation IS NOT NULL
        GROUP BY 1 ORDER BY 1 DESC
        LIMIT 1 OFFSET 1
        """
    ).fetchone()
    if row is None:
        return CheckResult(
            "day_completeness", True, "warn", "not enough history for a finished day yet"
        )
    day, quarters = row
    return CheckResult(
        name="day_completeness",
        passed=quarters in _VALID_DAY_QUARTERS,
        severity="warn",
        detail=f"{day}: {quarters} observed quarter-hour(s) (expected 96, DST days 92/100)",
    )
