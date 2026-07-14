"""Client for the ODRÉ (Opendatasoft) explore API.

Uses the /exports/json endpoint: unlike /records it has no pagination cap, so
one request covers any ingestion window. Retries transient failures with
backoff; API-level errors (a JSON object instead of a list) surface as
SourceError with the upstream message.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from courant.errors import SourceError

_MAX_ATTEMPTS = 3


class OdreClient:
    def __init__(
        self,
        base_url: str,
        dataset: str,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.dataset = dataset
        self._sleep = sleep
        self._http = http or httpx.Client(
            base_url=base_url,
            timeout=120.0,
            headers={"User-Agent": "courant-pipeline"},
            follow_redirects=True,
        )

    def fetch_window(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """All records with start <= date_heure < end, ordered by date_heure."""
        where = f"date_heure >= '{_utc_iso(start)}' AND date_heure < '{_utc_iso(end)}'"
        params = {"where": where, "order_by": "date_heure"}
        path = f"/api/explore/v2.1/catalog/datasets/{self.dataset}/exports/json"

        last_error = ""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                if attempt < _MAX_ATTEMPTS:
                    self._sleep(float(attempt))
                    continue
                raise SourceError(f"could not reach the ODRE API: {exc}") from exc

            if response.status_code >= 500 or response.status_code == 429:
                last_error = f"HTTP {response.status_code}"
                if attempt < _MAX_ATTEMPTS:
                    self._sleep(float(attempt))
                    continue
                raise SourceError(
                    f"ODRE API failed after {_MAX_ATTEMPTS} attempts: {last_error}"
                )
            if response.status_code >= 400:
                raise SourceError(
                    f"ODRE API returned {response.status_code}: {response.text[:200]}"
                )
            return _parse_payload(response)
        raise SourceError(f"ODRE API failed after {_MAX_ATTEMPTS} attempts: {last_error}")


def _parse_payload(response: httpx.Response) -> list[dict[str, Any]]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceError(f"ODRE API returned invalid JSON: {exc}") from exc
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):  # the API reports errors as a JSON object
        message = payload.get("message") or payload.get("error_code") or "unknown error"
        raise SourceError(f"ODRE API error: {message}")
    raise SourceError(f"unexpected payload shape: {type(payload).__name__}")


def _utc_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise SourceError("ingestion windows must be timezone-aware")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
