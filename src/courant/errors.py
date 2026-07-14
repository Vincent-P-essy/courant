"""Exception hierarchy. Everything user-facing derives from CourantError."""

from __future__ import annotations


class CourantError(Exception):
    """Base class for failures that should surface as a clean CLI error."""


class SourceError(CourantError):
    """The upstream open-data API could not be queried."""


class ConfigError(CourantError):
    """Invalid configuration."""


class WarehouseError(CourantError):
    """The DuckDB warehouse rejected an operation."""
