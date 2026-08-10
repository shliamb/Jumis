# jumis/ingest/worker.py
import asyncio
from vector import embedder
from logs.set_logger import set_logger

logger = set_logger(name="ingest")




class IngestionWorker:
    def __init__(self, dbmessages, queue: asyncio.Queue):
        self.dbmessages = dbmessages
        self.embedder = embedder
        self.queue = queue
        # Множество для хранения ссылок на активные фоновые задачи (защита от GC в Python 3.11+)
        self.background_tasks = set()



    async def run(self):
        """Основной фоновый цикл обработки сообщений из очереди."""
        logger.info("[IngestionWorker] Запущен и ожидает сообщения...")
        try:
            while True:
                # Ждем появление нового элемента в очереди (не забирает CPU)
                message_data = await self.queue.get()

                try:
                    await self._process_and_save(message_data)
                except Exception as e:
                    logger.error(f"[IngestionWorker] Ошибка сохранения: {e}", exc_info=True)
                finally:
                    # Обязательно сообщаем очереди, что задача обработана
                    self.queue.task_done()

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

            data = {
                "id": message_id,
                "embedding": embedding
            }

            # 2. Обновляем вектор в базе
            success = await self.dbmessages.update_embedding(message_id=message_id, embedding=embedding)
            if success:
                logger.info(f"[BG Embedding] Message ID {message_id} successfully vectorized and updated.")
            else:
                logger.error(f"[BG Embedding] Failed to update embedding in DB for message_id={message_id}")

        except Exception as e:
            logger.error(f"[BG Embedding] Critical error vectorizing message_id={message_id}: {e}", exc_info=True)



    async def _process_and_save(self, data: dict):
        """Внутренняя логика разбора и записи в БД."""

        msg_info = data.get("message", {})
        
        chat_id = msg_info.get("chat_id")
        if not chat_id:
            logger.warning("[Ingest] Пропущено сообщение: отсутствует chat_id.")
            return

        # Собираем словарь для DBMessages
        msg_type = msg_info.get("msg_type", "text")
        content = msg_info.get("content")
        data_message = {
            "tg_id": chat_id,
            "tg_msg_id": msg_info.get("tg_msg_id"),
            "role": msg_info.get("role", "user"),
            "content": content,
            "msg_type": msg_type,
            "media_file_id": msg_info.get("media_file_id"),
            "media_local_path": msg_info.get("media_local_path"),
            "created_at": msg_info.get("created_at"),  # Сохраняем точное время отправки в Telegram
        }

        message_id = await self.dbmessages.add_message(data_message)
        if not message_id:
            logger.error(f"[Ingest] Не удалось сохранить сообщение для chat_id={chat_id}")
            return

        if msg_type == "text":
            # Регистрируем фоновую задачу в множестве, чтобы GC её не уничтожил
            task = asyncio.create_task(self._background_vectorize(message_id=message_id, content=content))
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)

        if msg_type == "voice":
            print("soon..")
        

        logger.info(f"💾 [Ingest] Сообщение id={message_id} (chat_id={chat_id}) сохранено.")
        #print(f"💾 [Ingest] Сообщение id={message_id} (chat_id={chat_id}) сохранено.")





