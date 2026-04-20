"""Funzioni di formattazione testo e orari per messaggi Telegram."""

from __future__ import annotations

from html import escape
from datetime import datetime, time, timedelta
from typing import Any

from .constants import ADDITIONAL_SERVICES, ITALY_TZ

WEEKDAY_LABELS = {
    1: "Lun",
    2: "Mar",
    3: "Mer",
    4: "Gio",
    5: "Ven",
    6: "Sab",
    7: "Dom",
}


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ITALY_TZ)
        return parsed.astimezone(ITALY_TZ)
    except ValueError:
        return None


def parse_time(value: str | None) -> time | None:
    if not value:
        return None
    raw = str(value).strip()
    try:
        if ":" in raw:
            return time.fromisoformat(raw)
        if "." in raw:
            hour_str, minute_str = raw.split(".")
            hour = int(hour_str)
            if hour == 24:
                hour = 0
            minute = int(minute_str) if minute_str else 0
            return time(hour=hour, minute=minute)
        hour = int(raw)
        if hour == 24:
            hour = 0
        return time(hour=hour, minute=0)
    except (ValueError, TypeError):
        return None


def summarize_station(station_data: dict[str, Any], csv_station: dict[str, Any] | None) -> str:
    station_name = station_data.get("nomeImpianto") or station_data.get("name") or "Stazione"
    address = station_data.get("address") or (csv_station or {}).get("address") or "n/d"
    brand = station_data.get("brand") or (csv_station or {}).get("brand") or "n/d"
    station_id = station_data.get("id") or (csv_station or {}).get("id") or "n/d"

    lines = [
        f"⛽ <b>{escape(str(station_name))}</b>",
        f"ID: <code>{station_id}</code>",
        f"Brand: {escape(str(brand))}",
        f"Indirizzo: {escape(str(address))}",
    ]

    if csv_station:
        municipality = csv_station.get("municipality") or ""
        province = csv_station.get("province") or ""
        if municipality or province:
            lines.append(f"Località: {escape(str(municipality))} ({escape(str(province))})")

    phone = station_data.get("phoneNumber")
    if phone:
        lines.append(f"Telefono: {escape(str(phone))}")

    site = station_data.get("website")
    if site:
        lines.append(f"Sito: {escape(str(site))}")

    status = compute_opening_status(station_data.get("orariapertura") or [])
    if status:
        lines.append(status)

    services = station_data.get("services") or []
    service_labels = _extract_service_labels(services)
    if service_labels:
        lines.append("Servizi: " + ", ".join(escape(label) for label in service_labels))

    return "\n".join(lines)


def format_fuels(fuels: list[dict[str, Any]]) -> str:
    if not fuels:
        return "Nessun prezzo carburante disponibile."

    ordered = sorted(
        fuels,
        key=lambda f: (
            str(f.get("name", "")).lower(),
            0 if f.get("isSelf") else 1,
            float(f.get("price") or 999),
        ),
    )
    lines = ["<b>Prezzi</b>"]

    for fuel in ordered:
        fuel_name = fuel.get("name", "Sconosciuto")
        mode = "Self" if fuel.get("isSelf") else "Servito"
        price = fuel.get("price")
        insert = parse_iso_datetime(fuel.get("insertDate"))
        insert_label = insert.strftime("%d/%m %H:%M") if insert else "n/d"
        if price is None:
            lines.append(f"- {escape(str(fuel_name))} ({mode}): n/d")
            continue
        lines.append(
            f"- {escape(str(fuel_name))} ({mode}): <b>{price:.3f} €/L</b> · agg. {insert_label}"
        )

    return "\n".join(lines)


def _extract_service_labels(services: list[dict[str, Any] | str | int]) -> list[str]:
    labels: list[str] = []
    for service in services:
        service_id: str | None = None
        explicit_description: str | None = None
        if isinstance(service, dict):
            if service.get("id") is not None:
                service_id = str(service.get("id"))
            explicit_description = service.get("description")
        elif isinstance(service, int):
            service_id = str(service)
        elif isinstance(service, str):
            service_id = service

        if explicit_description:
            labels.append(explicit_description)
            continue
        if service_id and service_id in ADDITIONAL_SERVICES:
            labels.append(ADDITIONAL_SERVICES[service_id])

    return sorted(set(labels))


def compute_opening_status(opening_hours: list[dict[str, Any]]) -> str | None:
    if not opening_hours:
        return None

    now = datetime.now(tz=ITALY_TZ)
    current_weekday = now.weekday() + 1
    today = next((day for day in opening_hours if day.get("giornoSettimanaId") == current_weekday), None)

    if not today:
        return "Stato: orari non disponibili"

    if today.get("flagH24"):
        return "Stato: aperto 24/7"

    is_open = _is_open_in_schedule(today, now.time())
    next_change_type, next_change = _find_next_change(opening_hours, now)

    if next_change:
        when = next_change.strftime("%H:%M")
        if next_change.date() != now.date():
            when = next_change.strftime("%H:%M (%d/%m)")
        if next_change_type == "closes_at":
            return f"Stato: aperto · chiude alle {when}"
        if next_change_type == "opens_at":
            return f"Stato: chiuso · apre alle {when}" if not is_open else f"Stato: aperto"

    return "Stato: aperto" if is_open else "Stato: chiuso"


def _is_open_in_schedule(schedule: dict[str, Any], current_time: time) -> bool:
    if schedule.get("flagChiusura"):
        return False

    if schedule.get("flagH24"):
        return True

    if schedule.get("flagOrarioContinuato"):
        open_time = parse_time(schedule.get("oraAperturaOrarioContinuato"))
        close_time = parse_time(schedule.get("oraChiusuraOrarioContinuato"))
        if open_time and close_time:
            if open_time <= close_time:
                return open_time <= current_time <= close_time
            return current_time >= open_time or current_time <= close_time
        return False

    morning_open = parse_time(schedule.get("oraAperturaMattina"))
    morning_close = parse_time(schedule.get("oraChiusuraMattina"))
    afternoon_open = parse_time(schedule.get("oraAperturaPomeriggio"))
    afternoon_close = parse_time(schedule.get("oraChiusuraPomeriggio"))

    if morning_open and morning_close and morning_open <= current_time <= morning_close:
        return True
    if afternoon_open and afternoon_close and afternoon_open <= current_time <= afternoon_close:
        return True
    return False


def _find_next_change(opening_hours: list[dict[str, Any]], now: datetime) -> tuple[str, datetime | None]:
    current_weekday = now.weekday() + 1
    today = next((day for day in opening_hours if day.get("giornoSettimanaId") == current_weekday), None)

    if today and not today.get("flagChiusura") and not today.get("flagNonComunicato"):
        current_time = now.time()
        if _is_open_in_schedule(today, current_time):
            close_dt = _find_next_closing_today(today, now)
            if close_dt:
                return "closes_at", close_dt

    open_dt = _find_next_opening(opening_hours, now)
    if open_dt:
        return "opens_at", open_dt

    return "none", None


def _find_next_closing_today(schedule: dict[str, Any], now: datetime) -> datetime | None:
    current_time = now.time()

    if schedule.get("flagOrarioContinuato"):
        close_time = parse_time(schedule.get("oraChiusuraOrarioContinuato"))
        if close_time and close_time > current_time:
            return now.replace(hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0)
        return None

    close_candidates = [
        parse_time(schedule.get("oraChiusuraMattina")),
        parse_time(schedule.get("oraChiusuraPomeriggio")),
    ]
    for close_time in close_candidates:
        if close_time and close_time > current_time:
            return now.replace(hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0)
    return None


def _find_next_opening(opening_hours: list[dict[str, Any]], now: datetime) -> datetime | None:
    current_weekday = now.weekday() + 1

    for day_offset in range(7):
        check_weekday = (current_weekday + day_offset - 1) % 7 + 1
        check_date = now + timedelta(days=day_offset)
        day_schedule = next(
            (day for day in opening_hours if day.get("giornoSettimanaId") == check_weekday),
            None,
        )
        if not day_schedule:
            continue
        if day_schedule.get("flagChiusura") or day_schedule.get("flagNonComunicato"):
            continue

        if day_schedule.get("flagH24"):
            if day_offset == 0:
                return None
            return datetime.combine(check_date.date(), time(0, 0), tzinfo=ITALY_TZ)

        opening_slots: list[time | None] = []
        if day_schedule.get("flagOrarioContinuato"):
            opening_slots.append(parse_time(day_schedule.get("oraAperturaOrarioContinuato")))
        else:
            opening_slots.append(parse_time(day_schedule.get("oraAperturaMattina")))
            opening_slots.append(parse_time(day_schedule.get("oraAperturaPomeriggio")))

        for slot in opening_slots:
            if slot is None:
                continue
            if day_offset == 0 and slot <= now.time():
                continue
            return datetime.combine(check_date.date(), slot, tzinfo=ITALY_TZ)

    return None


def format_nearest_stations(stations: list[dict[str, Any]]) -> str:
    if not stations:
        return "Nessuna stazione trovata nel raggio indicato."

    lines = ["<b>Stazioni vicine</b>"]
    for station in stations:
        sid = station.get("id")
        name = station.get("name") or "Stazione"
        municipality = station.get("municipality") or ""
        province = station.get("province") or ""
        distance = station.get("distance_km")
        distance_text = f"{distance:.2f} km" if distance is not None else "n/d"
        lines.append(
            "- "
            f"<code>{sid}</code> · {escape(str(name))} · "
            f"{escape(str(municipality))} ({escape(str(province))}) · {distance_text}"
        )
    return "\n".join(lines)


def format_zone_cheapest(
    results: list[dict[str, Any]],
    fuel_query: str,
    service_mode: str,
) -> str:
    normalized_query = fuel_query.lower().strip()
    is_self = service_mode.lower() == "self"

    best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    for station in results:
        for fuel in station.get("fuels", []):
            fuel_name = str(fuel.get("name", "")).lower()
            if normalized_query not in fuel_name:
                continue
            if bool(fuel.get("isSelf")) != is_self:
                continue
            price = fuel.get("price")
            if price is None:
                continue
            score = float(price)
            if best is None or score < best[0]:
                best = (score, station, fuel)

    if not best:
        return "Nessuna offerta trovata con i filtri richiesti."

    price, station, fuel = best
    distance = station.get("distance")
    if distance is None:
        distance_text = "n/d"
    else:
        try:
            distance_text = f"{float(distance):.2f} km"
        except (TypeError, ValueError):
            distance_text = "n/d"
    lines = [
        "<b>Miglior prezzo trovato</b>",
        f"Carburante: {escape(str(fuel.get('name')))} ({'Self' if fuel.get('isSelf') else 'Servito'})",
        f"Prezzo: <b>{price:.3f} €/L</b>",
        f"Stazione: {escape(str(station.get('name')))}",
        f"ID: <code>{station.get('id')}</code>",
        f"Brand: {escape(str(station.get('brand') or 'n/d'))}",
        f"Distanza: {distance_text}",
    ]
    return "\n".join(lines)
