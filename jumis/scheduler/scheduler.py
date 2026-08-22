# jumis/scheduler/scheduler.py
import asyncio
from datetime import datetime, timezone
from logs.set_logger import set_logger
logger = set_logger(name="scheduler")





class SmartScheduler:
    
    def __init__(self, db_pool):
        self.db = db_pool
        self.new_task_event = asyncio.Event()

    def notify_new_task(self):
        """Вызывается при создании любой новой задачи из кода бота/API"""
        self.new_task_event.set()

    async def run(self):
        logger.info("[Scheduler] Планировщик запущен")
        
        while True:
            try:
                # 1. Забираем самую ближайшую pending-задачу
                task = await self.db.fetchrow("""
                    SELECT id, execute_at, payload 
                    FROM scheduled_tasks 
                    WHERE status = 'pending' 
                    ORDER BY execute_at ASC 
                    LIMIT 1
                """)

                # 2. Если задач вообще нет — спим 1 час (или пока не дернут notify_new_task)
                if not task:
                    await self._sleep_or_event(3600)
                    continue

                now = datetime.now(timezone.utc)
                execute_at = task['execute_at']
                delay = (execute_at - now).total_seconds()

                # 3. Если время задачи уже пришло (или прошло) — выполняем
                if delay <= 0:
                    await self._execute_task(task)
                    continue

                # 4. Если задача в будущем — спим ровно delay секунд
                # Если в это время создадут срочную задачу, event.set() прервет сон
                logger.info(f"[Scheduler] Следующая задача #{task['id']} через {delay:.1f} сек.")
                await self._sleep_or_event(delay)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Scheduler] Ошибка в цикле: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _sleep_or_event(self, timeout: float):
        """Спит timeout секунд ИЛИ просыпается раньше, если подняли new_task_event"""
        try:
            await asyncio.wait_for(self.new_task_event.wait(), timeout=timeout)
            # Если мы тут — значит сработал event.set() (добавили новую задачу)
            logger.info("[Scheduler] Поступила новая задача, пересчитываем таймер...")
        except asyncio.TimeoutError:
            # Тайм-аут истёк — значит пора выполнять запланированную задачу
            pass
        finally:
            self.new_task_event.clear()

    async def _execute_task(self, task):
        task_id = task['id']
        logger.info(f"[Scheduler] Запуск задачи #{task_id}")
        
        # Меняем статус на processing, чтобы другие воркеры не подхватили
        await self.db.execute("UPDATE scheduled_tasks SET status = 'processing' WHERE id = $1", task_id)
        
        # ... Твоя бизнес-логика выполнения ...
        
        await self.db.execute("UPDATE scheduled_tasks SET status = 'completed' WHERE id = $1", task_id)