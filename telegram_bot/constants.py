"""Costanti condivise per il bot Telegram Osservaprezzi."""

from __future__ import annotations

from zoneinfo import ZoneInfo

BASE_URL = "https://carburanti.mise.gov.it/ospzApi"
STATION_ENDPOINT = "/registry/servicearea/{station_id}"
SEARCH_ZONE_ENDPOINT = "/search/zone"
CSV_URL = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
    ),
}

CSV_COLUMNS = {
    "idImpianto": "id",
    "Gestore": "operator",
    "Bandiera": "brand",
    "Tipo Impianto": "station_type",
    "Nome Impianto": "name",
    "Indirizzo": "address",
    "Comune": "municipality",
    "Provincia": "province",
    "Latitudine": "latitude",
    "Longitudine": "longitude",
}

ADDITIONAL_SERVICES = {
    "1": "Food&Beverage",
    "2": "Officina",
    "3": "Sosta Camper/Tir",
    "4": "Scarico Camper",
    "5": "Area bambini",
    "6": "Bancomat",
    "7": "Accesso disabili",
    "8": "Wi-Fi",
    "9": "Gommista",
    "10": "Autolavaggio",
    "11": "Ricarica elettrica",
}

ITALY_TZ = ZoneInfo("Europe/Rome")
