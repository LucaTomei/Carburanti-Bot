"""Bot Telegram completo per Osservaprezzi Carburanti."""

from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

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
BTN_SERVICES = "🔎 Servizi vicini"
BTN_SETTINGS_MENU = "⚙️ Impostazioni"
BTN_HELP = "❓ Aiuto"
BTN_ADMIN = "👑 Admin"
# Kept for backward-compatibility (inline Annulla buttons are preferred now)
BTN_NOTIFY = "🔔 Imposta notifica"
BTN_DISABLE_NOTIFY = "🔕 Disattiva notifiche"
BTN_MY_SETTINGS = "⚙️ Le mie impostazioni"
BTN_REMOVE_STATION = "🗑 Rimuovi stazione"
BTN_CANCEL = "✖️ Annulla"

# ── Callback data prefixes (max 64 byte totali per Telegram) ──────────────────
CB_SET_STATION = "st:set:"    # st:set:{station_id}
CB_SHOW_PRICES = "st:pr:"     # st:pr:{station_id}
CB_BEST_FUEL = "bf:"          # bf:{fuel_name}
CB_BEST_MODE = "bm:"          # bm:{self|servito}
CB_SERVICE = "svc:"           # svc:{service_id}
CB_REMOVE_OK = "rm:ok"
CB_REMOVE_NO = "rm:no"
CB_GUIDE_SEARCH = "guide:search"
CB_GUIDE_NEARBY = "guide:nearby"
CB_CANCEL_INPUT = "cancel:input"
CB_ADMIN_LIST = "adm:list:"    # adm:list:{page}
CB_ADMIN_BLOCK = "adm:blk:"    # adm:blk:{user_id}
CB_ADMIN_UNBLOCK = "adm:ublk:" # adm:ublk:{user_id}
CB_SETTINGS_MENU = "cfg:menu"
CB_SETTINGS_NOTIFY = "cfg:notify"
CB_SETTINGS_DISABLE = "cfg:disable"
CB_SETTINGS_REMOVE = "cfg:remove"

_ADMIN_PAGE_SIZE = 5

# ── Carburanti disponibili per la tastiera inline ─────────────────────────────
_FUEL_OPTIONS: list[tuple[str, str]] = [
    ("⛽ Benzina", "benzina"),
    ("🛢 Gasolio", "gasolio"),
    ("🟢 GPL", "gpl"),
    ("⚡ Metano", "metano"),
    ("🔋 Elettrico", "elettrico"),
    ("🚗 AdBlue", "adblue"),
]

# ── Servizi disponibili per la tastiera inline ────────────────────────────────
_SERVICE_OPTIONS: list[tuple[str, str]] = [
    ("🚿 Autolavaggio", "10"),
    ("⚡ Ricarica elettrica", "11"),
    ("🏧 Bancomat", "6"),
    ("☕ Food & Beverage", "1"),
    ("🔧 Officina", "2"),
    ("♿ Accesso disabili", "7"),
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
    def __init__(self, token: str, data_dir: Path, admin_ids: set[int] | None = None) -> None:
        self._token = token
        self._data_dir = data_dir
        self._admin_ids: set[int] = admin_ids or set()
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
        self.app.add_handler(CommandHandler("admin", self.cmd_admin))
        self.app.add_handler(CommandHandler("broadcast", self.cmd_broadcast))
        self.app.add_handler(CommandHandler("msg", self.cmd_msg))
        self.app.add_handler(CommandHandler("block", self.cmd_block))
        self.app.add_handler(CommandHandler("unblock", self.cmd_unblock))
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

    def _main_keyboard(self, is_admin: bool = False) -> ReplyKeyboardMarkup:
        rows: list = [
            [BTN_PRICES, BTN_SET_STATION],
            [BTN_SEARCH, BTN_NEARBY],
            [BTN_BEST, BTN_SERVICES],
            [KeyboardButton("📍 Condividi posizione", request_location=True)],
            [BTN_SETTINGS_MENU, BTN_HELP],
        ]
        if is_admin:
            rows.append([BTN_ADMIN])
        return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)

    def _settings_inline_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        """Tastiera inline del sottomenu Impostazioni."""
        settings = self._storage.get(user_id)
        rows: list[list[InlineKeyboardButton]] = []
        if settings.station_id:
            rows.append([
                InlineKeyboardButton("📋 Vedi prezzi", callback_data=f"{CB_SHOW_PRICES}{settings.station_id}"),
                InlineKeyboardButton("🗑 Rimuovi stazione", callback_data=CB_SETTINGS_REMOVE),
            ])
        if settings.notify_time:
            rows.append([
                InlineKeyboardButton("🔕 Disattiva notifiche", callback_data=CB_SETTINGS_DISABLE),
            ])
        else:
            rows.append([
                InlineKeyboardButton("🔔 Imposta notifica", callback_data=CB_SETTINGS_NOTIFY),
            ])
        return InlineKeyboardMarkup(rows)

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

    @staticmethod
    def _services_keyboard() -> InlineKeyboardMarkup:
        """Selettore tipo di servizio da cercare."""
        rows = []
        for i in range(0, len(_SERVICE_OPTIONS), 2):
            row = [
                InlineKeyboardButton(label, callback_data=f"{CB_SERVICE}{sid}")
                for label, sid in _SERVICE_OPTIONS[i:i + 2]
            ]
            rows.append(row)
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _cancel_keyboard() -> InlineKeyboardMarkup:
        """Pulsante inline Annulla per i prompt di input testuale."""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Annulla", callback_data=CB_CANCEL_INPUT),
        ]])

    async def _ask_with_cancel(self, update: Update, text: str) -> None:
        """Invia un prompt di input con pulsante inline ❌ Annulla."""
        if not update.message:
            return
        await update.message.reply_text(text, reply_markup=self._cancel_keyboard())

    async def _reply_with_keyboard(
        self, update: Update, text: str, parse_mode: str | None = None
    ) -> None:
        if not update.message:
            return
        user = update.effective_user
        is_admin = user is not None and self._is_admin(user.id)
        await update.message.reply_text(
            text=text,
            parse_mode=parse_mode,
            reply_markup=self._main_keyboard(is_admin=is_admin),
            disable_web_page_preview=True,
        )

    # ── Commands ──────────────────────────────────────────────────────────────

    def _is_admin(self, user_id: int) -> bool:
        return user_id in self._admin_ids

    async def _check_not_blocked(self, update: Update) -> bool:
        """Ritorna False e risponde se l'utente è bloccato."""
        user = update.effective_user
        if not user:
            return False
        if self._storage.is_blocked(user.id):
            if update.message:
                await update.message.reply_text("Non sei autorizzato a usare questo bot.")
            elif update.callback_query:
                await update.callback_query.answer("Non autorizzato.", show_alert=True)
            return False
        return True

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return
        if not await self._check_not_blocked(update):
            return

        is_new = await self._storage.upsert_user_info(
            user.id, user.username, user.full_name
        )
        if is_new:
            await self._notify_admin_new_user(context, user)

        await update.message.reply_text(
            _START_MESSAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=self._main_keyboard(is_admin=self._is_admin(user.id)),
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
                "⛽ Non hai ancora una stazione preferita.\n\n"
                "Cercane una per nome o comune, oppure trova quelle vicine a te.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔍 Cerca per nome", callback_data=CB_GUIDE_SEARCH)],
                    [InlineKeyboardButton("📍 Stazioni vicine a me", callback_data=CB_GUIDE_NEARBY)],
                ]),
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
                "📍 Non ho ancora la tua posizione.\n\n"
                "Usa il pulsante qui sotto per condividerla, oppure scrivi:\n"
                "<code>/vicino &lt;lat&gt; &lt;lon&gt;</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📍 Condividi posizione", request_location=True)]],
                    resize_keyboard=True,
                    one_time_keyboard=True,
                ),
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

        elif data == CB_CANCEL_INPUT:
            context.user_data.pop("awaiting_input", None)
            context.user_data.pop("best_fuel", None)
            await query.edit_message_text("✖️ Operazione annullata.")

        elif data == CB_GUIDE_SEARCH:
            context.user_data["awaiting_input"] = "search_query"
            await query.edit_message_text(
                "🔍 Scrivi il nome, comune o brand da cercare.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Annulla", callback_data=CB_CANCEL_INPUT),
                ]]),
            )

        elif data.startswith(CB_ADMIN_LIST):
            if not self._is_admin(user.id):
                await query.answer("Non autorizzato.", show_alert=True)
                return
            page = int(data[len(CB_ADMIN_LIST):] or "0")
            await self._cb_admin_list(query, page)

        elif data.startswith(CB_ADMIN_BLOCK):
            if not self._is_admin(user.id):
                await query.answer("Non autorizzato.", show_alert=True)
                return
            target_id = int(data[len(CB_ADMIN_BLOCK):])
            if self._is_admin(target_id):
                await query.answer("Non puoi bloccare un admin.", show_alert=True)
                return
            await self._storage.set_blocked(target_id, True)
            await query.answer(f"Utente {target_id} bloccato.")
            page = context.user_data.get("admin_list_page", 0)
            await self._cb_admin_list(query, page)

        elif data.startswith(CB_ADMIN_UNBLOCK):
            if not self._is_admin(user.id):
                await query.answer("Non autorizzato.", show_alert=True)
                return
            target_id = int(data[len(CB_ADMIN_UNBLOCK):])
            await self._storage.set_blocked(target_id, False)
            await query.answer(f"Utente {target_id} sbloccato.")
            page = context.user_data.get("admin_list_page", 0)
            await self._cb_admin_list(query, page)

        elif data.startswith(CB_SERVICE):
            service_id = data[len(CB_SERVICE):]
            service_label = next(
                (lbl for lbl, sid in _SERVICE_OPTIONS if sid == service_id), "Servizio"
            )
            settings = self._storage.get(user.id)
            if settings.location_lat is None or settings.location_lon is None:
                await query.edit_message_text(
                    "📍 Condividi prima la posizione con il pulsante dedicato."
                )
                return
            await query.edit_message_text(
                f"🔍 Cerco <b>{escape(service_label)}</b> nelle vicinanze…",
                parse_mode=ParseMode.HTML,
            )
            stations = await self._find_stations_with_service(
                settings.location_lat, settings.location_lon, service_id
            )
            text = _format_service_results(stations, service_label)
            keyboard = self._stations_list_keyboard(stations) if stations else None
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

        elif data == CB_GUIDE_NEARBY:
            settings = self._storage.get(user.id)
            if settings.location_lat is None or settings.location_lon is None:
                await query.edit_message_text(
                    "📍 Non ho ancora la tua posizione.\n"
                    "Usa il pulsante 📍 Condividi posizione nella tastiera."
                )
                return
            nearby = self._get_station_cache().nearest(
                settings.location_lat, settings.location_lon, limit=5, max_radius_km=5
            )
            text = "📍 <b>Posizione salvata!</b>\n\n" + format_nearest_stations(nearby)
            keyboard = self._stations_list_keyboard(nearby) if nearby else None
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

        elif data == CB_SETTINGS_MENU:
            settings = self._storage.get(user.id)
            station_line = (
                f"<code>{settings.station_id}</code>" if settings.station_id else "non impostata"
            )
            if settings.location_lat is not None and settings.location_lon is not None:
                location_line = f"{settings.location_lat:.5f}, {settings.location_lon:.5f}"
            else:
                location_line = "non condivisa"
            await query.edit_message_text(
                "⚙️ <b>Impostazioni</b>\n\n"
                f"⛽ Stazione: {station_line}\n"
                f"📍 Posizione: {location_line}\n"
                f"🔔 Notifica: {settings.notify_time or 'disattivata'}",
                parse_mode=ParseMode.HTML,
                reply_markup=self._settings_inline_keyboard(user.id),
            )

        elif data == CB_SETTINGS_NOTIFY:
            context.user_data["awaiting_input"] = "notify_time"
            await query.edit_message_text(
                "🔔 Inserisci l'orario della notifica in formato HH:MM (es. 08:30).",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Annulla", callback_data=CB_CANCEL_INPUT),
                ]]),
            )

        elif data == CB_SETTINGS_DISABLE:
            await self._storage.update_notify_time(user.id, None)
            await query.edit_message_text(
                "🔕 Notifiche giornaliere disattivate.",
                reply_markup=self._settings_inline_keyboard(user.id),
            )

        elif data == CB_SETTINGS_REMOVE:
            settings = self._storage.get(user.id)
            if not settings.station_id:
                await query.edit_message_text("Nessuna stazione impostata.")
                return
            await query.edit_message_text(
                "🗑 Sei sicuro di voler rimuovere la stazione preferita?",
                reply_markup=self._remove_confirm_keyboard(),
            )

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
        if not await self._check_not_blocked(update):
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
        if not await self._check_not_blocked(update):
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
            BTN_SERVICES, "Servizi vicini",
            BTN_SETTINGS_MENU, "Impostazioni",
            BTN_ADMIN, "Admin",
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
            await self._ask_with_cancel(update, "🔢 Inserisci l'ID stazione numerico (es. 12345).")

        elif text in (BTN_SEARCH, "Cerca stazioni"):
            context.user_data["awaiting_input"] = "search_query"
            await self._ask_with_cancel(
                update, "🔍 Scrivi un testo da cercare (nome, comune, indirizzo, brand)."
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
            await self._ask_with_cancel(
                update, "🔔 Inserisci l'orario della notifica in formato HH:MM (es. 08:30)."
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

        elif text in (BTN_SERVICES, "Servizi vicini"):
            user = update.effective_user
            if user:
                settings = self._storage.get(user.id)
                if settings.location_lat is None or settings.location_lon is None:
                    await self._reply_with_keyboard(
                        update,
                        "📍 Per cercare servizi vicini, condividi prima la posizione "
                        "con il pulsante 📍 Condividi posizione.",
                    )
                    return
            await update.message.reply_text(
                "🔎 <b>Cerca servizio</b>\n\nQuale servizio stai cercando?",
                parse_mode=ParseMode.HTML,
                reply_markup=self._services_keyboard(),
            )

        elif text in (BTN_HELP, "Aiuto"):
            await self.cmd_start(update, context)

        elif text in (BTN_SETTINGS_MENU, "Impostazioni"):
            user = update.effective_user
            if not user:
                return
            settings = self._storage.get(user.id)
            station_line = (
                f"<code>{settings.station_id}</code>" if settings.station_id else "non impostata"
            )
            if settings.location_lat is not None and settings.location_lon is not None:
                location_line = f"{settings.location_lat:.5f}, {settings.location_lon:.5f}"
            else:
                location_line = "non condivisa"
            await update.message.reply_text(
                "⚙️ <b>Impostazioni</b>\n\n"
                f"⛽ Stazione: {station_line}\n"
                f"📍 Posizione: {location_line}\n"
                f"🔔 Notifica: {settings.notify_time or 'disattivata'}",
                parse_mode=ParseMode.HTML,
                reply_markup=self._settings_inline_keyboard(user.id),
            )

        elif text == BTN_ADMIN:
            await self.cmd_admin(update, context)

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

    async def _find_stations_with_service(
        self, lat: float, lon: float, service_id: str, radius_km: float = 5.0
    ) -> list[dict]:
        """
        Cerca stazioni nel raggio indicato che offrono il servizio richiesto.
        Prima tenta con la zone search (un'unica chiamata API veloce).
        Se la zone search non restituisce il campo services, effettua chiamate
        individuali sulle 8 stazioni più vicine come fallback.
        """
        zone_results: list[dict] = []
        try:
            zone_results = await self._get_client().search_zone(lat, lon, radius_km=radius_km)
        except OsservaprezziError:
            pass

        matched = [s for s in zone_results if _station_has_service(s, service_id)]

        # Fallback: la zone search non include services → chiamate individuali
        if not matched and zone_results and not any(s.get("services") for s in zone_results):
            nearest = self._get_station_cache().nearest(lat, lon, limit=8, max_radius_km=radius_km)
            for cache_s in nearest:
                sid = str(cache_s.get("id", ""))
                if not sid:
                    continue
                try:
                    data = await self._get_client().fetch_station(sid)
                    if _station_has_service(data, service_id):
                        data["distance_km"] = cache_s.get("distance_km")
                        matched.append(data)
                except OsservaprezziError:
                    continue

        return matched[:5]

    async def _cb_admin_list(self, query, page: int) -> None:
        """Mostra la lista utenti paginata con azioni blocca/sblocca."""
        all_users = self._storage.all_users()
        total = len(all_users)
        start = page * _ADMIN_PAGE_SIZE
        chunk = all_users[start:start + _ADMIN_PAGE_SIZE]

        if not chunk:
            await query.edit_message_text("Nessun utente.")
            return

        lines = [f"👥 <b>Utenti</b> ({total} totali) — pagina {page + 1}\n"]
        rows: list[list[InlineKeyboardButton]] = []

        for u in chunk:
            uname = f"@{u.username}" if u.username else str(u.user_id)
            station = f"⛽ {u.station_id}" if u.station_id else "nessuna stazione"
            notify = f"🔔 {u.notify_time}" if u.notify_time else ""
            is_admin_user = u.user_id in self._admin_ids
            role_label = " 👑" if is_admin_user else (" 🚫" if u.blocked else "")
            lines.append(
                f"• <b>{escape(u.display_name)}</b>{role_label} "
                f"<code>{u.user_id}</code>\n"
                f"  {escape(uname)} · {station} {notify}"
            )
            if not is_admin_user:
                if u.blocked:
                    rows.append([InlineKeyboardButton(
                        f"✅ Sblocca {u.display_name[:15]}",
                        callback_data=f"{CB_ADMIN_UNBLOCK}{u.user_id}",
                    )])
                else:
                    rows.append([InlineKeyboardButton(
                        f"🚫 Blocca {u.display_name[:15]}",
                        callback_data=f"{CB_ADMIN_BLOCK}{u.user_id}",
                    )])

        # Navigazione pagine
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Prec", callback_data=f"{CB_ADMIN_LIST}{page - 1}"))
        if start + _ADMIN_PAGE_SIZE < total:
            nav.append(InlineKeyboardButton("Succ ▶️", callback_data=f"{CB_ADMIN_LIST}{page + 1}"))
        if nav:
            rows.append(nav)

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(rows),
        )

    # ── Admin commands ────────────────────────────────────────────────────────

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        user = update.effective_user
        if not user or not update.message:
            return
        if not self._is_admin(user.id):
            await update.message.reply_text("Non autorizzato.")
            return

        all_users = self._storage.all_users()
        total = len(all_users)
        blocked = sum(1 for u in all_users if u.blocked)
        with_notify = sum(1 for u in all_users if u.notify_time)

        text = (
            "👑 <b>Pannello Admin</b>\n\n"
            f"👥 Utenti totali: <b>{total}</b>\n"
            f"🚫 Bloccati: <b>{blocked}</b>\n"
            f"🔔 Con notifica attiva: <b>{with_notify}</b>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Lista utenti", callback_data=f"{CB_ADMIN_LIST}0")],
        ])
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def cmd_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return
        if not self._is_admin(user.id):
            return
        if not context.args:
            await update.message.reply_text("Uso: /broadcast <messaggio>")
            return

        message = " ".join(context.args)
        all_users = self._storage.all_users()
        sent = 0
        failed = 0
        for u in all_users:
            if u.blocked or u.user_id == user.id:
                continue
            try:
                await context.bot.send_message(
                    chat_id=u.user_id,
                    text=f"📢 <b>Messaggio dal gestore del bot</b>\n\n{escape(message)}",
                    parse_mode=ParseMode.HTML,
                )
                sent += 1
            except Exception:
                failed += 1

        await update.message.reply_text(
            f"📢 Broadcast completato.\n✅ Inviati: {sent} · ❌ Falliti: {failed}"
        )

    async def cmd_msg(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return
        if not self._is_admin(user.id):
            return
        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Uso: /msg <user_id> <messaggio>")
            return

        target_id_str = context.args[0]
        if not target_id_str.isdigit():
            await update.message.reply_text("user_id non valido.")
            return

        target_id = int(target_id_str)
        message = " ".join(context.args[1:])
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"✉️ <b>Messaggio dal gestore del bot</b>\n\n{escape(message)}",
                parse_mode=ParseMode.HTML,
            )
            await update.message.reply_text(f"✅ Messaggio inviato a {target_id}.")
        except Exception as err:
            await update.message.reply_text(f"❌ Errore: {err}")

    async def cmd_block(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return
        if not self._is_admin(user.id):
            return
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("Uso: /block <user_id>")
            return
        target_id = int(context.args[0])
        if self._is_admin(target_id):
            await update.message.reply_text("❌ Non puoi bloccare un admin.")
            return
        await self._storage.set_blocked(target_id, True)
        await update.message.reply_text(f"🚫 Utente {target_id} bloccato.")

    async def cmd_unblock(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return
        if not self._is_admin(user.id):
            return
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("Uso: /unblock <user_id>")
            return
        target_id = int(context.args[0])
        await self._storage.set_blocked(target_id, False)
        await update.message.reply_text(f"✅ Utente {target_id} sbloccato.")

    async def _notify_admin_new_user(
        self, context: ContextTypes.DEFAULT_TYPE, user: Any
    ) -> None:
        if not self._admin_ids:
            return
        username_line = f"@{user.username}" if user.username else "—"
        text = (
            "👤 <b>Nuovo utente!</b>\n\n"
            f"Nome: {escape(user.full_name or '—')}\n"
            f"Username: {username_line}\n"
            f"ID: <code>{user.id}</code>"
        )
        for admin_id in self._admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id, text=text, parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    def run(self) -> None:
        self.app.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)


# ── Module-level helper ────────────────────────────────────────────────────────

def _station_has_service(station: dict, service_id: str) -> bool:
    """Controlla se una stazione ha un determinato servizio (per ID)."""
    for svc in station.get("services", []):
        if isinstance(svc, dict):
            if str(svc.get("id", "")) == service_id:
                return True
        elif str(svc) == service_id:
            return True
    return False


def _format_service_results(stations: list[dict], service_label: str) -> str:
    """Formatta i risultati di una ricerca per servizio."""
    if not stations:
        return (
            f"Nessuna stazione con <b>{escape(service_label)}</b> "
            f"trovata nel raggio di 5 km."
        )
    lines = [f"🔎 <b>Stazioni con {escape(service_label)}</b>", ""]
    for i, s in enumerate(stations, 1):
        name = str(s.get("nomeImpianto") or s.get("name") or "Stazione")
        brand = str(s.get("brand") or "n/d")
        addr = str(s.get("address") or "")
        distance = s.get("distance") or s.get("distance_km")
        dist_text = f"{float(distance):.2f} km" if distance is not None else "n/d"
        lines.append(f"{i}. <b>{escape(name)}</b> · {escape(brand)}")
        if addr:
            lines.append(f"   📌 {escape(addr)}")
        lines.append(f"   📏 {dist_text}")
    return "\n".join(lines)


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
