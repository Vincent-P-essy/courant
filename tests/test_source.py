from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from conftest import WINDOW_RECORDS
from courant.errors import SourceError
from courant.source import OdreClient

UTC = timezone.utc

BASE = "https://odre.opendatasoft.com"
EXPORT = f"{BASE}/api/explore/v2.1/catalog/datasets/eco2mix-national-tr/exports/json"
START = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
END = datetime(2026, 7, 14, 1, 0, tzinfo=UTC)


def _client() -> tuple[OdreClient, list[float]]:
    sleeps: list[float] = []
    return OdreClient(BASE, "eco2mix-national-tr", sleep=sleeps.append), sleeps


@respx.mock
def test_fetch_window_builds_the_odsql_clause() -> None:
    route = respx.get(EXPORT).mock(return_value=httpx.Response(200, json=WINDOW_RECORDS))
    client, _ = _client()
    rows = client.fetch_window(START, END)
    assert len(rows) == 4
    params = route.calls[0].request.url.params
    assert params["where"] == (
        "date_heure >= '2026-07-14T00:00:00' AND date_heure < '2026-07-14T01:00:00'"
    )
    assert params["order_by"] == "date_heure"


@respx.mock
def test_window_bounds_are_converted_to_utc() -> None:
    route = respx.get(EXPORT).mock(return_value=httpx.Response(200, json=[]))
    client, _ = _client()
    paris = datetime(2026, 7, 14, 2, 0).astimezone()  # whatever local tz, explicit below
    paris = datetime.fromisoformat("2026-07-14T02:00:00+02:00")
    client.fetch_window(paris, datetime.fromisoformat("2026-07-14T03:00:00+02:00"))
    where = route.calls[0].request.url.params["where"]
    assert "2026-07-14T00:00:00" in where
    assert "2026-07-14T01:00:00" in where


def test_naive_window_is_rejected() -> None:
    client, _ = _client()
    with pytest.raises(SourceError, match="timezone-aware"):
        client.fetch_window(datetime(2026, 7, 14), END)


@respx.mock
def test_transient_errors_are_retried() -> None:
    respx.get(EXPORT).mock(
        side_effect=[
            httpx.Response(503, text="unavailable"),
            httpx.Response(200, json=WINDOW_RECORDS),
        ]
    )
    client, sleeps = _client()
    assert len(client.fetch_window(START, END)) == 4
    assert len(sleeps) == 1


@respx.mock
def test_retries_exhaust_into_source_error() -> None:
    respx.get(EXPORT).mock(return_value=httpx.Response(500, text="boom"))
    client, sleeps = _client()
    with pytest.raises(SourceError, match="after 3 attempts"):
        client.fetch_window(START, END)
    assert len(sleeps) == 2


@respx.mock
def test_client_errors_do_not_retry() -> None:
    respx.get(EXPORT).mock(return_value=httpx.Response(400, text="bad where clause"))
    client, sleeps = _client()
    with pytest.raises(SourceError, match="400"):
        client.fetch_window(START, END)
    assert sleeps == []


@respx.mock
def test_api_error_objects_surface_their_message() -> None:
    respx.get(EXPORT).mock(
        return_value=httpx.Response(
            200, json={"error_code": "ODSQLSyntaxError", "message": "unexpected token"}
        )
    )
    client, _ = _client()
    with pytest.raises(SourceError, match="unexpected token"):
        client.fetch_window(START, END)


@respx.mock
def test_invalid_json_surfaces_as_source_error() -> None:
    respx.get(EXPORT).mock(return_value=httpx.Response(200, text="<html>gateway</html>"))
    client, _ = _client()
    with pytest.raises(SourceError, match="invalid JSON"):
        client.fetch_window(START, END)
