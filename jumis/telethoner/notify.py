# jumis/telethoner/notify.py
"""Уведомления владельцу о состоянии userbot.

Два повода писать в чат:
  * состояние сессии сменилось на «нужен вход» (или сломалось) — одно сообщение;
  * пока вход не восстановлен — напоминание раз в сутки (по просьбе владельца).

Состояния `WAITING_*` и `OK` молчат: в первом случае человек и так в диалоге
с ботом, во втором всё хорошо. Сетевые обрывы (`OFFLINE`) не будятся толчком —
они лечатся сами, иначе каждое мигание интернета давало бы сообщение.
"""

import asyncio
import html
import time

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from logs.set_logger import logger
from telethoner.auth import AuthState

# Напоминание раз в сутки, пока userbot не подключён.
REMIND_EVERY_SEC = 24 * 3600
# Защита от дребезга: одно и то же состояние не чаще раза в час.
SAME_STATE_GUARD_SEC = 3600

# Состояния, о которых сообщаем сразу (переход в них).
PUSH_STATES = frozenset({
    AuthState.NO_SESSION,
    AuthState.REVOKED,
    AuthState.NOT_CONFIGURED,
    AuthState.LOCKED,
    AuthState.ERROR,
})

# Состояния, которые не стоят напоминаний: вход в процессе или всё хорошо.
QUIET_STATES = frozenset({
    AuthState.OK,
    AuthState.WAITING_QR,
    AuthState.WAITING_CODE,
    AuthState.WAITING_PASSWORD,
})

BUTTON = {"ru": "🔐 Подключить", "en": "🔐 Connect"}

BODIES = {
    AuthState.NO_SESSION: {
        "ru": (
            "🔌 <b>Юзербот не подключён</b>\n\n"
            "Сессии Telegram ещё нет: бот не видит твои переписки и не может отвечать от твоего имени.\n"
            "Один вход — и он заработает: нажми кнопку, подтверди вход в приложении Telegram "
            "(или введи код, если QR не подходит)."
        ),
        "en": (
            "🔌 <b>Userbot is not connected</b>\n\n"
            "There is no Telegram session yet: the bot cannot see your chats or reply as you.\n"
            "One login and it starts working: tap the button and confirm in the Telegram app "
            "(or enter a code if QR does not fit)."
        ),
    },
    AuthState.REVOKED: {
        "ru": (
            "⚠️ <b>Telegram отозвал сессию</b>\n\n"
            "Файл сессии на месте, но сервер больше не признаёт ключ: сработало "
            "завершение неактивных сеансов, ты вышел из устройства или ключ был "
            "скомпрометирован.\n\n"
            "Нужно войти заново. История и память на месте — бот продолжит с того же места."
        ),
        "en": (
            "⚠️ <b>Telegram revoked the session</b>\n\n"
            "The session file exists, but the server no longer accepts the key: an "
            "inactive session was terminated, you logged this device out, or the key "
            "was compromised.\n\n"
            "Please log in again. History and memory are intact — the bot continues "
            "from the same point."
        ),
    },
    AuthState.NOT_CONFIGURED: {
        "ru": (
            "⚙️ <b>Нет ключей API Telegram</b>\n\n"
            "В <code>.env</code> не заданы <code>api_id</code> и/или <code>api_hash</code>. "
            "Их выдают на my.telegram.org (API development tools) для твоего аккаунта.\n\n"
            "Впиши ключи в <code>.env</code> и запусти бота заново — вход я предложу сам."
        ),
        "en": (
            "⚙️ <b>Telegram API keys are missing</b>\n\n"
            "<code>api_id</code> and/or <code>api_hash</code> are not set in <code>.env</code>. "
            "Get them at my.telegram.org (API development tools) for your account.\n\n"
            "Put the keys into <code>.env</code> and restart the bot — I will offer the login."
        ),
    },
    AuthState.LOCKED: {
        "ru": (
            "🗄 <b>Файл сессии занят</b>\n\n"
            "Файл <code>session_jumis.session</code> держит другой процесс — скорее всего, "
            "бот уже запущен второй раз или остался висеть старый.\n\n"
            "Оставь один экземпляр: лишний процесс снимает файл и мешает входу."
        ),
        "en": (
            "🗄 <b>Session file is locked</b>\n\n"
            "Another process holds <code>session_jumis.session</code> — most likely the bot "
            "was started twice or an old instance is still alive.\n\n"
            "Keep a single instance: the extra process holds the file and blocks login."
        ),
    },
    AuthState.OFFLINE: {
        "ru": (
            "🌐 <b>Нет связи с Telegram</b>\n\n"
            "Сессия в порядке, но соединение не устанавливается. Проверь интернет — "
            "я пробую снова сам, вход не нужен."
        ),
        "en": (
            "🌐 <b>No connection to Telegram</b>\n\n"
            "The session is fine, but the connection cannot be established. Check the "
            "internet — I keep retrying, no login needed."
        ),
    },
    AuthState.ERROR: {
        "ru": (
            "❗️ <b>Юзербот не работает</b>\n\n"
            "Подключение упало с неожиданной ошибкой. Если это повторяется — "
            "попробуй войти заново или посмотри логи (<code>/logs</code>)."
        ),
        "en": (
            "❗️ <b>Userbot is down</b>\n\n"
            "The connection failed with an unexpected error. If it keeps happening, "
            "try logging in again or check the logs (<code>/logs</code>)."
        ),
    },
}


class UserbotNotifier:
    """Пишет владельцу о состоянии userbot. Без LLM и без БД — только Telegram."""

    def __init__(self, bot: Bot, admin_id: int, lang: str = "en", state_provider=None):
        self.bot = bot
        self.admin_id = admin_id
        self.lang = "ru" if str(lang).lower().startswith("ru") else "en"
        self._state_provider = state_provider or (lambda: None)
        self._task = None
        self._last_sent: dict[str, float] = {}

    # ------------------------------------------------------------------ тексты

    def _body(self, state: AuthState):
        return BODIES.get(state, {}).get(self.lang)

    def _keyboard(self, state: AuthState):
        """Кнопка входа. Без api_id/api_hash жать нечего — не рисуем."""
        if state is AuthState.NOT_CONFIGURED:
            return None
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=BUTTON[self.lang], callback_data="login:start"),
        ]])

    def _text(self, state: AuthState, detail: str = "") -> str:
        body = self._body(state) or BODIES[AuthState.ERROR][self.lang]
        if state is AuthState.ERROR and detail:
            short = html.escape(str(detail))[:200]
            body = f"{body}\n\n<code>{short}</code>"
        return body

    # ------------------------------------------------------------------ отправка

    async def _send(self, state: AuthState, detail: str = "") -> bool:
        if not BODIES.get(state):
            return False
        try:
            await self.bot.send_message(
                self.admin_id,
                self._text(state, detail),
                reply_markup=self._keyboard(state),
            )
            self._last_sent[state.value] = time.monotonic()
            logger.info("Userbot alert sent: state=%s", state.value)
            return True
        except Exception as e:
            logger.warning("Userbot alert failed (%s): %s", state.value, e)
            return False

    async def on_state_change(self, state: AuthState, detail: str = "") -> None:
        """Вызывается машиной входа при каждой смене состояния."""
        if state not in PUSH_STATES:
            return
        last = self._last_sent.get(state.value)
        if last is not None and time.monotonic() - last < SAME_STATE_GUARD_SEC:
            return
        await self._send(state, detail)

    async def remind(self) -> bool:
        """Напоминание: раз в сутки, если вход так и не восстановлен."""
        state = self._state_provider()
        if state is None or state in QUIET_STATES:
            return False
        return await self._send(state)

    # ------------------------------------------------------------------ жизненный цикл

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._remind_loop(), name="userbot_reminder")

    async def _remind_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(REMIND_EVERY_SEC)
                try:
                    await self.remind()
                except Exception:
                    logger.exception("Userbot reminder failed")
        except asyncio.CancelledError:
            raise

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
