# jumis/telethoner/auth.py
"""Машина входа в Telegram для userbot — без единой строки пользовательского текста.

Здесь только состояние, коды результата и работа с Telethon. Все подписи, кнопки
и ru/en-локализация живут в `handlers/login.py`: так машину можно прогнать тестами
без aiogram и позже перевести на другой интерфейс, не трогая логику.

Два входа, которые видит владелец:
  * QR   — `start_qr()` → ссылка `tg://login?...` → владелец подтверждает в приложении;
  * код  — `send_code(phone)` → `verify_code(code)` → при 2FA `verify_password(pwd)`.

Никакого `client.start()`: он спрашивает телефон через `input()`, а в боте
терминала нет — отсюда был бесконечный цикл переподключений.
"""

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from telethon import errors

from config import API_HASH, API_ID
from logs.set_logger import logger


class AuthState(str, Enum):
    """Состояние сессии: его видят уведомления, `/admin` и `run()`.

    `str`-наследование — чтобы состояние можно было писать в лог и сравнивать
    со строкой в тестах без магии.
    """

    OK = "ok"                             # сессия жива, слушаем сообщения
    NOT_CONFIGURED = "not_configured"     # в .env нет api_id/api_hash
    NO_SESSION = "no_session"             # ключа нет — первичная настройка
    REVOKED = "revoked"                   # ключ есть, но Telegram его отозвал
    WAITING_QR = "waiting_qr"             # выдан QR, ждём подтверждения
    WAITING_CODE = "waiting_code"         # код выслан, ждём ввода
    WAITING_PASSWORD = "waiting_password" # код принят, ждём пароль 2FA
    OFFLINE = "offline"                   # нет связи с Telegram
    LOCKED = "locked"                     # файл сессии занят другим процессом
    ERROR = "error"                       # прочее


# Состояния, из которых выбираются только входом (или он не нужен вовсе).
NEEDS_LOGIN = frozenset({AuthState.NO_SESSION, AuthState.REVOKED, AuthState.NOT_CONFIGURED})
WAITING_STATES = frozenset({AuthState.WAITING_QR, AuthState.WAITING_CODE, AuthState.WAITING_PASSWORD})

# Ошибки, означающие «ключ больше не действует»: лечится только повторным входом.
REVOKED_ERRORS = (
    errors.AuthKeyUnregisteredError,
    errors.AuthKeyDuplicatedError,
    errors.AuthKeyPermEmptyError,
    errors.SessionRevokedError,
    errors.SessionExpiredError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
)

# Сеть/сокет: повторяем позже, вход не нужен.
NETWORK_ERRORS = (ConnectionError, OSError, asyncio.TimeoutError)

# Как Telegram отправил код (класс `SentCodeType` → короткое имя для текстов).
CODE_TYPES = {
    "SentCodeTypeApp": "app",
    "SentCodeTypeSms": "sms",
    "SentCodeTypeCall": "call",
    "SentCodeTypeFlashCall": "flash",
    "SentCodeTypeMissedCall": "missed",
    "SentCodeTypeEmailCode": "email",
    "SentCodeTypeFragmentSms": "fragment",
    "SentCodeTypeFirebaseSms": "firebase",
    "SentCodeTypeSmsWord": "sms",
    "SentCodeTypeSmsPhrase": "sms",
}


def classify_error(exc: BaseException) -> AuthState:
    """Классифицирует исключение Telethon в состояние сессии.

    Нужна, чтобы «нет интернета» и «Telegram отозвал ключ» не сливались в одну
    строку «connection lost» — лечатся они по-разному.
    """
    if isinstance(exc, REVOKED_ERRORS):
        return AuthState.REVOKED
    if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
        return AuthState.LOCKED
    if isinstance(exc, NETWORK_ERRORS):
        return AuthState.OFFLINE
    return AuthState.ERROR


def normalize_phone(raw: str | None) -> str | None:
    """Приводит ввод к виду `+79001234567`; None — если это не номер.

    Понимает пробелы, дефисы, скобки, ведущий `8` (русская привычка) и `+`.
    """
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if not 10 <= len(digits) <= 15:
        return None
    return "+" + digits


def code_type_name(sent_code) -> str:
    """`SentCode` → `app`/`sms`/`call`/… — чтобы сказать, где искать код."""
    return CODE_TYPES.get(type(getattr(sent_code, "type", None)).__name__, "unknown")


@dataclass
class LoginFlow:
    """Всё, что нужно помнить между шагами одного входа."""

    method: str = "code"                  # "code" | "qr"
    phone: str | None = None
    phone_code_hash: str | None = None
    code_type: str | None = None
    qr: object | None = None
    force_sms: bool = False


class TelethonAuth:
    """Состояние сессии и шаги входа поверх одного клиента Telethon.

    Клиент приходит через `client_factory` (async-функцию): машина не создаёт его
    сама, благодаря чему в тестах подставляется фейк, а в бою — тот же клиент,
    что слушает сообщения (два клиента на один файл сессии Telethon не переживёт).
    """

    def __init__(self, client_factory, on_state=None):
        self._factory = client_factory
        self._on_state = on_state            # async (state, detail) -> None
        self.state = AuthState.NO_SESSION
        self.detail = ""                     # короткая причина для лога и /admin
        self.me_id: int | None = None
        self.flow = LoginFlow()
        self.ready = asyncio.Event()         # вход завершён — будим `run()`
        self._lock = asyncio.Lock()          # один шаг входа за раз
        self._state_lock = asyncio.Lock()

    # ------------------------------------------------------------------ состояние

    async def set_state(self, state: AuthState, detail: str = "") -> None:
        """Запоминает состояние; обработчик уведомляется только о смене."""
        async with self._state_lock:
            changed = state != self.state
            self.state = state
            self.detail = detail or ""
            if changed and self._on_state:
                try:
                    await self._on_state(state, self.detail)
                except Exception:
                    logger.exception("Userbot state callback failed")

    def health(self) -> dict:
        return {"state": self.state, "detail": self.detail, "me_id": self.me_id}

    async def _client(self):
        return await self._factory()

    # ------------------------------------------------------------------ проверка

    async def check(self, ignore_flow: bool = False) -> AuthState:
        """Проверяет сессию настоящим запросом и запоминает состояние.

        `get_me()` у Telethon глотает `UnauthorizedError` и отдаёт None, поэтому
        «ключа нет» и «ключ отозван» различаем по наличию `auth_key` в сессии:
        для владельца это разные истории (первая настройка / подтвердить заново).
        """
        if self.state in WAITING_STATES and not ignore_flow:
            return self.state

        if not API_ID or not API_HASH:
            await self.set_state(AuthState.NOT_CONFIGURED, "api_id/api_hash missing")
            return self.state

        try:
            client = await self._client()
            me = await client.get_me()
        except Exception as e:
            state = classify_error(e)
            logger.warning("Telethon session check failed (%s): %s", state.value, e)
            await self.set_state(state, str(e))
            return self.state

        if me:
            self.me_id = me.id
            if self.state is not AuthState.OK:
                self.ready.set()
            await self.set_state(AuthState.OK)
            return self.state

        has_key = getattr(getattr(client, "session", None), "auth_key", None) is not None
        await self.set_state(AuthState.REVOKED if has_key else AuthState.NO_SESSION)
        return self.state

    async def _finish(self, user) -> None:
        """Общий финал любого входа: запомнить id, разбудить `run()`."""
        self.me_id = getattr(user, "id", None)
        self.flow = LoginFlow()
        self.ready.set()
        await self.set_state(AuthState.OK)
        logger.info("Telethon login completed: me_id=%s", self.me_id)

    # ------------------------------------------------------------------ QR-вход

    async def start_qr(self) -> dict:
        """Готовит новую ссылку QR-входа.

        Возвращает `{"status": "qr", "url": ..., "expires_in": сек, "qr": объект}`
        или `{"status": "ok"}` — если вход уже есть.
        """
        if not API_ID or not API_HASH:
            return {"status": "not_configured"}

        async with self._lock:
            try:
                client = await self._client()
                me = await client.get_me()
                if me:
                    await self._finish(me)
                    return {"status": "ok"}
                qr = await client.qr_login()
            except Exception as e:
                state = classify_error(e)
                await self.set_state(state, str(e))
                return {"status": "error", "code": state.value, "error": str(e)}

            self.flow = LoginFlow(method="qr", qr=qr)
            expires_in = max(0, int((qr.expires - _utcnow()).total_seconds()))
            await self.set_state(AuthState.WAITING_QR)
            return {"status": "qr", "url": qr.url, "expires_in": expires_in, "qr": qr}

    async def wait_qr(self, timeout: float = 18) -> dict:
        """Ждёт подтверждения QR. Возвращает короткий статус для обработчика."""
        qr = self.flow.qr
        if not qr:
            return {"status": "expired"}

        async with self._lock:
            try:
                user = await qr.wait(timeout=timeout)
            except asyncio.TimeoutError:
                return {"status": "timeout"}
            except errors.SessionPasswordNeededError:
                await self.set_state(AuthState.WAITING_PASSWORD)
                return {"status": "password"}
            except asyncio.CancelledError:
                raise
            except errors.FloodWaitError as e:
                return {"status": "flood", "seconds": e.seconds}
            except Exception as e:
                state = classify_error(e)
                await self.set_state(state, str(e))
                return {"status": "error", "code": state.value, "error": str(e)}

            await self._finish(user)
            return {"status": "ok"}

    # ------------------------------------------------------------------ вход по коду

    async def send_code(self, phone: str, force_sms: bool = False) -> dict:
        """Запрашивает код. `force_sms=True` — просить именно SMS."""
        if not API_ID or not API_HASH:
            return {"status": "not_configured"}

        phone = normalize_phone(phone)
        if not phone:
            return {"status": "invalid_phone"}

        async with self._lock:
            try:
                client = await self._client()
                sent = await client.send_code_request(phone, force_sms=force_sms)
            except errors.PhoneNumberInvalidError:
                return {"status": "invalid_phone"}
            except errors.PhoneNumberUnoccupiedError:
                return {"status": "no_account"}
            except errors.PhoneNumberBannedError:
                return {"status": "banned"}
            except errors.FloodWaitError as e:
                return {"status": "flood", "seconds": e.seconds}
            except Exception as e:
                state = classify_error(e)
                return {"status": "error", "code": state.value, "error": str(e)}

            self.flow = LoginFlow(
                method="code",
                phone=phone,
                phone_code_hash=getattr(sent, "phone_code_hash", None),
                code_type=code_type_name(sent),
                force_sms=force_sms,
            )
            await self.set_state(AuthState.WAITING_CODE)
            return {
                "status": "code_sent",
                "phone": phone,
                "code_type": self.flow.code_type,
            }

    async def verify_code(self, code: str) -> dict:
        """Проверяет код из Telegram/SMS. При 2FA вернёт `{"status": "password"}`."""
        digits = "".join(ch for ch in str(code or "") if ch.isdigit())
        if not self.flow.phone or not digits:
            return {"status": "bad_input"}

        async with self._lock:
            try:
                client = await self._client()
                user = await client.sign_in(
                    phone=self.flow.phone,
                    code=digits,
                    phone_code_hash=self.flow.phone_code_hash,
                )
            except errors.SessionPasswordNeededError:
                await self.set_state(AuthState.WAITING_PASSWORD)
                return {"status": "password"}
            except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
                return {"status": "invalid_code"}
            except (errors.PhoneCodeExpiredError, errors.PhoneCodeHashEmptyError):
                return {"status": "expired_code"}
            except errors.FloodWaitError as e:
                return {"status": "flood", "seconds": e.seconds}
            except Exception as e:
                state = classify_error(e)
                return {"status": "error", "code": state.value, "error": str(e)}

            await self._finish(user)
            return {"status": "ok"}

    # ------------------------------------------------------------------ 2FA

    async def verify_password(self, password: str) -> dict:
        """Второй фактор. Пароль нигде не сохраняется и не логируется."""
        password = str(password or "")
        if not password:
            return {"status": "bad_input"}

        async with self._lock:
            try:
                client = await self._client()
                user = await client.sign_in(password=password)
            except errors.PasswordHashInvalidError:
                return {"status": "wrong_password"}
            except errors.FloodWaitError as e:
                return {"status": "flood", "seconds": e.seconds}
            except Exception as e:
                state = classify_error(e)
                return {"status": "error", "code": state.value, "error": str(e)}

            await self._finish(user)
            return {"status": "ok"}

    # ------------------------------------------------------------------ отмена

    async def cancel(self) -> AuthState:
        """Бросает начатый вход и возвращает реальное состояние сессии."""
        async with self._lock:
            self.flow = LoginFlow()
            self.ready.clear()
        return await self.check(ignore_flow=True)


def _utcnow():
    """`datetime` в UTC — QRLogin.expires приходит с tzinfo."""
    return datetime.now(tz=timezone.utc)
