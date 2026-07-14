"""Versioned SQL transforms.

Marts are plain .sql files executed in filename order — every run rebuilds
them from raw (CREATE OR REPLACE), so a transform change is just a new file
revision, reviewed like any other code, and the marts can never drift from
their definitions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from courant.errors import WarehouseError

SQL_DIR = Path(__file__).parent / "sql" / "marts"


def run_transforms(con: Any, sql_dir: Path | None = None) -> list[str]:
    directory = sql_dir or SQL_DIR
    executed: list[str] = []
    for script in sorted(directory.glob("*.sql")):
        try:
            con.execute(script.read_text(encoding="utf-8"))
        except duckdb.Error as exc:
            raise WarehouseError(f"transform {script.name} failed: {exc}") from exc
        executed.append(script.stem)
    return executed
