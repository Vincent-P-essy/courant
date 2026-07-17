"""Command-line interface."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from courant import __version__
from courant.config import CourantConfig
from courant.errors import CourantError
from courant.pipeline import RunOutcome, run_once
from courant.warehouse import Warehouse

UTC = timezone.utc

_DB_DEFAULT = Path("data/courant.duckdb")


@click.group(help="courant — an incremental open-data pipeline for the French grid.")
@click.version_option(__version__, prog_name="courant")
def main() -> None: ...


@main.command(help="Run one cycle: fetch, validate, load, transform, check.")
@click.option(
    "--db",
    "db_path",
    type=click.Path(path_type=Path),
    default=_DB_DEFAULT,
    show_default=True,
    help="Warehouse file.",
)
@click.option("--since", help="Override the window start (ISO 8601, UTC assumed if naive).")
@click.option(
    "--report",
    "report_path",
    type=click.Path(path_type=Path),
    help="Also write the quality report as Markdown to this path.",
)
@click.option("--json", "json_output", is_flag=True, help="Print the outcome as JSON.")
@click.option("--strict", is_flag=True, help="Exit 1 when the quality status is `warn`.")
def run(
    db_path: Path,
    since: str | None,
    report_path: Path | None,
    json_output: bool,
    strict: bool,
) -> None:
    since_moment: datetime | None = None
    if since:
        try:
            since_moment = datetime.fromisoformat(since)
        except ValueError as exc:
            raise click.UsageError(f"--since must be ISO 8601: {exc}") from exc
        if since_moment.tzinfo is None:
            since_moment = since_moment.replace(tzinfo=UTC)

    try:
        cfg = CourantConfig.load(warehouse_path=db_path)
        outcome = run_once(cfg, since=since_moment)
    except CourantError as exc:
        raise click.ClickException(str(exc)) from exc

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_report_markdown(outcome), encoding="utf-8")

    if json_output:
        click.echo(json.dumps(outcome.to_dict(), indent=2))
    else:
        _print_outcome(outcome)

    if outcome.status == "fail":
        sys.exit(2)
    if outcome.status == "warn" and strict:
        sys.exit(1)


@main.command(help="Show the run ledger and data freshness.")
@click.option(
    "--db",
    "db_path",
    type=click.Path(path_type=Path),
    default=_DB_DEFAULT,
    show_default=True,
)
def status(db_path: Path) -> None:
    if not db_path.exists():
        raise click.ClickException(f"no warehouse at {db_path} — run `courant run` first")
    console = Console()
    with Warehouse(db_path) as wh:
        watermark = wh.watermark()
        if watermark is None:
            console.print("[yellow]warehouse holds no observed rows yet[/]")
        else:
            lag = max((datetime.now(UTC) - watermark).total_seconds() / 3600.0, 0.0)
            console.print(
                f"raw rows: [bold]{wh.count_raw()}[/] · last observed quarter-hour: "
                f"[bold]{watermark:%Y-%m-%d %H:%M} UTC[/] ({lag:.1f} h ago)"
            )
        table = Table(title="last runs")
        for column in ("run", "started (UTC)", "status", "fetched", "+ins", "~upd", "error"):
            table.add_column(column)
        for entry in wh.last_runs(10):
            style = {"pass": "green", "warn": "yellow", "fail": "red", "error": "red"}.get(
                str(entry["status"]), ""
            )
            table.add_row(
                str(entry["run_id"]),
                f"{entry['started_at']:%Y-%m-%d %H:%M}" if entry["started_at"] else "",
                f"[{style}]{entry['status']}[/]" if style else str(entry["status"]),
                str(entry["rows_fetched"] or 0),
                str(entry["rows_inserted"] or 0),
                str(entry["rows_updated"] or 0),
                str(entry["error"] or "")[:60],
            )
        console.print(table)


@main.command(help="Serve the read API over the warehouse.")
@click.option(
    "--db",
    "db_path",
    type=click.Path(path_type=Path),
    default=_DB_DEFAULT,
    show_default=True,
)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
def serve(db_path: Path, host: str, port: int) -> None:
    import uvicorn

    from courant.api import create_app

    uvicorn.run(create_app(db_path), host=host, port=port)


def _report_markdown(outcome: RunOutcome) -> str:
    header = (
        f"# courant run {outcome.run_id}\n\n"
        f"- window: {outcome.window_start:%Y-%m-%d %H:%M} -> "
        f"{outcome.window_end:%Y-%m-%d %H:%M} UTC\n"
        f"- fetched {outcome.rows_fetched} · rejected {outcome.rows_rejected} · "
        f"inserted {outcome.rows_inserted} · updated {outcome.rows_updated}\n"
        f"- marts rebuilt: {', '.join(outcome.marts) or 'none'} · "
        f"{outcome.duration_seconds:.1f}s\n\n"
    )
    return header + outcome.report.to_markdown() + "\n"


def _print_outcome(outcome: RunOutcome) -> None:
    console = Console()
    color = {"pass": "green", "warn": "yellow", "fail": "red"}[outcome.status]
    console.print(
        f"[bold {color}]{outcome.status.upper()}[/] · run {outcome.run_id} · "
        f"window {outcome.window_start:%m-%d %H:%M} -> {outcome.window_end:%m-%d %H:%M} UTC"
    )
    console.print(
        f"fetched {outcome.rows_fetched} (rejected {outcome.rows_rejected}) · "
        f"inserted {outcome.rows_inserted} · updated {outcome.rows_updated} · "
        f"marts: {', '.join(outcome.marts) or 'skipped'} · {outcome.duration_seconds:.1f}s"
    )
    for check in outcome.report.failures():
        console.print(
            f"  [{'red' if check.severity == 'fail' else 'yellow'}]"
            f"{check.severity}[/] {check.name}: {check.detail}"
        )


if __name__ == "__main__":
    main()
