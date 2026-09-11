# jumis/scheduler/scheduler.py
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from logs.set_logger import set_logger
logger = set_logger(name="scheduler")





class SmartScheduler:
    """
    Асинхронный планировщик задач.
    Отслеживает ближайшие по времени задачи в БД и выполняет их в точный момент.
    """

    def __init__(self, db_tasks, bot=None):
        """
        Args:
            db_tasks: Экземпляр класса работы с таблицей scheduled_tasks.
            bot: Экземпляр Aiogram Bot (опционально, для прямой отправки уведомлений).
        """
        self.db_tasks = db_tasks
        self.bot = bot
        self.new_task_event = asyncio.Event()


    def notify_new_task(self):
        """
        Уведомляет планировщик о создании новой задачи в БД.
        Прерывает текущее ожидание и заставляет планировщик пересчитать таймер.
        """
        logger.info("[Scheduler] Получен сигнал о новой задаче. Сброс ожидания.")
        self.new_task_event.set()


    async def run(self):
        """Основной бесконечный цикл планировщика."""
        logger.info("[Scheduler] Фоновый демон планировщика успешно запущен")

        while True:
            try:
                # 1. Забираем самую ближайшую pending-задачу
                task = await self.db_tasks.get_pending_task()

                # 2. Если задач нет — спим 1 час (или пока не дернут notify_new_task)
                if not task:
                    await self._sleep_or_event(3600.0)
                    continue

                now = datetime.now(timezone.utc)
                scheduled_at = task["scheduled_at"]

                # Приводим время из БД к UTC, если оно пришли без tzinfo
                if scheduled_at.tzinfo is None:
                    scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

                delay = (scheduled_at - now).total_seconds()

                # 3. Если время задачи уже наступило (или просрочено) — исполняем
                if delay <= 0:
                    await self._execute_task(task)
                    continue

                # 4. Спим ровно до момента наступления задачи или нового Event
                logger.info(
                    f"[Scheduler] Задача #{task['id']} ({task['title']}) запланирована через {delay:.1f} сек."
                )
                await self._sleep_or_event(delay)

            except asyncio.CancelledError:
                logger.info("[Scheduler] Остановка цикла планировщика (Cancelled).")
                break
            except Exception as e:
                logger.error(
                    f"[Scheduler] Критическая ошибка в основном цикле: {e}",
                    exc_info=True,
                )
                await asyncio.sleep(5)


    async def _sleep_or_event(self, timeout: float):
        """
        Блокирует выполнение на `timeout` секунд.
        Прерывается раньше, если вызван `notify_new_task()`.
        """
        try:
            await asyncio.wait_for(self.new_task_event.wait(), timeout=timeout)
            logger.info("[Scheduler] Таймер пересчитан из-за добавления новой задачи.")
        except asyncio.TimeoutError:
            pass
        finally:
            self.new_task_event.clear()


    async def _execute_task(self, task: Dict[str, Any]):
        """Маршрутизация и запуск выполнения задачи."""
        id_task = task["id"]
        logger.info(f"[Scheduler] Исполнение задачи #{id_task}: '{task['title']}'")

        # Меняем статус задачи на 'running'
        if not await self.db_tasks.db_update_task({"id":id_task, "status": "running"}):
            logger.warning(
                f"[Scheduler] Пропуск задачи #{id_task}: не удалось сменить статус на 'running'."
            )
            return

        try:
            # Маршрутизация по типам задач
            if task["task_type"] == "system_cron":
                await self._handle_system_cron(task)
            else:
                await self._handle_user_task(task)

            # Завершаем задачу
            if task["cron_expression"]:
                # TODO: Перерасчет следующего запуска для периодических задач
                pass
            elif task["requires_ack"] and not task["is_ack_received"]:
                # TODO: Логика режима «Дятел»
                pass
            else:
                await self.db_tasks.db_update_task({"id":id_task, "status": "completed"})

        except Exception as e:
            logger.error(
                f"[Scheduler] Ошибка при обработке задачи #{id_task}: {e}",
                exc_info=True,
            )


    async def _handle_system_cron(self, task: Dict[str, Any]):
        """Обработка системных сценариев (уборка БД, снятие метрик и т.д.)."""
        logger.info(f"[Scheduler System] Выполнение системного скрипта: {task['title']}")


    async def _handle_user_task(self, task: Dict[str, Any]):
        """Обработка пользовательских напоминаний и заданий для LLM."""
        logger.info(
            f"[Scheduler User] Выполнение пользовательской задачи для tg_id={task['tg_id']}: {task['title']}"
        )

    # # Когда пользователь или Юмис создает новую задачу:
    # async def create_reminder_handler(...):
    #     # 1. Записали в БД
    #     await db_tasks.add_task(task_data)
        
    #     # 2. Дёрнули планировщик, чтобы он пересчитал таймер!
    #     schedul.notify_new_task()