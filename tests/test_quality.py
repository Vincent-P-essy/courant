from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import BASE_MOMENT, make_row, quarter_hours
from courant.quality import CheckResult, QualityReport, check_batch, check_warehouse
from courant.transform import run_transforms
from courant.warehouse import Warehouse


def _by_name(checks: list[CheckResult]) -> dict[str, CheckResult]:
    return {check.name: check for check in checks}


def test_clean_batch_passes_everything() -> None:
    rows = quarter_hours(BASE_MOMENT, 8)
    report = QualityReport(check_batch(rows, [], len(rows), 5.0))
    assert report.status == "pass"
    assert report.failures() == []


def test_rejections_warn_then_fail_on_ratio() -> None:
    rows = quarter_hours(BASE_MOMENT, 40)
    warn = _by_name(check_batch(rows, ["r1"], 41, 5.0))["contract_coercion"]
    assert not warn.passed and warn.severity == "warn"

    fail = _by_name(check_batch(rows, ["r"] * 5, 45, 5.0))["contract_coercion"]
    assert not fail.passed and fail.severity == "fail"


def test_duplicate_timestamps_fail() -> None:
    rows = [make_row(), make_row()]
    check = _by_name(check_batch(rows, [], 2, 5.0))["primary_key_unique"]
    assert not check.passed and check.severity == "fail"


def test_off_grid_timestamps_warn() -> None:
    rows = [make_row(BASE_MOMENT + timedelta(minutes=7))]
    check = _by_name(check_batch(rows, [], 1, 5.0))["quarter_hour_grid"]
    assert not check.passed and check.severity == "warn"


def test_empty_window_warns() -> None:
    check = _by_name(check_batch([], [], 0, 5.0))["window_not_empty"]
    assert not check.passed and check.severity == "warn"


def test_range_violation_warns_with_example() -> None:
    rows = [make_row(consommation=200_000)]
    check = _by_name(check_batch(rows, [], 1, 5.0))["plausible_ranges"]
    assert not check.passed
    assert "consommation=200000" in check.detail


def test_balance_violation_warns() -> None:
    rows = [make_row(nucleaire=5_000)]  # rip 32 GW out of the supply side
    check = _by_name(check_batch(rows, [], 1, 5.0))["grid_balance"]
    assert not check.passed
    assert "1 beyond" in check.detail


def test_balance_skips_rows_with_missing_components() -> None:
    rows = [make_row(pompage=None)]
    check = _by_name(check_batch(rows, [], 1, 5.0))["grid_balance"]
    assert check.passed
    assert "0 row(s) checked" in check.detail


def test_forecast_rows_are_exempt_from_observed_checks() -> None:
    rows = [make_row(consommation=None, nucleaire=999_999)]
    checks = _by_name(check_batch(rows, [], 1, 5.0))
    assert checks["plausible_ranges"].passed
    assert checks["grid_balance"].passed


def test_report_status_aggregation() -> None:
    ok = CheckResult("a", True, "fail", "")
    warn = CheckResult("b", False, "warn", "")
    fail = CheckResult("c", False, "fail", "")
    assert QualityReport([ok]).status == "pass"
    assert QualityReport([ok, warn]).status == "warn"
    assert QualityReport([ok, warn, fail]).status == "fail"


def test_report_markdown_and_json() -> None:
    report = QualityReport([CheckResult("grid_balance", False, "warn", "worst 9.9%")])
    markdown = report.to_markdown()
    assert "WARN" in markdown
    assert "| grid_balance | WARN | worst 9.9% |" in markdown
    assert '"status": "warn"' in report.to_json()


def test_freshness_thresholds(warehouse: Warehouse) -> None:
    moment = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    warehouse.upsert([make_row(moment)])

    fresh = check_warehouse(warehouse.con, moment + timedelta(hours=2), 6.0, 48.0)[0]
    assert fresh.passed

    warn = check_warehouse(warehouse.con, moment + timedelta(hours=12), 6.0, 48.0)[0]
    assert not warn.passed and warn.severity == "warn"

    fail = check_warehouse(warehouse.con, moment + timedelta(hours=60), 6.0, 48.0)[0]
    assert not fail.passed and fail.severity == "fail"


def test_freshness_fails_on_empty_warehouse(warehouse: Warehouse) -> None:
    check = check_warehouse(warehouse.con, datetime.now(UTC), 6.0, 48.0)[0]
    assert not check.passed and check.severity == "fail"


def test_day_completeness(warehouse: Warehouse) -> None:
    # A full local day (96 quarters starting at 22:00 UTC = midnight Paris in July)
    start = datetime(2026, 7, 12, 22, 0, tzinfo=UTC)
    warehouse.upsert(quarter_hours(start, 96))  # the finished day
    warehouse.upsert(quarter_hours(start + timedelta(days=1), 4))  # today, partial
    run_transforms(warehouse.con)

    check = check_warehouse(warehouse.con, datetime.now(UTC), 6.0, 48.0)[1]
    assert check.name == "day_completeness"
    assert check.passed, check.detail
    assert "96" in check.detail


def test_day_completeness_detects_gaps(warehouse: Warehouse) -> None:
    start = datetime(2026, 7, 12, 22, 0, tzinfo=UTC)
    rows = quarter_hours(start, 96)
    # Remove two full hours: 88 quarters is invalid even on a DST day (92/100).
    del rows[10:18]
    warehouse.upsert(rows)
    warehouse.upsert(quarter_hours(start + timedelta(days=1), 4))

    check = check_warehouse(warehouse.con, datetime.now(UTC), 6.0, 48.0)[1]
    assert not check.passed
    assert "88" in check.detail
