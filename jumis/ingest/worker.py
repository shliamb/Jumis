# jumis/ingest/worker.py
from config import ADMIN_ID
import asyncio
from vector import embedder
from stt_sense import stt
from logs.set_logger import set_logger

logger = set_logger(name="ingest")




class IngestionWorker:

    def __init__(self, db_messages, db_users, queue_messages: asyncio.Queue, queue_new_mess: asyncio.Queue, telethon_client):
        self.db_messages = db_messages
        self.db_users = db_users
        self.embedder = embedder
        self.queue_messages = queue_messages
        self.queue_new_mess = queue_new_mess
        self.admin_id = ADMIN_ID
        self.telethon_client = telethon_client

        # Множество для хранения ссылок на активные фоновые задачи (защита от GC в Python 3.11+)
        self.background_tasks = set()


    async def run(self):
        """Основной фоновый цикл обработки сообщений из очереди."""
        logger.info("[IngestionWorker] Запущен и ожидает сообщения...")
        try:
            while True:
                # Ждем появление нового элемента в очереди (не забирает CPU)
                message_data = await self.queue_messages.get()

                try:
                    # Запуск сохранения входящие/исходящие
                    await self._process_and_save(message_data)

                except Exception as e:
                    logger.error(f"[IngestionWorker] Ошибка сохранения: {e}", exc_info=True)
                finally:
                    # Обязательно сообщаем очереди, что задача обработана
                    self.queue_messages.task_done()

        except asyncio.CancelledError:
            logger.info("[IngestionWorker] Завершение работы воркера.")



    async def _background_vectorize(self, message_id: int, content: str):
        """Фоновая векторизация сообщения и обновление его записи в БД."""
        try:
            # 1. Получаем вектор
            embedding = await self.embedder.get_embedding(content)
            if not embedding:
                logger.error(f"[BG Embedding] Failed to generate embedding for message_id={message_id}")
                return

            # 2. Обновляем вектор в базе
            success = await self.db_messages.update_embedding(message_id=message_id, embedding=embedding)
            if success:
                logger.info(f"[BG Embedding] Message ID {message_id} successfully vectorized and updated.")
            else:
                logger.error(f"[BG Embedding] Failed to update embedding in DB for message_id={message_id}")

        except Exception as e:
            logger.error(f"[BG Embedding] Critical error vectorizing message_id={message_id}: {e}", exc_info=True)




    async def _process_and_save(self, data: dict):
        """Внутренняя логика разбора и записи в БД."""

        msg_info = data.get("message", {})
        user_info = data.get("user", {})
        
        chat_id = msg_info.get("chat_id")
        recipient_id = chat_id
        sender_id = user_info.get("tg_id")

        if not chat_id or not sender_id:
            logger.warning(f"[Ingest] Пропущено сообщение: отсутствует chat_id ({chat_id}) или tg_id ({sender_id}).")
            return

        msg_type = msg_info.get("msg_type", "text")
        content = msg_info.get("content")
        audio_bytes = msg_info.get("audio_bytes")
        direction = "outbound_owner" if sender_id == self.admin_id else "inbound_peer"
        is_favourites = (sender_id == self.admin_id and chat_id == self.admin_id)

        # Расшифровка голосовых из RAM
        if msg_type == "voice" and audio_bytes:
            try:
                logger.info(f"[STT] Расшифровка голосового из RAM для chat_id={chat_id}...")
                transcribed_text = await stt.transcribe(audio_bytes, file_extension=".ogg")
                content = transcribed_text.strip() if (transcribed_text and transcribed_text.strip()) else "[Голосовое сообщение без речи]"
            except Exception as e:
                logger.error(f"[STT] Ошибка расшифровки аудио для chat_id={chat_id}: {e}")
                content = "[Ошибка расшифровки голосового]"

        # VOICE + FAVORITES MESSAGES
        if is_favourites:
            if msg_type == "voice":
                preview = f"{content[:30]}..." if content and len(content) > 30 else content
                logger.info(f"[ResponseWorker] Голосовое из Избранного: {preview}")
                # Отправляем расшифрованный текст обратно в Избранное
                await self.telethon_client.send_message(message_text=content, telegram_id=chat_id, username=None)
            return

        # 1. ADD USER (входящие) Admin нажимает в первые /start
        username = user_info.get("username").lower() if user_info.get("username") else None
        first_name = user_info.get("first_name") or ""
        last_name = user_info.get("last_name") or ""
        full_name = f"{first_name} {last_name}".strip() or "Unnamed"

        if sender_id != self.admin_id:
            new_user_data = {
                "tg_id": sender_id,
                "username": username, # @
                "full_name": full_name,
                "phone": user_info.get("phone"),
                "lang_code": user_info.get("lang_code")
            }
            # Если пользователя нет - сохранит, если есть - ничего
            await self.db_users.add_user(new_user_data)

        # Типы, которые сохраняем в БД и векторизуем
        white_type = ["voice", "text"]

        msg_db_id = None

        # 2. ADD MESSAGE (входящие + исходящие только для реальных сообщений)
        if msg_type in white_type:
            data_message = {
                "chat_id": chat_id,
                "sender_id": sender_id,
                "recipient_id": chat_id,
                "tg_msg_id": msg_info.get("tg_msg_id"),
                "direction": direction,
                "content": content,
                "msg_type": msg_type,
                "media_file_id": msg_info.get("media_file_id"),
                "media_local_path": msg_info.get("media_local_path"),
                "created_at": msg_info.get("created_at"),
            }

            msg_db_id = await self.db_messages.add_message(data_message)
            if not msg_db_id:
                logger.error(f"[Ingest] Не удалось сохранить сообщение для chat_id={chat_id}")
                return

            # 3. VECTORIZATION 
            if content and not content.startswith("[") and msg_db_id:
                task = asyncio.create_task(self._background_vectorize(message_id=msg_db_id, content=content))
                self.background_tasks.add(task)
                task.add_done_callback(self.background_tasks.discard)


        # 4. CHECKING WHITE/BLACK LIST (пропускается для Избранного)
        ##### ПОЗЖЕ ПРИДУМАЮ ЛОГИКУ #####
        # user_data = await self.db_users.db_get_user(tg_id=sender_id)
        # if not user_data:
        #     logger.warning(f"[Ingest] Пользователь tg_id={sender_id} не найден в БД при проверке списков.")
        #     return
        # if user_data.get("is_blocked") or user_data.get("is_whitelisted"):
        #     logger.info(f"[Ingest] Сообщение от tg_id={sender_id} проигнорировано (Blacklist/Whitelist).")
        #     return
        

        # 5. ADD QUEUE RESPONSE (входящие + исходящие + избранное)
        task_payload = {
            "chat_id": chat_id,
            "sender_id": sender_id,
            "recipient_id": recipient_id,

            "tg_msg_id": msg_info.get("tg_msg_id"),
            "msg_db_id": msg_db_id,

            "username": username,
            "content": content,
            "direction": direction,
            "msg_type": msg_type,
            "created_at": msg_info.get("created_at")
        }

        # В очередь передаем, далее вылавливаем в jumis/jumis_agent/jumis_agent.py
        await self.queue_new_mess.put(task_payload)
        logger.info(f"[IngestWorker] Сообщение [{msg_db_id}] передано в queue_new_mess")

        logger.info(f"💾 [Ingest] Сообщение id={msg_db_id} (chat_id={chat_id}, type={msg_type}) успешно обработано.")




