from __future__ import annotations

from datetime import UTC, datetime

from conftest import OBSERVED_RECORDS, WINDOW_RECORDS
from courant.schema import (
    BALANCE_FLOWS,
    FIELD_NAMES,
    PRODUCTION_SOURCES,
    is_observed,
    parse_record,
)


def test_real_observed_records_pass_the_contract() -> None:
    for raw in OBSERVED_RECORDS:
        row, problem = parse_record(raw)
        assert problem is None
        assert row is not None
        assert row["date_heure"].tzinfo is UTC
        assert is_observed(row)


def test_real_window_records_pass_the_contract() -> None:
    for raw in WINDOW_RECORDS:
        row, problem = parse_record(raw)
        assert problem is None and row is not None


def test_real_record_closes_the_balance_equation() -> None:
    row, _ = parse_record(OBSERVED_RECORDS[0])
    assert row is not None
    supply = sum(row[name] for name in (*PRODUCTION_SOURCES, *BALANCE_FLOWS))
    assert abs(supply - row["consommation"]) / row["consommation"] < 0.01


def test_forecast_only_rows_are_not_observed() -> None:
    raw = dict(OBSERVED_RECORDS[0])
    raw["consommation"] = None
    row, problem = parse_record(raw)
    assert problem is None and row is not None
    assert not is_observed(row)


def test_missing_required_field_is_rejected() -> None:
    raw = dict(OBSERVED_RECORDS[0])
    del raw["consommation"]
    row, problem = parse_record(raw)
    assert row is None
    assert problem is not None and "consommation" in problem


def test_null_date_heure_is_rejected() -> None:
    raw = dict(OBSERVED_RECORDS[0])
    raw["date_heure"] = None
    row, problem = parse_record(raw)
    assert row is None
    assert "date_heure" in str(problem)


def test_naive_timestamp_is_rejected() -> None:
    raw = dict(OBSERVED_RECORDS[0])
    raw["date_heure"] = "2026-07-14T12:00:00"
    row, problem = parse_record(raw)
    assert row is None
    assert "timezone" in str(problem)


def test_unparseable_number_is_rejected() -> None:
    raw = dict(OBSERVED_RECORDS[0])
    raw["consommation"] = "a lot"
    row, problem = parse_record(raw)
    assert row is None
    assert "consommation" in str(problem)


def test_numeric_strings_are_coerced() -> None:
    raw = dict(OBSERVED_RECORDS[0])
    raw["consommation"] = "48586.0"
    row, problem = parse_record(raw)
    assert problem is None and row is not None
    assert row["consommation"] == 48586


def test_timestamps_are_normalised_to_utc() -> None:
    raw = dict(OBSERVED_RECORDS[0])
    raw["date_heure"] = "2026-07-14T14:00:00+02:00"
    row, _ = parse_record(raw)
    assert row is not None
    assert row["date_heure"] == datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def test_unknown_payload_fields_are_ignored() -> None:
    raw = dict(OBSERVED_RECORDS[0])
    raw["brand_new_upstream_column"] = 42
    row, problem = parse_record(raw)
    assert problem is None and row is not None
    assert "brand_new_upstream_column" not in row
    assert set(row) == set(FIELD_NAMES)
