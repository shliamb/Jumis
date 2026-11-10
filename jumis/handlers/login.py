# jumis/handlers/login.py
"""Вход в Telegram для userbot прямо из бота: QR, код, пароль 2FA.

Машина входа живёт в `telethoner/auth.py` и ничего не знает про тексты; здесь —
только FSM, кнопки и ru/en. Код и пароль двухфакторной защиты удаляются из чата
сразу после прочтения: в облаке Telegram они не нужны.
"""

import asyncio
import contextlib
import html

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_ID
from handlers.common import rights_verification, typing
from logs.set_logger import logger
from telethoner.auth import AuthState

router = Router()

QR_WAIT_SEC = 18          # сколько ждём подтверждения одного QR-токена
CODE_ATTEMPTS = 3         # попыток ввода кода
PASSWORD_ATTEMPTS = 3     # попыток ввода пароля 2FA
CANCEL_WORDS = {"отмена", "отменить", "cancel", "stop", "стоп"}


class LoginStates(StatesGroup):
    phone = State()
    code = State()
    password = State()


# Активные QR-циклы: один на чат. Задача правит одно и то же сообщение,
# чтобы каждые 18 секунд не плодить новые.
_qr_tasks: dict[int, asyncio.Task] = {}


STATE_LABELS = {
    AuthState.OK: {"ru": "✅ работает", "en": "✅ connected"},
    AuthState.NO_SESSION: {"ru": "⚠️ сессии нет — вход не выполнен", "en": "⚠️ no session — not logged in"},
    AuthState.REVOKED: {"ru": "⚠️ Telegram отозвал сессию", "en": "⚠️ session revoked by Telegram"},
    AuthState.NOT_CONFIGURED: {"ru": "⚠️ нет api_id/api_hash в .env", "en": "⚠️ api_id/api_hash missing in .env"},
    AuthState.WAITING_QR: {"ru": "⏳ жду подтверждения QR", "en": "⏳ waiting for QR confirmation"},
    AuthState.WAITING_CODE: {"ru": "⏳ жду код из Telegram", "en": "⏳ waiting for the code"},
    AuthState.WAITING_PASSWORD: {"ru": "⏳ жду пароль 2FA", "en": "⏳ waiting for the 2FA password"},
    AuthState.OFFLINE: {"ru": "⏳ нет связи с Telegram", "en": "⏳ no connection to Telegram"},
    AuthState.LOCKED: {"ru": "⚠️ файл сессии занят другим процессом", "en": "⚠️ session file is locked"},
    AuthState.ERROR: {"ru": "⚠️ ошибка подключения", "en": "⚠️ connection error"},
}


def state_label(state: AuthState, lang: str = "ru") -> str:
    """Короткая подпись состояния — для `/admin` и мастера входа."""
    item = STATE_LABELS.get(state) or STATE_LABELS[AuthState.ERROR]
    return item["ru" if str(lang).lower().startswith("ru") else "en"]


TEXTS = {
    "menu": {
        "ru": (
            "🔐 <b>ВХОД В TELEGRAM (USERBOT)</b>\n"
            "─────────────────\n"
            "Статус: {status}\n\n"
            "Бот работает от твоего имени: читает личные переписки и отвечает по твоему "
            "поручению. Пока сессия не подключена, он этого не видит.\n\n"
            "<b>Два способа войти:</b>\n"
            "• <b>QR</b> — быстрее всего: нажать кнопку и подтвердить вход в приложении Telegram.\n"
            "• <b>Код</b> — Telegram пришлёт код сообщением (или SMS), введём его здесь.\n\n"
            "<i>Пароль двухфакторной защиты, если включён, спрошу отдельно и сразу удалю из чата.</i>"
        ),
        "en": (
            "🔐 <b>TELEGRAM LOGIN (USERBOT)</b>\n"
            "─────────────────\n"
            "Status: {status}\n\n"
            "The bot acts as you: reads your private chats and replies on your behalf. "
            "Until the session is connected it sees nothing.\n\n"
            "<b>Two ways in:</b>\n"
            "• <b>QR</b> — fastest: tap the button and confirm in the Telegram app.\n"
            "• <b>Code</b> — Telegram sends a code message (or SMS); we enter it here.\n\n"
            "<i>If 2FA is enabled, I will ask for the password and delete it from the chat "
            "right away.</i>"
        ),
    },
    "qr_wait": {
        "ru": (
            "📷 <b>Вход по QR</b>\n\n"
            "Нажми кнопку ниже — откроется Telegram, подтверди вход.\n"
            "Ссылка живёт ~{sec} секунд; я сам обновлю её и перепишу это сообщение.\n\n"
            "Не открывается? Жми «🔢 Войти по коду» — там придёт код сообщением."
        ),
        "en": (
            "📷 <b>QR login</b>\n\n"
            "Tap the button below — Telegram opens, confirm the login.\n"
            "The link lives ~{sec} seconds; I refresh it and rewrite this message myself.\n\n"
            "Does not open? Tap «🔢 Enter a code» — you will get a code message instead."
        ),
    },
    "phone_prompt": {
        "ru": (
            "📱 <b>Шаг 1 из 2 — номер телефона</b>\n\n"
            "Пришли номер аккаунта Telegram, который будет работать как юзербот. "
            "Можно так: <code>+7 900 000-00-00</code>, <code>89000000000</code> или в "
            "международном формате.\n\n"
            "<i>Номер нужен только для входа, в память и базу он не сохраняется.</i>"
        ),
        "en": (
            "📱 <b>Step 1 of 2 — phone number</b>\n\n"
            "Send the phone number of the Telegram account that will act as the userbot. "
            "Any format works: <code>+7 900 000-00-00</code>, <code>89000000000</code>, "
            "or international.\n\n"
            "<i>The number is only needed for login; it is not stored in memory or the DB.</i>"
        ),
    },
    "code_prompt": {
        "ru": (
            "✉️ <b>Шаг 2 из 2 — код</b>\n\n{where}\n\n"
            "Пришли код сюда (пробелы не мешают). Сообщение с кодом я сразу удалю."
        ),
        "en": (
            "✉️ <b>Step 2 of 2 — the code</b>\n\n{where}\n\n"
            "Send the code here (spaces are fine). I will delete the message with the code."
        ),
    },
    "password_prompt": {
        "ru": (
            "🔑 <b>Пароль двухфакторной защиты</b>\n\n"
            "Аккаунт защищён паролем 2FA. Пришли его сюда — сообщение я сразу удалю, "
            "пароль никуда не сохраняется.\n\n"
            "<i>Передумал — /login_cancel.</i>"
        ),
        "en": (
            "🔑 <b>Two-factor password</b>\n\n"
            "The account is protected by a 2FA password. Send it here — I delete the "
            "message right away and never store it.\n\n"
            "<i>Changed your mind — /login_cancel.</i>"
        ),
    },
    "connected": {
        "ru": (
            "✅ <b>Userbot подключён</b>\n\n"
            "Аккаунт: <b>{name}</b> (id <code>{uid}</code>)\n"
            "Слушаю личные сообщения. Статус всегда виден в /admin."
        ),
        "en": (
            "✅ <b>Userbot connected</b>\n\n"
            "Account: <b>{name}</b> (id <code>{uid}</code>)\n"
            "Listening to private messages. Status is always visible in /admin."
        ),
    },
    "already_connected": {
        "ru": "✅ Сессия уже подключена: <b>{name}</b> (id <code>{uid}</code>). Ничего делать не нужно.",
        "en": "✅ The session is already connected: <b>{name}</b> (id <code>{uid}</code>). Nothing to do.",
    },
    "cancelled": {
        "ru": "🚫 Вход отменён. Когда будешь готов — /login.",
        "en": "🚫 Login cancelled. Whenever you are ready — /login.",
    },
    "too_many": {
        "ru": "🚫 Слишком много попыток. Начнём заново — /login.",
        "en": "🚫 Too many attempts. Let us start over — /login.",
    },
    "where_app": {
        "ru": "Код отправлен <b>сообщением в приложение Telegram</b> — открой Telegram на "
              "телефоне и найди сообщение от «Telegram».",
        "en": "The code was sent <b>as a message inside the Telegram app</b> — open Telegram "
              "on your phone and find the message from “Telegram”.",
    },
    "where_sms": {
        "ru": "Код отправлен <b>по SMS</b> на номер {phone}.",
        "en": "The code was sent <b>by SMS</b> to {phone}.",
    },
    "where_call": {
        "ru": "Telegram <b>позвонит</b> на номер {phone} и продиктует код.",
        "en": "Telegram will <b>call</b> {phone} and dictate the code.",
    },
    "where_flash": {
        "ru": "Будет <b>звонок</b>: код — последние цифры номера, с которого звонят.",
        "en": "Expect a <b>call</b>: the code is the last digits of the calling number.",
    },
    "where_missed": {
        "ru": "Будет <b>пропущенный звонок</b>: код — последние цифры номера звонящего.",
        "en": "Expect a <b>missed call</b>: the code is the last digits of the caller.",
    },
    "where_email": {
        "ru": "Код отправлен <b>на email</b>, привязанный к аккаунту.",
        "en": "The code was sent <b>to the account email</b>.",
    },
    "where_other": {
        "ru": "Код отправлен — проверь Telegram и SMS.",
        "en": "The code was sent — check Telegram and SMS.",
    },
    "invalid_phone": {
        "ru": "🚫 Не похоже на номер. Пришли в формате <code>+7 900 000-00-00</code>.",
        "en": "🚫 That does not look like a phone number. Use <code>+7 900 000-00-00</code>.",
    },
    "no_account": {
        "ru": "🚫 На этот номер нет аккаунта Telegram. Проверь номер и пришли снова.",
        "en": "🚫 There is no Telegram account for this number. Check it and send again.",
    },
    "banned": {
        "ru": "🚫 Telegram заблокировал этот номер — вход с него невозможен.",
        "en": "🚫 Telegram banned this number — it cannot be used to log in.",
    },
    "invalid_code": {
        "ru": "🚫 Код неверный. Попытка {n} из {total}.",
        "en": "🚫 Wrong code. Attempt {n} of {total}.",
    },
    "expired_code": {
        "ru": "⌛️ Код истёк. Жми «Прислать ещё» — вышлю новый.",
        "en": "⌛️ The code expired. Tap «Send again» and I will request a new one.",
    },
    "wrong_password": {
        "ru": "🚫 Пароль неверный. Попытка {n} из {total}.",
        "en": "🚫 Wrong password. Attempt {n} of {total}.",
    },
    "flood": {
        "ru": "⏳ Telegram просит подождать {sec} секунд. Не спешим — попробуем после.",
        "en": "⏳ Telegram asks to wait {sec} seconds. Let us not rush — retry after.",
    },
    "bad_code": {
        "ru": "🚫 Нужен код цифрами, без текста.",
        "en": "🚫 Send the code as digits, without text.",
    },
    "bad_password": {
        "ru": "🚫 Нужен непустой пароль.",
        "en": "🚫 The password cannot be empty.",
    },
    "failure": {
        "ru": "❗️ Не получилось: <code>{code}</code>. Попробуй ещё раз — /login.",
        "en": "❗️ Failed: <code>{code}</code>. Try again — /login.",
    },
    "not_configured": {
        "ru": "⚙️ В <code>.env</code> нет <code>api_id</code>/<code>api_hash</code> — без них вход "
              "невозможен. Добавь ключи с my.telegram.org и сделай /reset.",
        "en": "⚙️ <code>.env</code> has no <code>api_id</code>/<code>api_hash</code> — login is "
              "impossible without them. Add the keys from my.telegram.org and run /reset.",
    },
    "sms_requested": {
        "ru": "📨 Попросил код по SMS на {phone}. Если SMS не придёт — код будет в приложении Telegram.",
        "en": "📨 Requested an SMS code to {phone}. If it does not arrive, look inside the Telegram app.",
    },
    "btn_qr": {"ru": "📷 Войти по QR", "en": "📷 Log in via QR"},
    "btn_code": {"ru": "🔢 Войти по коду", "en": "🔢 Enter a code"},
    "btn_check": {"ru": "🔄 Проверить статус", "en": "🔄 Check status"},
    "btn_cancel": {"ru": "❌ Отмена", "en": "❌ Cancel"},
    "btn_open": {"ru": "▶️ Открыть Telegram", "en": "▶️ Open Telegram"},
    "btn_sms": {"ru": "📨 Прислать ещё", "en": "📨 Send again"},
    "btn_sms_alt": {"ru": "📨 Попросить SMS", "en": "📨 Request SMS"},
}


def _t(key: str, lang: str, **fmt) -> str:
    text = TEXTS[key]["ru" if lang == "ru" else "en"]
    return text.format(**fmt) if fmt else text


def _lang(obj) -> str:
    code = getattr(getattr(obj, "from_user", None), "language_code", None)
    return "ru" if str(code or "").lower().startswith("ru") else "en"


def _kb(*rows) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def _menu_kb(lang: str) -> types.InlineKeyboardMarkup:
    return _kb(
        [(_t("btn_qr", lang), "login:qr")],
        [(_t("btn_code", lang), "login:code")],
        [(_t("btn_check", lang), "login:status")],
    )


def _cancel_kb(lang: str) -> types.InlineKeyboardMarkup:
    return _kb([(_t("btn_cancel", lang), "login:cancel")])


async def _stop_qr(chat_id: int) -> None:
    """Останавливает QR-цикл, чтобы он не держал блокировку входа."""
    task = _qr_tasks.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _chat_id(callback: types.CallbackQuery) -> int:
    """Чат, где висит сообщение мастера (для inline-режима — личка владельца)."""
    if isinstance(callback.message, types.Message):
        return callback.message.chat.id
    return callback.from_user.id


async def _deny(callback: types.CallbackQuery) -> None:
    logger.error("Login callback denied: user=%s", callback.from_user.id)
    try:
        await callback.answer("🔐 Нет доступа / No access", show_alert=True)
    except Exception:
        pass


async def _delete_sensitive(message: types.Message) -> None:
    """Код и пароль не должны оставаться ни в чате, ни в облаке Telegram."""
    try:
        await message.delete()
    except Exception as e:
        logger.warning("Sensitive message delete failed: %s", e)


async def _edit(message: types.Message, text: str, keyboard=None) -> None:
    """Переписывает сообщение мастера; если нельзя — шлёт новое."""
    try:
        await message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        try:
            await message.answer(text, reply_markup=keyboard)
        except Exception as inner:
            logger.warning("Login message edit/answer failed: %s", inner)


async def _whoami(mytelethon) -> dict:
    try:
        return await mytelethon.whoami()
    except Exception as e:
        logger.warning("whoami failed: %s", e)
        return {}


async def _connected_text(mytelethon, lang: str) -> str:
    who = await _whoami(mytelethon)
    if not who:
        who = {"id": mytelethon.health().get("me_id") or 0, "name": "owner"}
    name = html.escape(str(who.get("name") or who.get("username") or "owner"))
    return _t("connected", lang, name=name, uid=who.get("id"))


async def _is_cancel_word(text: str | None) -> bool:
    return bool(text) and str(text).strip().lower() in CANCEL_WORDS


# --------------------------------------------------------------------- команды


@router.message(Command("login"))
async def cmd_login(message: types.Message, state: FSMContext, mytelethon):
    """Меню входа: QR или код."""
    await typing(message)
    lang = message.from_user.language_code
    if not await rights_verification(message.from_user.id, lang, message):
        return

    lang = _lang(message)
    await _stop_qr(message.chat.id)
    await state.clear()
    status = state_label(mytelethon.health()["state"], lang)
    await message.answer(_t("menu", lang, status=status), reply_markup=_menu_kb(lang))


@router.message(Command("login_cancel"))
async def cmd_login_cancel(message: types.Message, state: FSMContext, mytelethon):
    await typing(message)
    lang = message.from_user.language_code
    if not await rights_verification(message.from_user.id, lang, message):
        return
    await _cancel(message, state, mytelethon)


async def _cancel(target: types.Message, state: FSMContext, mytelethon) -> None:
    """Общий откат: снять состояние, остановить QR, перепроверить сессию."""
    lang = _lang(target)
    await _stop_qr(target.chat.id)
    await state.clear()
    await mytelethon.auth.cancel()
    await target.answer(_t("cancelled", lang))


# --------------------------------------------------------------------- callbacks


@router.callback_query(F.data == "login:start")
async def cb_start(callback: types.CallbackQuery, state: FSMContext, mytelethon):
    if callback.from_user.id != ADMIN_ID:
        return await _deny(callback)
    lang = _lang(callback)
    await _stop_qr(_chat_id(callback))
    await state.clear()
    await callback.answer()
    status = state_label(mytelethon.health()["state"], lang)
    text = _t("menu", lang, status=status)
    if isinstance(callback.message, types.Message):
        await _edit(callback.message, text, _menu_kb(lang))
    else:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=_menu_kb(lang))


@router.callback_query(F.data == "login:status")
async def cb_status(callback: types.CallbackQuery, mytelethon):
    if callback.from_user.id != ADMIN_ID:
        return await _deny(callback)
    lang = _lang(callback)
    state_now = await mytelethon.auth.check(ignore_flow=True)
    await callback.answer()

    if state_now is AuthState.OK:
        text = await _connected_text(mytelethon, lang)
    else:
        text = _t("menu", lang, status=state_label(state_now, lang))

    if isinstance(callback.message, types.Message):
        await _edit(callback.message, text, _menu_kb(lang) if state_now is not AuthState.OK else None)


@router.callback_query(F.data == "login:qr")
async def cb_qr(callback: types.CallbackQuery, state: FSMContext, mytelethon):
    if callback.from_user.id != ADMIN_ID:
        return await _deny(callback)
    lang = _lang(callback)
    chat_id = _chat_id(callback)
    await _stop_qr(chat_id)
    await state.clear()
    await callback.answer()

    res = await mytelethon.auth.start_qr()

    if res["status"] == "ok":
        text = await _connected_text(mytelethon, lang)
        if isinstance(callback.message, types.Message):
            await _edit(callback.message, text)
        return
    if res["status"] != "qr":
        text = _login_failure(res, lang, "qr")
        if isinstance(callback.message, types.Message):
            await _edit(callback.message, text, _menu_kb(lang))
        return

    if not isinstance(callback.message, types.Message):
        msg = await callback.bot.send_message(callback.from_user.id, _t("qr_wait", lang, sec=res["expires_in"]))
    else:
        msg = callback.message
        await _edit(msg, _t("qr_wait", lang, sec=res["expires_in"]), _qr_kb(lang, res["url"]))

    _qr_tasks[chat_id] = asyncio.create_task(
        _qr_loop(msg, mytelethon, lang, res, state), name=f"login_qr_{chat_id}"
    )


def _qr_kb(lang: str, url: str) -> types.InlineKeyboardMarkup:
    """Кнопка-ссылка `tg://login` + путь назад к коду."""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=_t("btn_open", lang), url=url)],
        [types.InlineKeyboardButton(text=_t("btn_code", lang), callback_data="login:code")],
        [types.InlineKeyboardButton(text=_t("btn_cancel", lang), callback_data="login:cancel")],
    ])


async def _qr_loop(message: types.Message, mytelethon, lang: str, res: dict, state: FSMContext):
    """Крутит QR-токен, переписывая одно сообщение, пока вход не завершится."""
    auth = mytelethon.auth
    try:
        while True:
            if res.get("url"):
                await _edit(message, _t("qr_wait", lang, sec=res.get("expires_in", QR_WAIT_SEC)), _qr_kb(lang, res["url"]))

            out = await auth.wait_qr(timeout=QR_WAIT_SEC)

            if out["status"] == "ok":
                await _edit(message, await _connected_text(mytelethon, lang))
                return

            if out["status"] == "timeout":
                res = await auth.start_qr()
                if res.get("status") == "ok":
                    await _edit(message, await _connected_text(mytelethon, lang))
                    return
                if res.get("status") != "qr":
                    await _edit(message, _login_failure(res, lang, "qr"), _menu_kb(lang))
                    return
                continue

            if out["status"] == "password":
                await state.set_state(LoginStates.password)
                await state.update_data(password_attempts=0)
                await _edit(message, _t("password_prompt", lang), _cancel_kb(lang))
                return

            await _edit(message, _login_failure(out, lang, "qr"), _menu_kb(lang))
            return
    except asyncio.CancelledError:
        logger.debug("QR login loop cancelled")
        raise
    except Exception:
        logger.exception("QR login loop crashed")
        await _edit(message, _t("failure", lang, code="qr"), _menu_kb(lang))
    finally:
        _qr_tasks.pop(message.chat.id, None)


@router.callback_query(F.data == "login:code")
async def cb_code(callback: types.CallbackQuery, state: FSMContext, mytelethon):
    if callback.from_user.id != ADMIN_ID:
        return await _deny(callback)
    lang = _lang(callback)
    chat_id = _chat_id(callback)
    await _stop_qr(chat_id)
    await state.clear()
    await state.set_state(LoginStates.phone)
    await callback.answer()
    text = _t("phone_prompt", lang)
    if isinstance(callback.message, types.Message):
        await _edit(callback.message, text, _cancel_kb(lang))
    else:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=_cancel_kb(lang))


@router.callback_query(F.data == "login:sms")
async def cb_sms(callback: types.CallbackQuery, mytelethon):
    if callback.from_user.id != ADMIN_ID:
        return await _deny(callback)
    lang = _lang(callback)
    phone = mytelethon.auth.flow.phone
    if not phone:
        return await callback.answer("Сначала номер / phone first", show_alert=True)

    await callback.answer()
    res = await mytelethon.auth.send_code(phone, force_sms=True)
    if res["status"] == "code_sent":
        text = _t("sms_requested", lang, phone=res["phone"])
    else:
        text = _login_failure(res, lang, "sms")
    if isinstance(callback.message, types.Message):
        await _edit(callback.message, text, _cancel_kb(lang))


@router.callback_query(F.data == "login:cancel")
async def cb_cancel(callback: types.CallbackQuery, state: FSMContext, mytelethon):
    if callback.from_user.id != ADMIN_ID:
        return await _deny(callback)
    lang = _lang(callback)
    chat_id = _chat_id(callback)
    await _stop_qr(chat_id)
    await state.clear()
    await callback.answer()
    await mytelethon.auth.cancel()
    if isinstance(callback.message, types.Message):
        await _edit(callback.message, _t("cancelled", lang))


# --------------------------------------------------------------------- шаги FSM


@router.message(LoginStates.phone, F.text)
async def on_phone(message: types.Message, state: FSMContext, mytelethon):
    if message.from_user.id != ADMIN_ID:
        return
    lang = _lang(message)
    if await _is_cancel_word(message.text):
        return await _cancel(message, state, mytelethon)

    res = await mytelethon.auth.send_code(message.text)

    if res["status"] == "code_sent":
        await state.set_state(LoginStates.code)
        await state.update_data(code_attempts=0, phone=res["phone"])
        await message.answer(
            _t("code_prompt", lang, where=_where_text(lang, res.get("code_type"), res.get("phone"))),
            reply_markup=_code_kb(lang),
        )
        return

    # Остальное — «нет аккаунта», «номер заблокирован», flood, сеть: показываем
    # причину и остаёмся в шаге телефона, чтобы можно было исправить ввод.
    await message.answer(_login_failure(res, lang, "phone"), reply_markup=_cancel_kb(lang))


def _where_text(lang: str, code_type: str | None, phone: str | None) -> str:
    key = {
        "app": "where_app",
        "sms": "where_sms",
        "call": "where_call",
        "flash": "where_flash",
        "missed": "where_missed",
        "email": "where_email",
    }.get(str(code_type or ""), "where_other")
    return _t(key, lang, phone=phone or "…")


def _code_kb(lang: str) -> types.InlineKeyboardMarkup:
    return _kb(
        [(_t("btn_sms_alt", lang), "login:sms")],
        [(_t("btn_cancel", lang), "login:cancel")],
    )


def _login_failure(res: dict, lang: str, stage: str) -> str:
    """Переводит ответ машины входа в текст для владельца."""
    status = res.get("status")
    if status == "not_configured":
        return _t("not_configured", lang)
    if status == "invalid_phone":
        return _t("invalid_phone", lang)
    if status == "no_account":
        return _t("no_account", lang)
    if status == "banned":
        return _t("banned", lang)
    if status == "bad_input":
        return _t("bad_code" if stage == "code" else "bad_password", lang)
    if status == "flood":
        return _t("flood", lang, sec=res.get("seconds", 0))
    return _t("failure", lang, code=html.escape(str(res.get("code") or status or "error")))


@router.message(LoginStates.code, F.text)
async def on_code(message: types.Message, state: FSMContext, mytelethon):
    if message.from_user.id != ADMIN_ID:
        return
    lang = _lang(message)
    if await _is_cancel_word(message.text):
        await _delete_sensitive(message)
        return await _cancel(message, state, mytelethon)

    data = await state.get_data()
    attempts = int(data.get("code_attempts", 0))
    res = await mytelethon.auth.verify_code(message.text)
    # Код прочитан — сообщение с ним в чате не оставляем.
    await _delete_sensitive(message)

    if res["status"] == "ok":
        await state.clear()
        await message.answer(await _connected_text(mytelethon, lang))
        return

    if res["status"] == "password":
        await state.set_state(LoginStates.password)
        await state.update_data(password_attempts=0)
        await message.answer(_t("password_prompt", lang), reply_markup=_cancel_kb(lang))
        return

    if res["status"] == "invalid_code":
        attempts += 1
        if attempts >= CODE_ATTEMPTS:
            await state.clear()
            await message.answer(_t("too_many", lang), reply_markup=_menu_kb(lang))
            return
        await state.update_data(code_attempts=attempts)
        await message.answer(
            _t("invalid_code", lang, n=attempts, total=CODE_ATTEMPTS),
            reply_markup=_code_kb(lang),
        )
        return

    if res["status"] == "expired_code":
        await message.answer(_t("expired_code", lang), reply_markup=_code_kb(lang))
        return

    await message.answer(_login_failure(res, lang, "code"), reply_markup=_cancel_kb(lang))


@router.message(LoginStates.password, F.text)
async def on_password(message: types.Message, state: FSMContext, mytelethon):
    if message.from_user.id != ADMIN_ID:
        return
    lang = _lang(message)
    if await _is_cancel_word(message.text):
        await _delete_sensitive(message)
        return await _cancel(message, state, mytelethon)

    data = await state.get_data()
    attempts = int(data.get("password_attempts", 0))
    res = await mytelethon.auth.verify_password(message.text)
    # Пароль прочитан — удаляем его из чата до любых сообщений.
    await _delete_sensitive(message)

    if res["status"] == "ok":
        await state.clear()
        await message.answer(await _connected_text(mytelethon, lang))
        return

    if res["status"] == "wrong_password":
        attempts += 1
        if attempts >= PASSWORD_ATTEMPTS:
            await state.clear()
            await message.answer(_t("too_many", lang), reply_markup=_menu_kb(lang))
            return
        await state.update_data(password_attempts=attempts)
        await message.answer(
            _t("wrong_password", lang, n=attempts, total=PASSWORD_ATTEMPTS),
            reply_markup=_cancel_kb(lang),
        )
        return

    await message.answer(_login_failure(res, lang, "password"), reply_markup=_cancel_kb(lang))
