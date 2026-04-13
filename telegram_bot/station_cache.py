"""Cache CSV stazioni attive MIMIT con ricerca testuale e geospaziale."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from .constants import CSV_COLUMNS, CSV_URL, DEFAULT_HEADERS, ITALY_TZ


@dataclass
class StationMatch:
    station: dict[str, Any]
    score: int


class StationCache:
    def __init__(self, session: aiohttp.ClientSession, data_dir: Path) -> None:
        self._session = session
        self._data_dir = data_dir
        self._cache_path = data_dir / "stations_cache.json"
        self._stations: dict[str, dict[str, Any]] = {}
        self._last_update: datetime | None = None
        self._csv_separator: str = "|"

    async def initialize(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        loaded = await self._load_cache_file()
        if not loaded or not self._stations or self._cache_is_stale():
            await self.refresh(force=True)

    def _cache_is_stale(self) -> bool:
        if not self._last_update:
            return True
        now = datetime.now(tz=ITALY_TZ)
        return now - self._last_update > timedelta(hours=24)

    async def refresh(self, force: bool = False) -> bool:
        if not force and self._stations and not self._cache_is_stale():
            return True

        headers = {
            **DEFAULT_HEADERS,
            "Accept": "text/csv,application/csv,text/plain,*/*",
        }
        async with self._session.get(
            CSV_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=90),
        ) as response:
            if response.status != 200:
                return False
            content = await response.text()

        stations, separator = await asyncio.to_thread(self._parse_csv, content)
        if not stations:
            return False

        self._stations = stations
        self._csv_separator = separator
        self._last_update = datetime.now(tz=ITALY_TZ)
        await self._save_cache_file()
        return True

    def get_station(self, station_id: str | int) -> dict[str, Any] | None:
        return self._stations.get(str(station_id))

    def search(self, text: str, limit: int = 10) -> list[dict[str, Any]]:
        query = " ".join(text.lower().split())
        if not query:
            return []

        results: list[StationMatch] = []
        for station in self._stations.values():
            fields = [
                station.get("id", ""),
                station.get("name", ""),
                station.get("address", ""),
                station.get("municipality", ""),
                station.get("province", ""),
                station.get("brand", ""),
            ]
            haystack = " ".join(str(item).lower() for item in fields if item)
            if query in haystack:
                score = 0
                if station.get("id") == query:
                    score += 100
                if station.get("name") and query in station["name"].lower():
                    score += 30
                if station.get("municipality") and query in station["municipality"].lower():
                    score += 20
                if station.get("address") and query in station["address"].lower():
                    score += 10
                results.append(StationMatch(station=station, score=score))

        results.sort(key=lambda x: (-x.score, x.station.get("name") or ""))
        return [entry.station for entry in results[:limit]]

    def nearest(
        self,
        lat: float,
        lon: float,
        limit: int = 10,
        max_radius_km: float | None = None,
    ) -> list[dict[str, Any]]:
        ranked: list[tuple[float, dict[str, Any]]] = []
        for station in self._stations.values():
            s_lat = station.get("latitude")
            s_lon = station.get("longitude")
            if s_lat is None or s_lon is None:
                continue
            distance = _haversine_km(lat, lon, float(s_lat), float(s_lon))
            if max_radius_km is not None and distance > max_radius_km:
                continue
            ranked.append((distance, station))

        ranked.sort(key=lambda item: item[0])
        output: list[dict[str, Any]] = []
        for distance, station in ranked[:limit]:
            item = dict(station)
            item["distance_km"] = round(distance, 3)
            output.append(item)
        return output

    async def _load_cache_file(self) -> bool:
        try:
            raw = await asyncio.to_thread(self._cache_path.read_text, "utf-8")
            payload = json.loads(raw)
            self._stations = payload.get("stations", {})
            self._csv_separator = payload.get("csv_separator", "|")
            last_update = payload.get("last_update")
            if last_update:
                self._last_update = datetime.fromisoformat(last_update)
                if self._last_update.tzinfo is None:
                    self._last_update = self._last_update.replace(tzinfo=ITALY_TZ)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    async def _save_cache_file(self) -> None:
        payload = {
            "version": "2.0",
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "csv_separator": self._csv_separator,
            "stations": self._stations,
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._cache_path.write_text, serialized, "utf-8")

    @staticmethod
    def _detect_separator(header_line: str) -> str:
        pipe_count = header_line.count("|")
        semicolon_count = header_line.count(";")
        if pipe_count >= semicolon_count:
            return "|"
        return ";"

    def _parse_csv(self, csv_content: str) -> tuple[dict[str, dict[str, Any]], str]:
        lines = csv_content.splitlines()
        if len(lines) < 3:
            return {}, "|"

        header_line = lines[1]
        separator = self._detect_separator(header_line)
        headers = [h.strip().strip('"') for h in header_line.split(separator)]

        col_indices: dict[str, int] = {}
        for csv_col, internal_col in CSV_COLUMNS.items():
            col_indices[internal_col] = headers.index(csv_col) if csv_col in headers else -1

        stations_cache: dict[str, dict[str, Any]] = {}
        for line in lines[2:]:
            values = [v.strip().strip('"') for v in line.split(separator)]
            if len(values) < len(headers):
                continue

            station: dict[str, Any] = {}
            for internal_col, idx in col_indices.items():
                if idx < 0 or idx >= len(values):
                    station[internal_col] = None
                    continue

                value = values[idx]
                if internal_col in {"latitude", "longitude"}:
                    try:
                        station[internal_col] = float(value.replace(",", ".")) if value else None
                    except ValueError:
                        station[internal_col] = None
                else:
                    station[internal_col] = value or None

            station_id = station.get("id")
            if station_id and station.get("latitude") and station.get("longitude"):
                stations_cache[str(station_id)] = station

        return stations_cache, separator


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_km = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_km * c
