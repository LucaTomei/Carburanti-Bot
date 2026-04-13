"""Persistenza locale configurazioni utente del bot."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class UserSettings:
    user_id: int
    station_id: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    notify_time: str | None = None
    last_notification_at: str | None = None

    @classmethod
    def from_dict(cls, user_id: int, data: dict[str, Any]) -> "UserSettings":
        return cls(
            user_id=user_id,
            station_id=data.get("station_id"),
            location_lat=data.get("location_lat"),
            location_lon=data.get("location_lon"),
            notify_time=data.get("notify_time"),
            last_notification_at=data.get("last_notification_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "station_id": self.station_id,
            "location_lat": self.location_lat,
            "location_lon": self.location_lon,
            "notify_time": self.notify_time,
            "last_notification_at": self.last_notification_at,
        }


class UserStorage:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._path = data_dir / "users.json"
        self._users: dict[int, UserSettings] = {}

    async def initialize(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        await self._load()

    async def _load(self) -> None:
        try:
            raw = await asyncio.to_thread(self._path.read_text, "utf-8")
            payload = json.loads(raw)
            self._users = {
                int(user_id): UserSettings.from_dict(int(user_id), config)
                for user_id, config in payload.get("users", {}).items()
            }
        except FileNotFoundError:
            self._users = {}
        except Exception:
            self._users = {}

    async def _save(self) -> None:
        payload = {
            "users": {
                str(user_id): settings.to_dict()
                for user_id, settings in self._users.items()
            }
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._path.write_text, serialized, "utf-8")

    def get(self, user_id: int) -> UserSettings:
        if user_id not in self._users:
            self._users[user_id] = UserSettings(user_id=user_id)
        return self._users[user_id]

    async def update_station(self, user_id: int, station_id: str | None) -> None:
        settings = self.get(user_id)
        settings.station_id = station_id
        await self._save()

    async def update_location(self, user_id: int, lat: float, lon: float) -> None:
        settings = self.get(user_id)
        settings.location_lat = lat
        settings.location_lon = lon
        await self._save()

    async def update_notify_time(self, user_id: int, notify_time: str | None) -> None:
        settings = self.get(user_id)
        settings.notify_time = notify_time
        await self._save()

    async def mark_notification_sent(self, user_id: int, at: datetime) -> None:
        settings = self.get(user_id)
        settings.last_notification_at = at.isoformat()
        await self._save()

    def all_with_notifications(self) -> list[UserSettings]:
        return [
            settings
            for settings in self._users.values()
            if settings.notify_time and settings.station_id
        ]
