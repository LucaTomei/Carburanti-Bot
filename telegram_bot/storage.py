"""Persistenza locale configurazioni utente del bot."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import ITALY_TZ


@dataclass
class UserSettings:
    user_id: int
    station_id: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    notify_time: str | None = None
    last_notification_at: str | None = None
    # Campi aggiuntivi (retrocompatibili: default None/False per utenti esistenti)
    username: str | None = None
    full_name: str | None = None
    first_seen: str | None = None
    blocked: bool = False

    @classmethod
    def from_dict(cls, user_id: int, data: dict[str, Any]) -> "UserSettings":
        return cls(
            user_id=user_id,
            station_id=data.get("station_id"),
            location_lat=data.get("location_lat"),
            location_lon=data.get("location_lon"),
            notify_time=data.get("notify_time"),
            last_notification_at=data.get("last_notification_at"),
            username=data.get("username"),
            full_name=data.get("full_name"),
            first_seen=data.get("first_seen"),
            blocked=data.get("blocked", False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "station_id": self.station_id,
            "location_lat": self.location_lat,
            "location_lon": self.location_lon,
            "notify_time": self.notify_time,
            "last_notification_at": self.last_notification_at,
            "username": self.username,
            "full_name": self.full_name,
            "first_seen": self.first_seen,
            "blocked": self.blocked,
        }

    @property
    def display_name(self) -> str:
        if self.full_name:
            return self.full_name
        if self.username:
            return f"@{self.username}"
        return str(self.user_id)


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

    # ── Metodi standard ───────────────────────────────────────────────────────

    async def update_station(self, user_id: int, station_id: str | None) -> None:
        self.get(user_id).station_id = station_id
        await self._save()

    async def update_location(self, user_id: int, lat: float, lon: float) -> None:
        s = self.get(user_id)
        s.location_lat = lat
        s.location_lon = lon
        await self._save()

    async def update_notify_time(self, user_id: int, notify_time: str | None) -> None:
        self.get(user_id).notify_time = notify_time
        await self._save()

    async def mark_notification_sent(self, user_id: int, at: datetime) -> None:
        self.get(user_id).last_notification_at = at.isoformat()
        await self._save()

    def all_with_notifications(self) -> list[UserSettings]:
        return [
            s for s in self._users.values()
            if s.notify_time and s.station_id and not s.blocked
        ]

    # ── Metodi admin ──────────────────────────────────────────────────────────

    async def upsert_user_info(
        self,
        user_id: int,
        username: str | None,
        full_name: str | None,
    ) -> bool:
        """
        Aggiorna username e nome dell'utente.
        Ritorna True se è la prima volta che questo utente appare.
        """
        settings = self.get(user_id)
        is_new = settings.first_seen is None
        if is_new:
            settings.first_seen = datetime.now(tz=ITALY_TZ).isoformat()
        settings.username = username
        settings.full_name = full_name
        await self._save()
        return is_new

    async def set_blocked(self, user_id: int, blocked: bool) -> None:
        self.get(user_id).blocked = blocked
        await self._save()

    def is_blocked(self, user_id: int) -> bool:
        s = self._users.get(user_id)
        return s.blocked if s else False

    def all_users(self) -> list[UserSettings]:
        """Tutti gli utenti, ordinati per data di primo accesso."""
        return sorted(
            self._users.values(),
            key=lambda s: s.first_seen or "",
            reverse=True,
        )
