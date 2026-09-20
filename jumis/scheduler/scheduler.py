# jumis/scheduler/scheduler.py
import asyncio
from datetime import datetime, timezone, timedelta
from croniter import croniter
from typing import Any, Dict, Optional
from config import ADMIN_ID
from logs.set_logger import set_logger
logger = set_logger(name="scheduler")





class SmartScheduler:
    """
    Асинхронный планировщик задач.
    Отслеживает ближайшие по времени задачи в БД и выполняет их в точный момент.
    """

    def __init__(self, db_tasks):
        self.db_tasks = db_tasks
        self.agent = None
        self.bot = None
        self.admin_id = ADMIN_ID

        # Две изолированные очереди
        self.agent_queue = asyncio.Queue() # пояснения
        self.cron_queue = asyncio.Queue() # пояснения

        self._workers = []
        self._notify_event = asyncio.Event() # пояснения

    def set_agent(self, agent):
            self.agent = agent

    def set_bot(self, bot):
            self.bot = bot

    def notify_new_task(self):
        """Сбрасывает таймер ожидания главного цикла при создании новой задачи."""
        logger.info("[Scheduler] Получен сигнал о новой задаче. Сброс ожидания.")
        self._notify_event.set()


    async def run(self):
        """Единая точка запуска фоновых процессов планировщика."""

        # Очищаем подвисшие и старые задачи перед запуском цикла
        await self.db_tasks.cleanup_stale_tasks(expire_hours=12)

        self._workers = [
            asyncio.create_task(self._agent_worker(), name="Scheduler-AgentWorker"),
            asyncio.create_task(self._cron_worker(), name="Scheduler-CronWorker"),
            asyncio.create_task(self._main_loop(), name="Scheduler-MainLoop"),
        ]
        logger.info("[SmartScheduler] Запущен с изолированными очередями.")


    async def _main_loop(self):
        """Основной цикл планировщика."""
        logger.info("[Scheduler] Фоновый демон планировщика запущен.")

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
            await asyncio.wait_for(self._notify_event.wait(), timeout=timeout)
            logger.info("[Scheduler] Таймер пересчитан из-за добавления новой задачи.")
        except asyncio.TimeoutError:
            pass
        finally:
            self._notify_event.clear()


    async def _execute_task(self, task: Dict[str, Any]):
        """Маршрутизация и запуск выполнения задачи."""
        id_task = task["id"]
        now = datetime.now(timezone.utc)
        scheduled_at = task["scheduled_at"]
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        delay = (scheduled_at - now).total_seconds()

        # 1. Если задача просрочена больше чем на 1 час (3600 сек) — сразу в 'expired'
        if delay < -3600:
            logger.warning(
                f"[Scheduler] Задача #{id_task} ('{task['title']}') просрочена на {abs(delay)/60:.1f} мин. "
                f"Смена статуса на 'expired'."
            )
            await self.db_tasks.db_update_task({"id": id_task, "status": "expired"})
            return

        logger.info(f"[Scheduler] Исполнение задачи #{id_task}: '{task['title']}'")

        # Меняем статус задачи на 'running'
        if not await self.db_tasks.db_update_task({"id":id_task, "status": "running"}):
            logger.warning(
                f"[Scheduler] Пропуск задачи #{id_task}: не удалось сменить статус на 'running'."
            )
            return

        # Маршрутизация по типам задач
        if task.get("task_type") == "system_cron":
            await self.cron_queue.put(task)
        else:
            await self.agent_queue.put(task)


    async def execute_system_cron(self, task: Dict[str, Any]):
        """Заглушка/обработчик системных крон-задач."""
        logger.info(f"[CronWorker] Выполнение системной задачи #{task.get('id')}: {task.get('title')}")
        # Здесь будет логика системных скриптов



    async def _agent_worker(self):
        """
        Воркер для работы с Юмис.
        
        ПРИМЕЧАНИЕ (MVP): Логика «Дятла» (requires_ack / nagging) временно закомментирована 
        и отключена для упрощения архитектуры и исключения гонок состояний. 
        Сейчас работает чистая стейт-машина:
        1. Крон-задачи (has_cron=True) -> Авто-пересчет даты через croniter -> pending.
        2. Разовые задачи -> Завершение -> completed.
        """
        while True:
            task = await self.agent_queue.get()
            t_id = task.get("id")
            task_type = task.get("task_type", "reminder")

            # --- ДЯТЕЛ / NAG-РЕЖИМ (ПОЛЯ БД СОХРАНЕНЫ ДЛЯ СТРУКТУРЫ) ---
            # Временно отключено для MVP. Поля оставлены для наглядности структуры таблицы БД.
            # В будущем logic «Дятла» будет выделена в отдельный локальный воркер/класс.
            #
            # requires_ack = task.get("requires_ack", False)
            # is_ack = task.get("is_ack_received", False)
            # current_count = task.get("current_nag_count", 0) + 1
            # max_attempts = task.get("max_nag_attempts", 5)
            # interval_min = task.get("repeat_interval_minutes") or 5
            # -------------------------------------------------------------

            has_cron = bool(task.get("cron_expression"))

            # Получаем ID получателя с фолбэком на admin_id
            recipient_id = task.get("tg_id") or self.admin_id

            reminder_types = ["reminder", "alarm"]
            action_types = ["agent_action", "system_cron"]

            try:
                if not self.agent:
                    logger.error("[AgentWorker] Экземпляр Agent не установлен (set_agent)!")
                    continue

                # 1. Личное напоминание владельцу (напрямую в этот чат)
                if task_type in reminder_types and recipient_id == self.admin_id:
                    prompt_to_agent = (
                        f"[SYSTEM TRIGGER: {task_type.upper()} #{t_id} FOR OWNER]\n"
                        f"NOTE TO AGENT: This is an internal background alarm for Alex.\n\n"
                        f"• Title: '{task.get('title')}'\n"
                        f"• Instruction: {task.get('agent_instruction')}\n\n"
                        f"ACTION REQUIRED:\n"
                        f"Deliver this reminder directly to Alex in this chat using your normal persona.\n"
                        f"DO NOT invoke external messaging tools or Telethon."
                    )

                # 2. Напоминание/сообщение стороннему пользователю
                elif task_type in reminder_types and recipient_id != self.admin_id:
                    prompt_to_agent = (
                        f"[SYSTEM TRIGGER: {task_type.upper()} #{t_id} FOR EXTERNAL USER]\n"
                        f"NOTE TO AGENT: Scheduled message for user ID: {recipient_id}.\n\n"
                        f"• Title: '{task.get('title')}'\n"
                        f"• Instruction: {task.get('agent_instruction')}\n\n"
                        f"ACTION REQUIRED:\n"
                        f"Formulate and send the message to user {recipient_id} following this instruction.\n"
                        f"Without confirmation"
                    )

                # 3. Автономные действия агента (вызов инструментов / скриптов)
                elif task_type in action_types:
                    prompt_to_agent = (
                        f"[SYSTEM TRIGGER: AGENT ACTION #{t_id}]\n"
                        f"NOTE TO AGENT: Executing autonomous background task.\n\n"
                        f"• Title: '{task.get('title')}'\n"
                        f"• Instruction: {task.get('agent_instruction')}\n\n"
                        f"ACTION REQUIRED:\n"
                        f"Execute the task using appropriate tools if required, then report status back."
                    )
                else:
                    logger.warning(f"[AgentWorker] Неизвестный task_type: {task_type}")
                    continue

                # 4. Запускаем Агента
                await self.agent.process_agent_request(
                    chat_id=recipient_id, 
                    prompt_text=prompt_to_agent
                )

                # 5. Обновляем таску ТОЛЬКО после успешного выполнения воркером
                now = datetime.now(timezone.utc)

                if has_cron:
                    # Повторяющаяся Крон-задача: считаем следующий запуск
                    next_scheduled_at = croniter(task["cron_expression"], now).get_next(datetime)
                    logger.info(f"[AgentWorker] Крон #{t_id}: следующий запуск запланирован на {next_scheduled_at}")
                    
                    task_data = {
                        "id": t_id,
                        "scheduled_at": next_scheduled_at,
                        "status": "pending"  # Возвращаем в очередь на будущий круг
                    }
                else:
                    # Разовая задача завершается окончательно
                    logger.info(f"[AgentWorker] Разовая задача #{t_id} успешно завершена.")
                    task_data = {
                        "id": t_id,
                        "status": "completed"
                    }

                await self.db_tasks.db_update_task(task_data)

                # Если задача вернулась в pending (на следующий крон-день) — пинаем петлю!
                if task_data.get("status") == "pending":
                    self.notify_new_task()

            except Exception as e:
                logger.error(f"[AgentWorker] Ошибка выполнения задачи #{t_id}: {e}", exc_info=True)
                await self.db_tasks.db_update_task({"id": t_id, "status": "failed"})
                self.notify_new_task()
            finally:
                self.agent_queue.task_done()



    # async def _agent_worker(self):
    #     """Воркер для работы с Юмис."""
    #     while True:
    #         task = await self.agent_queue.get()
    #         t_id = task.get("id")
    #         task_type = task.get("task_type", "reminder")

    #         requires_ack = task.get("requires_ack", False)
    #         is_ack = task.get("is_ack_received", False)
    #         current_count = task.get("current_nag_count", 0) + 1
    #         max_attempts = task.get("max_nag_attempts", 5)
    #         interval_min = task.get("repeat_interval_minutes") or 5
    #         has_cron = bool(task.get("cron_expression"))

    #         # Получаем ID получателя с фолбэком на admin_id
    #         recipient_id = task.get("tg_id") or self.admin_id

    #         reminder_types = ["reminder", "alarm"]
    #         action_types = ["agent_action", "system_cron"]

    #         try:
    #             if not self.agent:
    #                 logger.error("[AgentWorker] Экземпляр Agent не установлен (set_agent)!")
    #                 continue

    #             # 1. Личное напоминание владельцу (напрямую в этот чат)
    #             if task_type in reminder_types and recipient_id == self.admin_id:
    #                 prompt_to_agent = (
    #                     f"[SYSTEM TRIGGER: {task_type.upper()} #{t_id} FOR OWNER]\n"
    #                     f"NOTE TO AGENT: This is an internal background alarm for Alex.\n\n"
    #                     f"• Title: '{task.get('title')}'\n"
    #                     f"• Instruction: {task.get('agent_instruction')}\n\n"
    #                     f"ACTION REQUIRED:\n"
    #                     f"Deliver this reminder directly to Alex in this chat using your normal persona.\n"
    #                     f"DO NOT invoke external messaging tools or Telethon."
    #                 )

    #             # 2. Напоминание/сообщение стороннему пользователю
    #             elif task_type in reminder_types and recipient_id != self.admin_id:
    #                 prompt_to_agent = (
    #                     f"[SYSTEM TRIGGER: {task_type.upper()} #{t_id} FOR EXTERNAL USER]\n"
    #                     f"NOTE TO AGENT: Scheduled message for user ID: {recipient_id}.\n\n"
    #                     f"• Title: '{task.get('title')}'\n"
    #                     f"• Instruction: {task.get('agent_instruction')}\n\n"
    #                     f"ACTION REQUIRED:\n"
    #                     f"Formulate and send the message to user {recipient_id} following this instruction.\n"
    #                     f"Without confirmation"
    #                 )

    #             # 3. Автономные действия агента (вызов инструментов / скриптов)
    #             elif task_type in action_types:
    #                 prompt_to_agent = (
    #                     f"[SYSTEM TRIGGER: AGENT ACTION #{t_id}]\n"
    #                     f"NOTE TO AGENT: Executing autonomous background task.\n\n"
    #                     f"• Title: '{task.get('title')}'\n"
    #                     f"• Instruction: {task.get('agent_instruction')}\n\n"
    #                     f"ACTION REQUIRED:\n"
    #                     f"Execute the task using appropriate tools if required, then report status back."
    #                 )
    #             else:
    #                 logger.warning(f"[AgentWorker] Неизвестный task_type: {task_type}")
    #                 continue

    #             # 4. Запускаем Агента
    #             await self.agent.process_agent_request(
    #                 chat_id=recipient_id, 
    #                 prompt_text=prompt_to_agent
    #             )

    #             # 5. Обновляем таску ТОЛЬКО после успешного выполнения воркером
    #             task_data = {}
    #             now = datetime.now(timezone.utc)


    #             # -------------------------------------------------------------------------
    #             # ШАГ 1: Если требуется ACK и лимит повторов не исчерпан -> Режим ДЯТЛА
    #             # -------------------------------------------------------------------------
    #             if requires_ack and not is_ack and current_count < max_attempts:
    #                 next_nag_at = now + timedelta(minutes=interval_min)
    #                 logger.info(
    #                     f"[AgentWorker] Дятел #{t_id}: повтор {current_count}/{max_attempts} "
    #                     f"через {interval_min} мин (в {next_nag_at.strftime('%H:%M:%S')})."
    #                 )
    #                 task_data = {
    #                     "id": t_id,
    #                     "current_nag_count": current_count,
    #                     "scheduled_at": next_nag_at,
    #                     "status": "pending"
    #                 }

    #             # -------------------------------------------------------------------------
    #             # ШАГ 2: Дятел завершён (получен ACK или исчерпаны попытки) / Обычный запуск
    #             # -------------------------------------------------------------------------
    #             else:
    #                 if has_cron:
    #                     # Считаем следующий запуск по Крону (например, на завтра в 01:15)
    #                     next_scheduled_at = croniter(task["cron_expression"], now).get_next(datetime)
    #                     logger.info(f"[AgentWorker] Крон #{t_id}: следующий запуск запланирован на {next_scheduled_at}")
                        
    #                     task_data = {
    #                         "id": t_id,
    #                         "scheduled_at": next_scheduled_at,
    #                         "current_nag_count": 0,    # Сбрасываем счетчик дятла для следующего дня
    #                         "is_ack_received": False,  # Сбрасываем флаг подтверждения
    #                         "status": "pending"        # Задача живет дальше
    #                     }
    #                 else:
    #                     # Разовая задача завершается окончательно
    #                     final_status = "completed" if (is_ack or not requires_ack) else "expired"
    #                     task_data = {
    #                         "id": t_id,
    #                         "status": final_status
    #                     }

    #             await self.db_tasks.db_update_task(task_data)

    #             # Если задача вернулась в pending (на повтор дятла или на следующий крон-день) — пинаем петлю!
    #             if task_data.get("status") == "pending":
    #                 self.notify_new_task()


    #         except Exception as e:
    #             logger.error(f"[AgentWorker] Ошибка выполнения задачи #{t_id}: {e}", exc_info=True)
    #             await self.db_tasks.db_update_task({"id": t_id, "status": "failed"})
    #             self.notify_new_task()
    #         finally:
    #             self.agent_queue.task_done()


    async def _cron_worker(self):
        """Воркер для системных скриптов."""
        while True:
            task = await self.cron_queue.get()
            t_id = task.get("id")
            try:
                await self.execute_system_cron(task)
                
                if not task.get("cron_expression"):
                    await self.db_tasks.db_update_task({"id": t_id, "status": "completed"})
            except Exception as e:
                logger.error(f"[CronWorker] Ошибка выполнения cron-задачи #{t_id}: {e}", exc_info=True)
                await self.db_tasks.db_update_task({"id": t_id, "status": "failed"})
            finally:
                self.cron_queue.task_done()




    # async def _handle_system_cron(self, task: Dict[str, Any]):
    #     """Обработка системных сценариев (уборка БД, снятие метрик и т.д.)."""
    #     logger.info(f"[Scheduler System] Выполнение системного скрипта: {task['title']}")


    # async def _handle_user_task(self, task: Dict[str, Any]):
    #     """Обработка пользовательских напоминаний и заданий для LLM."""
    #     logger.info(
    #         f"[Scheduler User] Выполнение пользовательской задачи для tg_id={task['tg_id']}: {task['title']}"
    #     )


