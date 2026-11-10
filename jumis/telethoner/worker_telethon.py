# master/telethoner/mytelethon.py
import asyncio
from datetime import datetime
from utils.common import get_now_datetime, to_app_tz_naive
from telethon import TelegramClient, events, types, utils

from config import API_HASH, API_ID, PATH_SESSION, TELETHON_CATCH_UP
from logs.set_logger import logger
from telethoner.auth import (
    NEEDS_LOGIN,
    WAITING_STATES,
    AuthState,
    TelethonAuth,
    classify_error,
)


class myTelethon:
    RETRY_SEC = 5      # пауза перед переподключением при сети/сбое
    LOGIN_WAIT_SEC = 300  # как часто перепроверять сессию, пока ждём вход

    def __init__(self, queue_messages):
        self.client = None
        self.queue_messages = queue_messages
        self.me_id = None
        # async (state, detail) -> None: уведомления владельцу о смене состояния
        self.on_state_change = None
        # Машина входа: QR/код/2FA. Клиент берёт у нас, чтобы на файл сессии
        # всегда был ровно один процесс-писатель.
        self.auth = TelethonAuth(self._ensure_client, self._relay_state)

    async def _relay_state(self, state: AuthState, detail: str) -> None:
        if self.on_state_change:
            await self.on_state_change(state, detail)

    def health(self) -> dict:
        """Статус для `/admin` и уведомлений."""
        return self.auth.health()

    async def whoami(self) -> dict:
        """Имя/id аккаунта userbot для сообщений «кто подключён». Пусто — если нет."""
        try:
            client = await self._ensure_client()
            me = await client.get_me()
        except Exception as e:
            logger.warning("whoami failed: %s", e)
            return {}
        if not me:
            return {}
        name = " ".join(
            part for part in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if part
        )
        return {
            "id": me.id,
            "name": name or (getattr(me, "username", None) or "owner"),
            "username": getattr(me, "username", None),
        }

    async def detect_media_info(self, event) -> dict:
        """Определяет точный тип медиафайла и извлекает первичные данные."""
        if not event.media:
            return {"msg_type": "text", "media_file_id": None, "media_name": None}

        media = event.media
        audio_bytes = None

        if isinstance(media, types.MessageMediaPhoto):
            return {
                "msg_type": "photo",
                "media_file_id": str(getattr(media.photo, "id", None)),
                "media_name": None,
            }

        if isinstance(media, types.MessageMediaDocument):
            doc = media.document
            msg_type = "document"
            media_name = None

            # Разбираем атрибуты документа (голос, видео, стикер, имя файла)
            if doc and doc.attributes:
                for attr in doc.attributes:
                    if isinstance(attr, types.DocumentAttributeAudio):
                        msg_type = "voice" if attr.voice else "audio"
                    elif isinstance(attr, types.DocumentAttributeVideo):
                        msg_type = "video"
                    elif isinstance(attr, types.DocumentAttributeSticker):
                        msg_type = "sticker"
                    elif isinstance(attr, types.DocumentAttributeFilename):
                        media_name = attr.file_name

            # Если сообщение содержит голосовое или аудио
            if event.message.voice or event.message.audio:
                # Telethon скачивает файл ПРЯМО В ОПЕРАТИВКУ
                audio_bytes = await event.message.download_media(file=bytes)

            return {
                "msg_type": msg_type,
                "media_file_id": str(getattr(doc, "id", None)),
                "media_name": media_name,
                "audio_bytes": audio_bytes
            }

        if isinstance(media, types.MessageMediaContact):
            return {
                "msg_type": "contact",
                "media_file_id": None,
                "media_name": getattr(media, "phone_number", None),
            }

        if isinstance(media, types.MessageMediaGeo) or isinstance(
            media, types.MessageMediaVenue
        ):
            return {
                "msg_type": "location",
                "media_file_id": None,
                "media_name": None,
            }

        if isinstance(media, types.MessageMediaPoll):
            return {"msg_type": "poll", "media_file_id": None, "media_name": None}

        return {"msg_type": "other_media", "media_file_id": None, "media_name": None}



    async def _ensure_client(self):
        """Один клиент на процесс: создаём при первом обращении и переиспользуем.

        Второй клиент на тот же файл сессии = sqlite-lock, а один и тот же ключ
        с двух мест Telegram наказывает отзывом (AuthKeyDuplicated). Поэтому
        и `run()`, и мастер входа берут клиента только здесь.
        """
        if self.client is None:
            if not API_ID or not API_HASH:
                raise RuntimeError("api_id/api_hash are not set")
            self.client = TelegramClient(
                PATH_SESSION, API_ID, API_HASH, catch_up=TELETHON_CATCH_UP
            )
            self._register_handlers()

        if not self.client.is_connected():
            await self.client.connect()

        return self.client

    def _register_handlers(self):
        """Обработчики личных сообщений и реакций — один раз на клиента."""

        @self.client.on(events.MessageEdited())
        async def reaction_handler(event):
            # 1. Фильтруем только личные сообщения
            if not event.is_private:
                return

            # 2. Проверяем наличие реакций в обновленном сообщении
            reactions = getattr(event.message, "reactions", None)
            if not reactions or not getattr(reactions, "recent_reactions", None):
                return

            # 3. Получаем объект чата (собеседника)
            chat = await event.get_chat()

            if not chat or getattr(chat, "bot", False):
                return

            # 4. Кэшируем свой ID
            if not hasattr(self, "me_id") or not self.me_id:
                me = await self.client.get_me()
                self.me_id = me.id

            # 5. Находим самую последнюю поставленную реакцию
            latest_reaction = max(reactions.recent_reactions, key=lambda r: r.date)
            reactor_id = latest_reaction.peer_id.user_id if isinstance(latest_reaction.peer_id, types.PeerUser) else None

            # -------------------------------------------------------------------
            # ВЕТКА 1: Реакция собеседника (Peer)
            # -------------------------------------------------------------------
            if reactor_id != self.me_id:
                # Пока ничего не делаем, задел на будущее
                logger.debug(f"[Reaction] Собеседник {chat.id} поставил реакцию.")
                return

            # -------------------------------------------------------------------
            # ВЕТКА 2: Твоя реакция (Owner)
            # -------------------------------------------------------------------
            emoji = getattr(latest_reaction.reaction, "emoticon", "❤")

            # ВАЖНО: В user_payload передаем ТВОЙ ID (отправителя), 
            # чтобы IngestWorker поставил direction = 'outbound_owner'
            user_payload = {
                "tg_id": self.me_id,  # (Ты отправитель!)
                "username": "owner",
                "first_name": "Owner",
                "last_name": None,
            }

            synthetic_msg_id = -int(latest_reaction.date.timestamp() * 1000)

            # В message_payload передаем ID чата/собеседника
            message_payload = {
                "tg_msg_id": synthetic_msg_id,
                "chat_id": chat.id,   # (Получатель реакта)
                "is_outgoing": True,
                "content": f"Реакция: {emoji}",
                "created_at": latest_reaction.date,
                "msg_type": "reaction",
                "media_file_id": None,
                "media_name": None,
            }

            task_payload = {"user": user_payload, "message": message_payload}
            await self.queue_messages.put(task_payload)





        @self.client.on(events.NewMessage())
        async def handler(event):

            # 1. Группы, супергруппы и каналы отсекаются сразу (даже если пишешь ты)
            if not event.is_private:
                return

            # 2. Получаем объект чата (собеседника)
            chat = await event.get_chat()

            if not chat:
                return

            # 3. Игнорируем чаты с ботами (даже если ты сам написал какому-то боту)
            if getattr(chat, "bot", False):
                return

            # 4. Если сообщение входящее, проверяем отправителя на скам / фейк
            sender = await event.get_sender()

            if not event.out:

                if not sender:
                    return

                if getattr(sender, "scam", False) or getattr(sender, "fake", False):
                    return

            # 5. Полный пакет данных об отправителе (Interlocutor / Sender)
            user_payload = {
                "tg_id": sender.id,
                "username": sender.username.lower() if sender.username else None,
                "first_name": getattr(sender, "first_name", None),
                "last_name": getattr(sender, "last_name", None),
                "full_name": (
                    f"{getattr(sender, 'first_name', '') or ''} {getattr(sender, 'last_name', '') or ''}".strip()
                    or "Unnamed"
                ),
                "phone": getattr(sender, "phone", None),
                "lang_code": getattr(sender, "lang_code", None),
                # Флаги аккаунта (для будущего анализа / фильтрации)
                # "is_bot": getattr(sender, "bot", False),
                "is_premium": getattr(sender, "premium", False),
                "is_verified": getattr(sender, "verified", False),
                "is_scam": getattr(sender, "scam", False),
                "is_fake": getattr(sender, "fake", False),
                "is_restricted": getattr(sender, "restricted", False),
                "is_mutual_contact": getattr(sender, "mutual_contact", False),
            }

            # 6. Детализация медиа (если есть)
            media_info = await self.detect_media_info(event)

            # 7. Полный пакет данных сообщения
            # Извлекаем дату из события Telethon
            raw_date = getattr(event, "date", None)

            # ХЗ нахер сток ебни, можно просто дату сейчас воткнуть, но пусть..
            if isinstance(raw_date, datetime):
                if raw_date.tzinfo is not None:
                    # Переводим из UTC в цифры твоего пояса и сдираем плашку тайм-зоны
                    created_at = to_app_tz_naive(raw_date)
                else:
                    created_at = raw_date
            else:
                # Если даты не было — берём текущее настенное время
                created_at = get_now_datetime()

            message_payload = {
                "tg_msg_id": event.id,
                "chat_id": event.chat_id,  # Уникальный ID чата/собеседника
                "is_outgoing": event.out,
                "content": event.raw_text or "",
                "created_at": created_at,  # Чистые настенные цифры без tzinfo
                **media_info,  # Подмешивает msg_type, media_file_id, media_name
            }

            # 8. Собираем итоговую задачу в очередь self.queue_messages
            # Тут минимально очищенные входящие + мои исходящие уходят в очередь и вылав-
            # ливаются в jumis/ingest/worker.py
            task_payload = {"user": user_payload, "message": message_payload}
            await self.queue_messages.put(task_payload)



    async def _disconnect(self):
        """Аккуратно отключает текущий клиент Telethon."""
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.warning("Telethon disconnect error: %s", e)
            finally:
                self.client = None


    async def run(self):
        """
        Основной цикл: проверяет сессию, ждёт вход (если нужно) и слушает сообщения.

        Никакого `client.start()`: он спрашивает телефон через `input()`, а в боте
        терминала нет — раньше это давало EOFError, лог «connection lost» каждые
        5 секунд и мёртвый userbot при живом aiogram. Теперь состояние проверяется
        явно, а вход делается кнопками в боте (`/login`).
        """
        while True:
            try:
                state = await self.auth.check()
            except asyncio.CancelledError:
                await self._disconnect()
                raise
            except Exception as e:
                logger.exception("Telethon session check crashed")
                await self.auth.set_state(classify_error(e), str(e))
                await asyncio.sleep(self.RETRY_SEC)
                continue

            # Вход нужен прямо сейчас: стоим на событии, но раз в 5 минут
            # перепроверяем — вдруг сессию починили снаружи (create_session.py).
            if state in NEEDS_LOGIN or state in WAITING_STATES:
                if state in NEEDS_LOGIN:
                    logger.warning("Telethon login required (%s)", state.value)
                try:
                    await asyncio.wait_for(self.auth.ready.wait(), timeout=self.LOGIN_WAIT_SEC)
                except asyncio.TimeoutError:
                    pass
                continue

            # Сеть/занятый файл/прочее — ждём и пробуем снова, вход не нужен.
            if state is not AuthState.OK:
                await asyncio.sleep(self.RETRY_SEC)
                continue

            try:
                logger.info("Telethon connected, listening")
                await self.client.run_until_disconnected()
                logger.warning("Telethon disconnected")

            except asyncio.CancelledError:
                await self._disconnect()
                raise

            except Exception as e:
                state = classify_error(e)
                logger.warning("Telethon connection lost (%s): %s", state.value, e)
                await self.auth.set_state(state, str(e))
                await self._disconnect()
                await asyncio.sleep(self.RETRY_SEC)


    async def stop(self):
        """Остановка клиента при завершении приложения."""
        await self._disconnect()


    @staticmethod
    def _split_text_smart(text: str, max_length: int = 4000) -> list[str]:
        """Разбивает текст по абзацам или предложениям, не превышая max_length."""
        if len(text) <= max_length:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break

            # Ищем естественные границы: сначала двойной перенос, потом одинарный, потом пробел
            split_pos = text.rfind("\n\n", 0, max_length)
            if split_pos == -1:
                split_pos = text.rfind("\n", 0, max_length)
            if split_pos == -1:
                split_pos = text.rfind(" ", 0, max_length)
            if split_pos == -1:
                split_pos = max_length  # Если нет пробелов, режем по лимиту

            chunks.append(text[:split_pos].strip())
            text = text[split_pos:].strip()

        return chunks



    async def send_message(
            self, 
            message_text: str, 
            telegram_id: int | None = None, 
            username: str | None = None
        ) -> int | str:
        """Отправить сообщение клиенту от моего личного имени через Telethon по 
            username или user_id, в группу или канал, возвращает 
            user_id/-id_canel/str - если ошибка"""

        if not message_text:
            return "Error: message text is empty"

        if not self.client or not self.client.is_connected():
            logger.error("Telethon client is not connected")
            return "❌ Error: Telethon client not connected."

        # Сессия проверена и жива? Иначе честнее сказать агенту причину, чем
        # отдавать ему RPC-ошибку «AUTH_KEY_UNREGISTERED» без перевода.
        if self.auth.state is not AuthState.OK:
            logger.warning("Send blocked: userbot state=%s", self.auth.state.value)
            return (
                f"❌ Error: userbot is not connected to Telegram "
                f"(state: {self.auth.state.value}). The owner must log in via /login."
            )

        # Очищаем юзернейм от @ и пробелов
        clean_username = username.strip().lstrip("@") if username else None
        target = telegram_id or clean_username

        if not target:
            return "Error: no ID or Username provided"

        try:
            # Умно нарезаем текст, если он превышает лимит MTProto
            chunks = self._split_text_smart(message_text, max_length=4000)
            for chunk in chunks:
                await self.client.send_message(target, chunk)

            entity = await self.client.get_entity(target)
            return utils.get_peer_id(entity) # int/-int - пользолватель/канал
                    
        except Exception as e:
            target_log = f"id: {telegram_id}" if telegram_id else f"@{clean_username}"
            error_msg = f"Не удалось отправить сообщение на {target_log}: {e}"
            logger.error(error_msg)
            return f"❌ Error sending to {target_log}.\n Error: {e}" # str












# # Поиск приватного канала по названию среди своих диалогов:
# async for dialog in client.iter_dialogs():
#     if dialog.name == "Мой Рабочий Дневник":
#         await client.send_message(dialog.id, "Текст")
#         break




    # async def send_message(self, message_text: str, telegram_id: int = None, username: str = None) -> int | str:
    #     """Отправить сообщение клиенту от моего личного имени через Telethon."""

    #     if not message_text:
    #         return "Ошибка: текст сообщения пуст / Error: message text is empty"

    #     if not self.client or not self.client.is_connected():
    #         print("❌ Telethon client not connected.")
    #         return "❌ Ошибка: сессия Telethon не подключена. / Telethon client not connected."

    #     target = telegram_id or (username.strip().replace("@", "") if username else None)

    #     if not target:
    #         return "Ошибка: не указан ни ID, ни Юзернейм / Error: no ID or Username provided"

    #     try:
    #         # Умно нарезаем текст, если он превышает лимит MTProto
    #         chunks = self._split_text_smart(message_text, max_length=4000)

    #         # Отправляем все части последовательно в один диалог
    #         for chunk in chunks:
    #             await self.client.send_message(target, chunk)
            
    #         if isinstance(target, str):
    #             entity = await self.client.get_entity(target)
    #             return entity.id
            
    #         return telegram_id
            
    #     except Exception as e:
    #         target_log = f"id: {telegram_id}" if telegram_id else f"@{username}"
    #         error_msg = f"Не удалось отправить сообщение на {target_log}: {e}"
    #         print(error_msg)
    #         return f"❌ Ошибка отправки на {target_log}.\nВозможно, у вас нет открытого диалога с пользователем или он вас заблокировал."

