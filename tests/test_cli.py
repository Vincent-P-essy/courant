from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from conftest import make_row
from courant.cli import main
from courant.pipeline import RunOutcome
from courant.quality import CheckResult, QualityReport
from courant.warehouse import Warehouse

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def _outcome(status: str = "pass", **overrides: Any) -> RunOutcome:
    checks = []
    if status != "pass":
        checks = [CheckResult("grid_balance", False, "fail" if status == "fail" else "warn", "x")]
    defaults: dict[str, Any] = {
        "run_id": 1,
        "status": status,
        "report": QualityReport(checks),
        "window_start": NOW - timedelta(hours=2),
        "window_end": NOW,
        "rows_fetched": 8,
        "rows_inserted": 8,
        "marts": ["01_mart_daily"],
        "duration_seconds": 1.2,
    }
    defaults.update(overrides)
    return RunOutcome(**defaults)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_run_prints_a_summary(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("courant.cli.run_once", lambda cfg, since=None: _outcome())
    result = runner.invoke(main, ["run", "--db", str(tmp_path / "wh.duckdb")])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    assert "inserted 8" in result.output


def test_run_json_output(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("courant.cli.run_once", lambda cfg, since=None: _outcome())
    result = runner.invoke(main, ["run", "--json", "--db", str(tmp_path / "wh.duckdb")])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["quality"]["status"] == "pass"


def test_run_writes_a_markdown_report(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("courant.cli.run_once", lambda cfg, since=None: _outcome("warn"))
    report = tmp_path / "out" / "report.md"
    result = runner.invoke(
        main, ["run", "--db", str(tmp_path / "wh.duckdb"), "--report", str(report)]
    )
    assert result.exit_code == 0
    text = report.read_text()
    assert "# courant run 1" in text
    assert "Data quality" in text


def test_run_exit_codes(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = str(tmp_path / "wh.duckdb")
    monkeypatch.setattr("courant.cli.run_once", lambda cfg, since=None: _outcome("fail"))
    assert runner.invoke(main, ["run", "--db", db]).exit_code == 2

    monkeypatch.setattr("courant.cli.run_once", lambda cfg, since=None: _outcome("warn"))
    assert runner.invoke(main, ["run", "--db", db]).exit_code == 0
    assert runner.invoke(main, ["run", "--db", db, "--strict"]).exit_code == 1


def test_run_passes_since_through(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake(cfg: Any, since: Any = None) -> RunOutcome:
        seen["since"] = since
        return _outcome()

    monkeypatch.setattr("courant.cli.run_once", fake)
    result = runner.invoke(
        main, ["run", "--db", str(tmp_path / "w.duckdb"), "--since", "2026-06-01T00:00:00"]
    )
    assert result.exit_code == 0
    assert seen["since"] == datetime(2026, 6, 1, tzinfo=UTC)


def test_run_rejects_bad_since(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        main, ["run", "--db", str(tmp_path / "w.duckdb"), "--since", "not-a-date"]
    )
    assert result.exit_code == 2
    assert "ISO 8601" in result.output


def test_status_requires_a_warehouse(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(main, ["status", "--db", str(tmp_path / "missing.duckdb")])
    assert result.exit_code == 1
    assert "courant run" in result.output


def test_status_renders_the_ledger(runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "wh.duckdb"
    with Warehouse(path) as wh:
        wh.upsert([make_row(datetime.now(UTC).replace(minute=0, second=0, microsecond=0))])
        run_id = wh.start_run(datetime.now(UTC), NOW, NOW)
        wh.finish_run(run_id, "pass", rows_fetched=1, rows_inserted=1)
    result = runner.invoke(main, ["status", "--db", str(path)])
    assert result.exit_code == 0, result.output
    assert "last runs" in result.output
    assert "pass" in result.output


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "courant" in result.output
