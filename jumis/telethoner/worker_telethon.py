# master/telethoner/mytelethon.py
import asyncio
from urllib.parse import urlparse
from telethon import TelegramClient, events, connection, types

from config import API_ID, API_HASH, USE_MTPROTO, ERR_PROXY_LIMIT
from proxy.mtprotoproxy import MTPROXY_STRINGS
from logs.set_logger import set_logger

logger = set_logger(name="telethon")




class myTelethon:
    def __init__(self, queue_messages):
        self.use_proxy = USE_MTPROTO
        self.proxy_strings = MTPROXY_STRINGS if self.use_proxy else []
        self.client = None
        self.current_proxy_index = -1   # индекс последнего успешного прокси
        self.error_limit = ERR_PROXY_LIMIT
        self.queue_messages = queue_messages


    def _parse_proxy_str(self, proxy_str: str):
        """Преобразует строку вида mtproxy://host:port:secret в кортеж (host, port, secret)"""
        parts = proxy_str.replace('mtproxy://', '').split(':')
        host = parts[0]
        port = int(parts[1])
        secret = parts[2]   # hex-строка
        return (host, port, secret)




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



    def _connect_proxy(self, proxy_tuple=None):
        """
        Создаёт клиента Telethon с указанным прокси (или без).
        Регистрирует обработчик сообщений.
        """
        if proxy_tuple:
            host, port, secret = proxy_tuple
            self.client = TelegramClient(
                'session_jumis',
                API_ID,
                API_HASH,
                proxy=(host, port, secret),
                connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
                auto_reconnect=False   # отключаем встроенный reconnect
            )
        else:
            self.client = TelegramClient('session_jumis', API_ID, API_HASH)


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
                # sender = await event.get_sender()
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
            message_payload = {
                "tg_msg_id": event.id,
                "chat_id": event.chat_id,  # Уникальный ID чата/собеседника
                "is_outgoing": event.out,
                "content": event.raw_text or "",
                "created_at": event.date,  # datetime с tz=UTC (подходит под TIMESTAMPTZ)
                **media_info,  # Подмешивает msg_type, media_file_id, media_name
            }

            # 8. Собираем итоговую задачу в очередь self.queue_messages
            # Тут минимально очищенные входящие + мои исходящие уходят в очередь и вылав-
            # ливаются в jumis/ingest/worker.py
            task_payload = {"user": user_payload, "message": message_payload}
            await self.queue_messages.put(task_payload)




    async def _try_connect(self):
        """
        Перебирает прокси по кругу (начиная со следующего за последним успешным),
        пытается подключиться. При успехе сохраняет индекс и возвращает True.
        Если ни один не подошёл, возвращает False.
        """
        if not self.proxy_strings:
            # без прокси
            self._connect_proxy(None)
            try:
                await self.client.connect()
                print("✅ Connected without proxy")
                return True
            except Exception as e:
                print(f"❌ Connection without proxy failed: {e}")
                self.client = None
                return False

        # Начинаем со следующего индекса
        start = (self.current_proxy_index + 1) % len(self.proxy_strings)
        for i in range(len(self.proxy_strings)):
            idx = (start + i) % len(self.proxy_strings)
            proxy_str = self.proxy_strings[idx]
            proxy_tuple = self._parse_proxy_str(proxy_str)
            print(f"🔄 Trying MTProxy {proxy_tuple[0]}:{proxy_tuple[1]}...")
            self._connect_proxy(proxy_tuple)
            try:
                await self.client.connect()
                # Успешно – сохраняем индекс
                self.current_proxy_index = idx
                print(f"✅ Connected via MTProxy {proxy_tuple[0]}:{proxy_tuple[1]}")
                return True
            except Exception as e:
                print(f"❌ Failed: {e}")
                await self.client.disconnect()
                self.client = None
                continue

        print("❌ No working MTProxy found")
        return False


    async def run(self):
        """
        Основной цикл: пытается подключиться с ротацией прокси,
        затем запускает run_until_disconnected, переподключаясь при обрыве.
        """
        if not await self._try_connect():
            return

        consecutive_errors = 0
        while True:
            try:
                await self.client.start()
                print("✅ Telethon started, listening...")
                await self.client.run_until_disconnected()
            except Exception as e:
                print(f"⚠️ Telethon connection lost: {e}")
                consecutive_errors += 1
                if consecutive_errors > self.error_limit:
                    # Переключаем прокси
                    if not await self._try_connect():
                        print("❌ No working proxy, exiting")
                        break
                    consecutive_errors = 0
                await asyncio.sleep(1)


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


    async def send_message(self, message_text: str, telegram_id: int = None, username: str = None) -> int | str:
        """Отправить сообщение клиенту от моего личного имени через Telethon."""

        if not message_text:
            return "Ошибка: текст сообщения пуст / Error: message text is empty"

        if not self.client or not self.client.is_connected():
            print("❌ Telethon client not connected.")
            return "❌ Ошибка: сессия Telethon не подключена. / Telethon client not connected."

        target = telegram_id or (username.strip().replace("@", "") if username else None)

        if not target:
            return "Ошибка: не указан ни ID, ни Юзернейм / Error: no ID or Username provided"

        try:
            # Умно нарезаем текст, если он превышает лимит MTProto
            chunks = self._split_text_smart(message_text, max_length=4000)

            # Отправляем все части последовательно в один диалог
            for chunk in chunks:
                await self.client.send_message(target, chunk)
            
            if isinstance(target, str):
                entity = await self.client.get_entity(target)
                return entity.id
            
            return telegram_id
            
        except Exception as e:
            target_log = f"id: {telegram_id}" if telegram_id else f"@{username}"
            error_msg = f"Не удалось отправить сообщение на {target_log}: {e}"
            print(error_msg)
            return f"❌ Ошибка отправки на {target_log}.\nВозможно, у вас нет открытого диалога с пользователем или он вас заблокировал."

