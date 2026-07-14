"""The data contract for éCO2mix national temps réel.

The field list and plausibility bounds were derived from live API responses,
not from documentation alone. Two physical facts shape everything downstream:

- Rows exist *ahead of now*, carrying only forecasts (`prevision_j*`); a row
  counts as **observed** once `consommation` is non-null. Quality checks and
  marts operate on the observed subset; forecast rows are stored as-is.
- Signed conventions: `pompage` (pumped storage) and `stockage_batterie`
  (battery charging) are negative draws, `ech_physiques` is the net physical
  exchange (negative = France exports). Grid balance must close:
  consumption ≈ Σ production + exchanges + storage flows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

Kind = Literal["timestamp", "integer", "text"]


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    kind: Kind
    required: bool = False  # the key must exist in every payload record
    lo: float | None = None  # plausibility bounds for quality checks (MW)
    hi: float | None = None


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("date_heure", "timestamp", required=True),
    FieldSpec("perimetre", "text"),
    FieldSpec("nature", "text"),
    FieldSpec("consommation", "integer", required=True, lo=20_000, hi=110_000),
    FieldSpec("prevision_j", "integer", lo=15_000, hi=120_000),
    FieldSpec("prevision_j1", "integer", lo=15_000, hi=120_000),
    FieldSpec("fioul", "integer", lo=0, hi=10_000),
    FieldSpec("charbon", "integer", lo=0, hi=4_000),
    FieldSpec("gaz", "integer", lo=0, hi=15_000),
    FieldSpec("nucleaire", "integer", required=True, lo=0, hi=64_000),
    FieldSpec("eolien", "integer", lo=0, hi=35_000),
    FieldSpec("eolien_terrestre", "integer", lo=0, hi=30_000),
    FieldSpec("eolien_offshore", "integer", lo=0, hi=10_000),
    FieldSpec("solaire", "integer", lo=-300, hi=30_000),
    FieldSpec("hydraulique", "integer", lo=0, hi=26_000),
    FieldSpec("pompage", "integer", lo=-8_000, hi=0),
    FieldSpec("bioenergies", "integer", lo=0, hi=4_000),
    FieldSpec("ech_physiques", "integer", lo=-25_000, hi=25_000),
    FieldSpec("taux_co2", "integer", lo=0, hi=300),
    FieldSpec("ech_comm_angleterre", "integer", lo=-10_000, hi=10_000),
    FieldSpec("ech_comm_espagne", "integer", lo=-10_000, hi=10_000),
    FieldSpec("ech_comm_italie", "integer", lo=-10_000, hi=10_000),
    FieldSpec("ech_comm_suisse", "integer", lo=-10_000, hi=10_000),
    FieldSpec("ech_comm_allemagne_belgique", "integer", lo=-15_000, hi=15_000),
    FieldSpec("fioul_tac", "integer", lo=0, hi=5_000),
    FieldSpec("fioul_cogen", "integer", lo=0, hi=5_000),
    FieldSpec("fioul_autres", "integer", lo=0, hi=5_000),
    FieldSpec("gaz_tac", "integer", lo=0, hi=5_000),
    FieldSpec("gaz_cogen", "integer", lo=0, hi=8_000),
    FieldSpec("gaz_ccg", "integer", lo=0, hi=12_000),
    FieldSpec("gaz_autres", "integer", lo=0, hi=8_000),
    FieldSpec("hydraulique_fil_eau_eclusee", "integer", lo=0, hi=15_000),
    FieldSpec("hydraulique_lacs", "integer", lo=0, hi=12_000),
    FieldSpec("hydraulique_step_turbinage", "integer", lo=0, hi=6_000),
    FieldSpec("bioenergies_dechets", "integer", lo=0, hi=2_000),
    FieldSpec("bioenergies_biomasse", "integer", lo=0, hi=2_000),
    FieldSpec("bioenergies_biogaz", "integer", lo=0, hi=2_000),
    FieldSpec("stockage_batterie", "integer", lo=-4_000, hi=100),
    FieldSpec("destockage_batterie", "integer", lo=-100, hi=4_000),
)

FIELD_NAMES: tuple[str, ...] = tuple(spec.name for spec in FIELDS)

#: Production sources for the balance equation and the mix marts.
PRODUCTION_SOURCES: tuple[str, ...] = (
    "fioul",
    "charbon",
    "gaz",
    "nucleaire",
    "eolien",
    "solaire",
    "hydraulique",
    "bioenergies",
)

#: Signed flows completing the balance: consumption ≈ production + flows.
BALANCE_FLOWS: tuple[str, ...] = (
    "pompage",
    "ech_physiques",
    "stockage_batterie",
    "destockage_batterie",
)


def is_observed(row: dict[str, Any]) -> bool:
    """A row is observed (not forecast-only) once consumption is filled."""
    return row.get("consommation") is not None


def parse_record(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Coerce one API record to the contract; returns (row, None) or (None, why)."""
    label = str(raw.get("date_heure") or "<no date_heure>")
    row: dict[str, Any] = {}
    for spec in FIELDS:
        if spec.required and spec.name not in raw:
            return None, f"{label}: required field {spec.name!r} is missing"
        value = raw.get(spec.name)
        try:
            row[spec.name] = _coerce(value, spec.kind)
        except (TypeError, ValueError) as exc:
            return None, f"{label}: field {spec.name!r}: {exc}"
    if row["date_heure"] is None:
        return None, f"{label}: date_heure must not be null"
    return row, None


def _coerce(value: Any, kind: Kind) -> Any:
    if value is None:
        return None
    if kind == "timestamp":
        moment = datetime.fromisoformat(str(value))
        if moment.tzinfo is None:
            raise ValueError("timestamp lacks a timezone offset")
        return moment.astimezone(UTC)
    if kind == "integer":
        number = float(value)
        if number != number:  # NaN
            raise ValueError("not a number")
        return round(number)
    return str(value)
