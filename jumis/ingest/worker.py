# jumis/ingest/worker.py
import asyncio
from logs.set_logger import set_logger

logger = set_logger(name="ingest")


class IngestionWorker:
    def __init__(self, db, queue: asyncio.Queue):
        self.db = db
        self.queue = queue

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

    async def _process_and_save(self, data: dict):
        """Внутренняя логика разбора и записи в БД."""
        user_info = data.get("user", {})
        msg_info = data.get("message", {})

        # 1. Гарантируем наличие пользователя в users
        # user_id = await self.db.get_or_create_user(user_info)

        # 2. Пишем сообщение в messages
        # await self.db.add_message(user_id=user_id, **msg_info)

        print(f"💾 [Ingest] Сообщение от chat_id={msg_info.get('chat_id')} обработано.")
        print(f"\n{data}\n")