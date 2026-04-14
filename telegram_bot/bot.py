"""Bot Telegram completo per Osservaprezzi Carburanti."""

from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from pathlib import Path

import aiohttp
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .constants import ITALY_TZ
from .formatting import (
    format_fuels,
    format_nearest_stations,
    format_zone_cheapest,
    parse_iso_datetime,
    summarize_station,
)
from .osservaprezzi_client import OsservaprezziClient, OsservaprezziError
from .station_cache import StationCache
from .storage import UserStorage

_LOGGER = logging.getLogger(__name__)

# ── Reply keyboard button labels ──────────────────────────────────────────────
BTN_PRICES = "📋 Prezzi stazione"
BTN_SET_STATION = "⭐ Imposta stazione"
BTN_SEARCH = "🔍 Cerca stazioni"
BTN_NEARBY = "📍 Stazioni vicine"
BTN_BEST = "🏆 Miglior prezzo"
BTN_NOTIFY = "🔔 Imposta notifica"
BTN_DISABLE_NOTIFY = "🔕 Disattiva notifiche"
BTN_MY_SETTINGS = "⚙️ Le mie impostazioni"
BTN_REMOVE_STATION = "🗑 Rimuovi stazione"
BTN_HELP = "❓ Aiuto"
BTN_CANCEL = "✖️ Annulla"

# ── Callback data prefixes (max 64 byte totali per Telegram) ──────────────────
CB_SET_STATION = "st:set:"    # st:set:{station_id}
CB_SHOW_PRICES = "st:pr:"     # st:pr:{station_id}
CB_BEST_FUEL = "bf:"          # bf:{fuel_name}
CB_BEST_MODE = "bm:"          # bm:{self|servito}
CB_REMOVE_OK = "rm:ok"
CB_REMOVE_NO = "rm:no"

# ── Carburanti disponibili per la tastiera inline ─────────────────────────────
_FUEL_OPTIONS: list[tuple[str, str]] = [
    ("⛽ Benzina", "benzina"),
    ("🛢 Gasolio", "gasolio"),
    ("🟢 GPL", "gpl"),
    ("⚡ Metano", "metano"),
    ("🔋 Elettrico", "elettrico"),
    ("🚗 AdBlue", "adblue"),
]

_START_MESSAGE = (
    "⛽ <b>Osservaprezzi Carburanti</b>\n\n"
    "Monitora i prezzi carburante in Italia con i dati ufficiali MIMIT.\n\n"
    "<b>Cosa puoi fare:</b>\n"
    "• Salvare la tua stazione preferita\n"
    "• Leggere prezzi e dettagli aggiornati\n"
    "• Trovare stazioni vicine a te\n"
    "• Trovare il miglior prezzo nella tua zona\n"
    "• Ricevere notifiche giornaliere automatiche\n\n"
    "Usa i <b>pulsanti</b> qui sotto per iniziare.\n\n"
    "💡 Per <i>Stazioni vicine</i> e <i>Miglior prezzo</i>, "
    "condividi prima la posizione con il pulsante <b>📍 Condividi posizione</b>."
)


class FuelPriceTelegramBot:
    def __init__(self, token: str, data_dir: Path) -> None:
        self._token = token
        self._data_dir = data_dir
        self._session: aiohttp.ClientSession | None = None
        self._client: OsservaprezziClient | None = None
        self._storage = UserStorage(data_dir)
        self._station_cache: StationCache | None = None

        self.app = (
            Application.builder()
            .token(self._token)
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
            .build()
        )
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("station", self.cmd_station))
        self.app.add_handler(CommandHandler("stazione", self.cmd_station))
        self.app.add_handler(CommandHandler("unset_station", self.cmd_unset_station))
        self.app.add_handler(CommandHandler("prezzi", self.cmd_prices))
        self.app.add_handler(CommandHandler("cerca", self.cmd_search))
        self.app.add_handler(CommandHandler("vicino", self.cmd_nearby))
        self.app.add_handler(CommandHandler("best", self.cmd_best))
        self.app.add_handler(CommandHandler("notifica", self.cmd_notify))
        self.app.add_handler(CommandHandler("no_notifica", self.cmd_disable_notify))
        self.app.add_handler(CommandHandler("mia", self.cmd_my_settings))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        self.app.add_handler(MessageHandler(filters.LOCATION, self.handle_location))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))

    async def _post_init(self, application: Application) -> None:
        self._session = aiohttp.ClientSession()
        self._client = OsservaprezziClient(self._session)
        self._station_cache = StationCache(self._session, self._data_dir)

        await self._storage.initialize()
        await self._station_cache.initialize()

        await application.bot.set_my_commands(
            [
                BotCommand("start", "Menu principale"),
                BotCommand("prezzi", "Prezzi stazione: /prezzi [id]"),
                BotCommand("station", "Imposta stazione preferita: /station <id>"),
                BotCommand("cerca", "Ricerca stazioni: /cerca testo"),
                BotCommand("vicino", "Stazioni vicine (usa posizione)"),
                BotCommand("best", "Miglior prezzo: /best <carburante> <self|servito>"),
                BotCommand("notifica", "Notifica giornaliera: /notifica HH:MM"),
                BotCommand("no_notifica", "Disattiva notifiche"),
                BotCommand("mia", "Mostra configurazione"),
                BotCommand("help", "Mostra aiuto"),
            ]
        )

        application.job_queue.run_repeating(
            self._notification_tick,
            interval=60,
            first=10,
            name="daily_notification_dispatch",
        )
        application.job_queue.run_repeating(
            self._cache_refresh_tick,
            interval=6 * 3600,
            first=120,
            name="station_cache_refresh",
        )
        _LOGGER.info("Bot pronto: cache stazioni inizializzata")

    async def _post_shutdown(self, _: Application) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Keyboards ─────────────────────────────────────────────────────────────

    def _main_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [BTN_PRICES, BTN_SET_STATION],
                [BTN_SEARCH, BTN_NEARBY],
                [BTN_BEST, KeyboardButton("📍 Condividi posizione", request_location=True)],
                [BTN_NOTIFY, BTN_DISABLE_NOTIFY],
                [BTN_MY_SETTINGS, BTN_REMOVE_STATION],
                [BTN_HELP, BTN_CANCEL],
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
        )

    @staticmethod
    def _station_action_keyboard(station_id: str) -> InlineKeyboardMarkup:
        """Azioni rapide su una singola stazione."""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⭐ Imposta preferita", callback_data=f"{CB_SET_STATION}{station_id}"),
                InlineKeyboardButton("📋 Vedi prezzi", callback_data=f"{CB_SHOW_PRICES}{station_id}"),
            ]
        ])

    @staticmethod
    def _stations_list_keyboard(stations: list[dict]) -> InlineKeyboardMarkup:
        """Una riga di pulsanti per ognuna delle prime 5 stazioni trovate."""
        rows = []
        for station in stations[:5]:
            sid = str(station.get("id", ""))
            name = str(station.get("name") or "Stazione")
            label = (name[:18] + "…") if len(name) > 18 else name
            rows.append([
                InlineKeyboardButton(f"⭐ {label}", callback_data=f"{CB_SET_STATION}{sid}"),
                InlineKeyboardButton("📋 Prezzi", callback_data=f"{CB_SHOW_PRICES}{sid}"),
            ])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _fuel_keyboard() -> InlineKeyboardMarkup:
        """Selettore carburante per Miglior prezzo."""
        rows = []
        for i in range(0, len(_FUEL_OPTIONS), 2):
            row = [
                InlineKeyboardButton(label, callback_data=f"{CB_BEST_FUEL}{val}")
                for label, val in _FUEL_OPTIONS[i:i + 2]
            ]
            rows.append(row)
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _mode_keyboard() -> InlineKeyboardMarkup:
        """Selettore modalità self/servito."""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔧 Self", callback_data=f"{CB_BEST_MODE}self"),
            InlineKeyboardButton("👨 Servito", callback_data=f"{CB_BEST_MODE}servito"),
        ]])

    @staticmethod
    def _remove_confirm_keyboard() -> InlineKeyboardMarkup:
        """Conferma rimozione stazione."""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Conferma", callback_data=CB_REMOVE_OK),
            InlineKeyboardButton("❌ Annulla", callback_data=CB_REMOVE_NO),
        ]])

    async def _reply_with_keyboard(
        self, update: Update, text: str, parse_mode: str | None = None
    ) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            text=text,
            parse_mode=parse_mode,
            reply_markup=self._main_keyboard(),
            disable_web_page_preview=True,
        )

    # ── Commands ──────────────────────────────────────────────────────────────

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if update.message:
            await update.message.reply_text(
                _START_MESSAGE,
                parse_mode=ParseMode.HTML,
                reply_markup=self._main_keyboard(),
            )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.cmd_start(update, context)

    async def cmd_station(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return
        if not context.args:
            await update.message.reply_text("Uso: /station <id_stazione>")
            return

        station_id = context.args[0].strip()
        if not station_id.isdigit():
            await update.message.reply_text("L'ID stazione deve essere numerico.")
            return

        try:
            station_data = await self._get_client().fetch_station(station_id)
        except OsservaprezziError as err:
            await update.message.reply_text(f"Errore: {err}")
            return

        await self._storage.update_station(user.id, station_id)
        station_name = station_data.get("nomeImpianto") or station_data.get("name") or station_id
        await update.message.reply_text(
            f"✅ Stazione preferita impostata:\n"
            f"<b>{escape(str(station_name))}</b> — ID <code>{station_id}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=self._station_action_keyboard(station_id),
        )

    async def cmd_unset_station(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        user = update.effective_user
        if not user or not update.message:
            return
        await self._storage.update_station(user.id, None)
        await update.message.reply_text("🗑 Stazione preferita rimossa.")

    async def cmd_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return

        station_id = self._resolve_station_id(user.id, context.args)
        if not station_id:
            await update.message.reply_text(
                "Nessuna stazione impostata.\n"
                "Usa /station <id> oppure /prezzi <id>."
            )
            return

        try:
            text = await self._build_station_message(station_id)
        except OsservaprezziError as err:
            await update.message.reply_text(f"Errore: {err}")
            return

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=self._station_action_keyboard(station_id),
        )

    async def cmd_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Uso: /cerca <testo>")
            return
        query = " ".join(context.args).strip()
        await self._send_search_results(update, query)

    async def cmd_nearby(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        lat, lon, radius = await self._resolve_location_and_radius(update, context)
        if lat is None or lon is None:
            await update.message.reply_text(
                "📍 Condividi prima la posizione oppure usa:\n"
                "/vicino <lat> <lon> [raggio_km]"
            )
            return

        nearby = self._get_station_cache().nearest(lat, lon, limit=5, max_radius_km=radius)
        text = format_nearest_stations(nearby)
        keyboard = self._stations_list_keyboard(nearby) if nearby else None
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def cmd_best(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        if len(context.args) < 2:
            await update.message.reply_text(
                "Uso: /best <carburante> <self|servito> [raggio_km]\n"
                "Esempio: /best gasolio self 5"
            )
            return

        fuel_query = context.args[0]
        service_mode = context.args[1].lower()
        if service_mode not in {"self", "servito"}:
            await update.message.reply_text("Il secondo parametro deve essere 'self' o 'servito'.")
            return

        radius = 5.0
        if len(context.args) >= 3:
            try:
                radius = float(context.args[2])
                if radius <= 0 or radius > 50:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("Raggio non valido: usa un numero tra 0.1 e 50 km.")
                return

        user = update.effective_user
        if not user:
            return
        settings = self._storage.get(user.id)
        if settings.location_lat is None or settings.location_lon is None:
            await update.message.reply_text(
                "📍 Per usare /best, condividi prima la posizione."
            )
            return

        try:
            zone_results = await self._get_client().search_zone(
                settings.location_lat, settings.location_lon, radius_km=radius,
            )
        except OsservaprezziError as err:
            await update.message.reply_text(f"Errore: {err}")
            return

        text = format_zone_cheapest(zone_results, fuel_query=fuel_query, service_mode=service_mode)
        best_id = _extract_best_station_id(zone_results, fuel_query, service_mode)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⭐ Imposta come preferita", callback_data=f"{CB_SET_STATION}{best_id}")
        ]]) if best_id else None
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def cmd_notify(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return

        if not context.args:
            await update.message.reply_text("Uso: /notifica HH:MM")
            return

        notify_time = context.args[0].strip()
        if not self._is_valid_time(notify_time):
            await update.message.reply_text("Orario non valido. Usa formato HH:MM (es. 08:30).")
            return

        settings = self._storage.get(user.id)
        if not settings.station_id:
            await update.message.reply_text("Imposta prima una stazione con /station <id>.")
            return

        await self._storage.update_notify_time(user.id, notify_time)
        await update.message.reply_text(
            f"🔔 Notifica giornaliera attiva alle <b>{notify_time}</b> (ora Italia).",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_disable_notify(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        user = update.effective_user
        if not user or not update.message:
            return
        await self._storage.update_notify_time(user.id, None)
        await update.message.reply_text("🔕 Notifiche giornaliere disattivate.")

    async def cmd_my_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        user = update.effective_user
        if not user or not update.message:
            return

        settings = self._storage.get(user.id)
        station_line = (
            f"<code>{settings.station_id}</code>" if settings.station_id else "non impostata"
        )
        if settings.location_lat is not None and settings.location_lon is not None:
            location_line = f"{settings.location_lat:.5f}, {settings.location_lon:.5f}"
        else:
            location_line = "non impostata"

        lines = [
            "⚙️ <b>Le tue impostazioni</b>",
            "",
            f"⛽ Stazione preferita: {station_line}",
            f"📍 Posizione: {location_line}",
            f"🔔 Notifica giornaliera: {settings.notify_time or 'disattivata'}",
        ]

        keyboard = self._station_action_keyboard(settings.station_id) if settings.station_id else None
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=keyboard
        )

    # ── Callback handler ──────────────────────────────────────────────────────

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()

        data = query.data or ""
        user = update.effective_user
        if not user:
            return

        if data.startswith(CB_SET_STATION):
            await self._cb_set_station(query, user.id, data[len(CB_SET_STATION):])

        elif data.startswith(CB_SHOW_PRICES):
            await self._cb_show_prices(query, data[len(CB_SHOW_PRICES):])

        elif data.startswith(CB_BEST_FUEL):
            fuel = data[len(CB_BEST_FUEL):]
            context.user_data["best_fuel"] = fuel
            await query.edit_message_text(
                f"🏆 <b>Miglior prezzo</b>\n\n"
                f"Carburante: <b>{escape(fuel)}</b>\n\n"
                "Seleziona la modalità di erogazione:",
                parse_mode=ParseMode.HTML,
                reply_markup=self._mode_keyboard(),
            )

        elif data.startswith(CB_BEST_MODE):
            mode = data[len(CB_BEST_MODE):]
            fuel = context.user_data.pop("best_fuel", "")
            if not fuel:
                await query.edit_message_text(
                    "⚠️ Sessione scaduta. Premi di nuovo 🏆 Miglior prezzo."
                )
                return
            await self._cb_best_search(query, user.id, fuel, mode)

        elif data == CB_REMOVE_OK:
            await self._storage.update_station(user.id, None)
            await query.edit_message_text("🗑 Stazione preferita rimossa.")

        elif data == CB_REMOVE_NO:
            await query.edit_message_text("Operazione annullata.")

    async def _cb_set_station(self, query, user_id: int, station_id: str) -> None:
        try:
            station_data = await self._get_client().fetch_station(station_id)
        except OsservaprezziError as err:
            await query.edit_message_text(f"Errore: {err}")
            return

        await self._storage.update_station(user_id, station_id)
        station_name = station_data.get("nomeImpianto") or station_data.get("name") or station_id
        await query.edit_message_text(
            f"✅ Stazione preferita impostata:\n"
            f"<b>{escape(str(station_name))}</b> — ID <code>{station_id}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Vedi prezzi", callback_data=f"{CB_SHOW_PRICES}{station_id}"),
            ]]),
        )

    async def _cb_show_prices(self, query, station_id: str) -> None:
        try:
            text = await self._build_station_message(station_id)
        except OsservaprezziError as err:
            await query.edit_message_text(f"Errore: {err}")
            return

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⭐ Imposta preferita", callback_data=f"{CB_SET_STATION}{station_id}"
                ),
            ]]),
        )

    async def _cb_best_search(self, query, user_id: int, fuel: str, mode: str) -> None:
        settings = self._storage.get(user_id)
        if settings.location_lat is None or settings.location_lon is None:
            await query.edit_message_text(
                "📍 Condividi prima la posizione con il pulsante dedicato."
            )
            return

        await query.edit_message_text(
            f"🔍 Cerco il miglior prezzo per <b>{escape(fuel)}</b> ({mode})…",
            parse_mode=ParseMode.HTML,
        )

        try:
            zone_results = await self._get_client().search_zone(
                settings.location_lat, settings.location_lon, radius_km=5.0,
            )
        except OsservaprezziError as err:
            await query.edit_message_text(f"Errore API: {err}")
            return

        text = format_zone_cheapest(zone_results, fuel_query=fuel, service_mode=mode)
        best_id = _extract_best_station_id(zone_results, fuel, mode)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "⭐ Imposta come preferita", callback_data=f"{CB_SET_STATION}{best_id}"
            )
        ]]) if best_id else None
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # ── Message handlers ──────────────────────────────────────────────────────

    async def handle_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not update.message or not update.effective_user or not update.message.location:
            return

        lat = update.message.location.latitude
        lon = update.message.location.longitude
        await self._storage.update_location(update.effective_user.id, lat, lon)

        nearby = self._get_station_cache().nearest(lat, lon, limit=5, max_radius_km=5)
        text = "📍 <b>Posizione salvata!</b>\n\n" + format_nearest_stations(nearby)
        keyboard = self._stations_list_keyboard(nearby) if nearby else None
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def handle_text_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message or not update.effective_user:
            return

        text = (update.message.text or "").strip()
        if not text:
            return

        awaiting = context.user_data.get("awaiting_input")

        if text in (BTN_CANCEL, "Annulla"):
            context.user_data.pop("awaiting_input", None)
            await self._reply_with_keyboard(update, "✖️ Operazione annullata.")
            return

        # I pulsanti noti hanno sempre la precedenza sullo stato awaiting.
        # Se l'utente preme un pulsante mentre è in attesa di input, lo stato
        # viene azzerato e il pulsante viene gestito normalmente.
        _KNOWN_BUTTONS = {
            BTN_PRICES, "Prezzi stazione",
            BTN_SET_STATION, "Imposta stazione",
            BTN_SEARCH, "Cerca stazioni",
            BTN_NEARBY, "Stazioni vicine",
            BTN_BEST, "Miglior prezzo",
            BTN_NOTIFY, "Imposta notifica",
            BTN_DISABLE_NOTIFY, "Disattiva notifiche",
            BTN_MY_SETTINGS, "Le mie impostazioni",
            BTN_REMOVE_STATION, "Rimuovi stazione",
            BTN_HELP, "Aiuto",
        }
        if text in _KNOWN_BUTTONS:
            context.user_data.pop("awaiting_input", None)
            awaiting = None

        if awaiting:
            handled = await self._handle_awaiting_input(update, context, awaiting, text)
            if handled:
                return

        if text in (BTN_PRICES, "Prezzi stazione"):
            await self.cmd_prices(update, context)

        elif text in (BTN_SET_STATION, "Imposta stazione"):
            context.user_data["awaiting_input"] = "station_id"
            await self._reply_with_keyboard(
                update,
                "🔢 Inserisci l'ID stazione numerico (es. 12345).\n"
                "Digita ✖️ Annulla per uscire.",
            )

        elif text in (BTN_SEARCH, "Cerca stazioni"):
            context.user_data["awaiting_input"] = "search_query"
            await self._reply_with_keyboard(
                update,
                "🔍 Scrivi un testo da cercare (nome, comune, indirizzo, brand).\n"
                "Digita ✖️ Annulla per uscire.",
            )

        elif text in (BTN_NEARBY, "Stazioni vicine"):
            await self.cmd_nearby(update, context)

        elif text in (BTN_BEST, "Miglior prezzo"):
            user = update.effective_user
            if user:
                settings = self._storage.get(user.id)
                if settings.location_lat is None or settings.location_lon is None:
                    await self._reply_with_keyboard(
                        update,
                        "📍 Per usare Miglior prezzo, condividi prima la posizione "
                        "con il pulsante 📍 Condividi posizione.",
                    )
                    return
            await update.message.reply_text(
                "🏆 <b>Miglior prezzo</b>\n\nSeleziona il tipo di carburante:",
                parse_mode=ParseMode.HTML,
                reply_markup=self._fuel_keyboard(),
            )

        elif text in (BTN_NOTIFY, "Imposta notifica"):
            context.user_data["awaiting_input"] = "notify_time"
            await self._reply_with_keyboard(
                update,
                "🔔 Inserisci l'orario della notifica in formato HH:MM (es. 08:30).\n"
                "Digita ✖️ Annulla per uscire.",
            )

        elif text in (BTN_DISABLE_NOTIFY, "Disattiva notifiche"):
            await self.cmd_disable_notify(update, context)

        elif text in (BTN_MY_SETTINGS, "Le mie impostazioni"):
            await self.cmd_my_settings(update, context)

        elif text in (BTN_REMOVE_STATION, "Rimuovi stazione"):
            user = update.effective_user
            if user:
                settings = self._storage.get(user.id)
                if not settings.station_id:
                    await self._reply_with_keyboard(update, "Nessuna stazione impostata.")
                    return
            await update.message.reply_text(
                "🗑 Sei sicuro di voler rimuovere la stazione preferita?",
                reply_markup=self._remove_confirm_keyboard(),
            )

        elif text in (BTN_HELP, "Aiuto"):
            await self.cmd_start(update, context)

        elif text.isdigit():
            # Numero digitato direttamente → imposta come stazione
            context.user_data["awaiting_input"] = "station_id"
            await self._handle_awaiting_input(update, context, "station_id", text)

        else:
            await self._reply_with_keyboard(
                update, "Non ho capito. Usa i pulsanti qui sotto oppure /help."
            )

    async def _handle_awaiting_input(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        awaiting: str,
        text: str,
    ) -> bool:
        user = update.effective_user
        if not user or not update.message:
            return True

        if awaiting == "station_id":
            if not text.isdigit():
                await self._reply_with_keyboard(update, "ID non valido: deve essere numerico.")
                return True
            try:
                station_data = await self._get_client().fetch_station(text)
            except OsservaprezziError as err:
                await self._reply_with_keyboard(update, f"Errore: {err}")
                return True

            await self._storage.update_station(user.id, text)
            station_name = station_data.get("nomeImpianto") or station_data.get("name") or text
            context.user_data.pop("awaiting_input", None)
            await update.message.reply_text(
                f"✅ Stazione preferita impostata:\n"
                f"<b>{escape(str(station_name))}</b> — ID <code>{text}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=self._station_action_keyboard(text),
            )
            return True

        if awaiting == "search_query":
            context.user_data.pop("awaiting_input", None)
            await self._send_search_results(update, text.strip())
            return True

        if awaiting == "notify_time":
            notify_time = text.strip()
            if not self._is_valid_time(notify_time):
                await self._reply_with_keyboard(
                    update, "Orario non valido. Usa formato HH:MM (es. 08:30)."
                )
                return True

            settings = self._storage.get(user.id)
            if not settings.station_id:
                await self._reply_with_keyboard(update, "Imposta prima una stazione preferita.")
                return True

            await self._storage.update_notify_time(user.id, notify_time)
            context.user_data.pop("awaiting_input", None)
            await self._reply_with_keyboard(
                update,
                f"🔔 Notifica giornaliera attiva alle <b>{notify_time}</b> (ora Italia).",
                parse_mode=ParseMode.HTML,
            )
            return True

        context.user_data.pop("awaiting_input", None)
        return False

    # ── Background jobs ───────────────────────────────────────────────────────

    async def _notification_tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = datetime.now(tz=ITALY_TZ)
        now_label = now.strftime("%H:%M")

        for settings in self._storage.all_with_notifications():
            if settings.notify_time != now_label:
                continue

            already_sent_today = False
            if settings.last_notification_at:
                sent_at = parse_iso_datetime(settings.last_notification_at)
                if sent_at and sent_at.date() == now.date() and sent_at.strftime("%H:%M") == now_label:
                    already_sent_today = True
            if already_sent_today:
                continue

            try:
                text = await self._build_station_message(settings.station_id or "")
                await context.bot.send_message(
                    chat_id=settings.user_id,
                    text="🔔 <b>Riepilogo prezzi giornaliero</b>\n\n" + text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                await self._storage.mark_notification_sent(settings.user_id, now)
            except Exception as err:  # pragma: no cover
                _LOGGER.warning("Invio notifica fallito per user_id=%s: %s", settings.user_id, err)

    async def _cache_refresh_tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        try:
            refreshed = await self._get_station_cache().refresh(force=False)
            _LOGGER.debug("Refresh cache stazioni eseguito: %s", refreshed)
        except Exception as err:  # pragma: no cover
            _LOGGER.warning("Refresh cache stazioni fallito: %s", err)

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _send_search_results(self, update: Update, query: str) -> None:
        if not update.message:
            return
        if not query:
            await update.message.reply_text("Scrivi il testo da cercare.")
            return

        results = self._get_station_cache().search(query, limit=5)
        if not results:
            await self._reply_with_keyboard(update, "Nessuna stazione trovata.")
            return

        lines = [f"🔍 <b>Risultati per:</b> <i>{escape(query)}</i>", ""]
        for i, s in enumerate(results, 1):
            lines.append(
                f"{i}. <b>{escape(str(s.get('name') or 'Stazione'))}</b>\n"
                f"   ID <code>{s.get('id')}</code> · {escape(str(s.get('brand') or 'n/d'))}\n"
                f"   {escape(str(s.get('municipality') or ''))} "
                f"({escape(str(s.get('province') or ''))})"
            )

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=self._stations_list_keyboard(results),
        )

    def _resolve_station_id(self, user_id: int, args: list[str]) -> str | None:
        if args:
            return args[0].strip()
        return self._storage.get(user_id).station_id

    async def _resolve_location_and_radius(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> tuple[float | None, float | None, float | None]:
        default_radius = 5.0
        args = context.args or []

        if len(args) >= 2:
            try:
                lat = float(args[0])
                lon = float(args[1])
                radius = float(args[2]) if len(args) >= 3 else default_radius
                if radius <= 0 or radius > 100:
                    raise ValueError
                return lat, lon, radius
            except ValueError:
                return None, None, None

        user = update.effective_user
        if not user:
            return None, None, None

        settings = self._storage.get(user.id)
        lat, lon = settings.location_lat, settings.location_lon
        if lat is None or lon is None:
            return None, None, None

        if len(args) == 1:
            try:
                default_radius = float(args[0])
                if default_radius <= 0 or default_radius > 100:
                    raise ValueError
            except ValueError:
                return None, None, None

        return lat, lon, default_radius

    async def _build_station_message(self, station_id: str) -> str:
        station = await self._get_client().fetch_station(station_id)
        csv_station = self._get_station_cache().get_station(station_id)

        lines = [summarize_station(station, csv_station), "", format_fuels(station.get("fuels", []))]

        lat = (csv_station or {}).get("latitude")
        lon = (csv_station or {}).get("longitude")
        if lat is not None and lon is not None:
            maps_url = f"https://maps.google.com/?q={lat},{lon}"
            lines.append("")
            lines.append(f'🗺 <a href="{maps_url}">Apri su Google Maps</a>')

        return "\n".join(lines)

    @staticmethod
    def _is_valid_time(value: str) -> bool:
        try:
            datetime.strptime(value, "%H:%M")
            return True
        except ValueError:
            return False

    def _get_client(self) -> OsservaprezziClient:
        if self._client is None:
            raise RuntimeError("Client API non inizializzato")
        return self._client

    def _get_station_cache(self) -> StationCache:
        if self._station_cache is None:
            raise RuntimeError("Cache stazioni non inizializzata")
        return self._station_cache

    def run(self) -> None:
        self.app.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)


# ── Module-level helper ────────────────────────────────────────────────────────

def _extract_best_station_id(
    zone_results: list[dict],
    fuel_query: str,
    service_mode: str,
) -> str | None:
    """Restituisce l'ID della stazione col miglior prezzo per il pulsante inline."""
    normalized = fuel_query.lower().strip()
    is_self = service_mode.lower() == "self"
    best: tuple[float, str] | None = None
    for station in zone_results:
        for fuel in station.get("fuels", []):
            if normalized not in str(fuel.get("name", "")).lower():
                continue
            if bool(fuel.get("isSelf")) != is_self:
                continue
            price = fuel.get("price")
            sid = str(station.get("id", ""))
            if price is not None and sid and (best is None or float(price) < best[0]):
                best = (float(price), sid)
    return best[1] if best else None
