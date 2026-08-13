# response/worker.py
import asyncio
from logs.set_logger import set_logger

logger = set_logger(name="response")




class ResponseWorker:
    def __init__(self, queue_response: asyncio.Queue, ai_agent=None, telethon_client=None):
        self.queue = queue_response
        self.ai_agent = ai_agent
        self.telethon_client = telethon_client
        self._is_running = False

    async def run(self):
        self._is_running = True
        logger.info("[ResponseWorker] Воркер обработки ответов запущен.")
        
        while self._is_running:
            try:
                # Ждем следующую задачу на ответ
                task = await self.queue.get()
                
                await self._handle_response_task(task)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ResponseWorker] Ошибка обработки задачи: {e}", exc_info=True)
            finally:
                self.queue.task_done()

    async def _handle_response_task(self, task: dict):
        tg_id = task["tg_id"]
        content = task["content"]

        print(f"[ResponseWorker] Обработка запроса от tg_id={tg_id}: '{content[:30]}...'")
        logger.info(f"[ResponseWorker] Обработка запроса от tg_id={tg_id}: '{content[:30]}...'")

        # Твоя бизнес-логика ответов:
        # 1. Сгенерировать ответ через ИИ / отправить уведомление в бота
        # 2. Отправить ответ пользователю через Telethon