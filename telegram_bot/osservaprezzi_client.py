"""Client async per API Osservaprezzi carburanti."""

from __future__ import annotations

from typing import Any

import aiohttp

from .constants import (
    BASE_URL,
    DEFAULT_HEADERS,
    SEARCH_ZONE_ENDPOINT,
    STATION_ENDPOINT,
)


class OsservaprezziError(RuntimeError):
    """Errore applicativo per API Osservaprezzi."""


class OsservaprezziClient:
    """Client HTTP leggero per endpoint Osservaprezzi."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def fetch_station(self, station_id: str | int, timeout: int = 30) -> dict[str, Any]:
        url = f"{BASE_URL}{STATION_ENDPOINT.format(station_id=station_id)}"
        async with self._session.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            if response.status == 200:
                return await response.json()
            if response.status == 404:
                raise OsservaprezziError(f"Stazione {station_id} non trovata")
            if response.status == 429:
                raise OsservaprezziError("Rate limit API raggiunto, riprova tra poco")
            raise OsservaprezziError(
                f"Errore servizio ({response.status}): {response.reason}"
            )

    async def search_zone(
        self,
        lat: float,
        lng: float,
        radius_km: float = 5.0,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        url = f"{BASE_URL}{SEARCH_ZONE_ENDPOINT}"
        payload = {
            "points": [{"lat": lat, "lng": lng}],
            "radius": radius_km,
        }
        async with self._session.post(
            url,
            headers=DEFAULT_HEADERS,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            if response.status != 200:
                if response.status == 429:
                    raise OsservaprezziError("Rate limit API raggiunto, riprova tra poco")
                raise OsservaprezziError(
                    f"Errore ricerca zona ({response.status}): {response.reason}"
                )

            data = await response.json()
            if not data.get("success"):
                raise OsservaprezziError("Ricerca zona non riuscita")
            return data.get("results", [])
