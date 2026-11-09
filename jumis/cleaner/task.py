# jumis/cleaner/task.py
"""
Железобетонный крон ночной уборки в таблице scheduled_tasks.

Правила:
  * строка создаётся один раз и восстанавливается при старте, если пропала;
  * ей нельзя поменять тип/название/cron (агентский update_task это запрещает);
  * её нельзя удалить (del_task отказывает для task_type='system_cron');
  * если бот лежал и задача протухла в 'expired' — оживляем;
  * 'cancelled' не трогаем: это осознанная пауза.
"""
from datetime import datetime

from croniter import croniter

from utils.common import get_now_datetime, to_cleen
from logs.set_logger import logger


TITLE = "Ночная чистка и самаризация БД"
CRON = "30 3 * * *"
TASK_TYPE = "system_cron"

INSTRUCTION = (
    "System maintenance. Remove junk messages, then compress the oldest "
    "history batches into digests (cleaner.nightly). Do not delete this task."
)

# Если задача висит в прошедшем времени дольше этого срока — пересчитываем время.
STALE_MINUTES = 12 * 60


def is_system_task(task: dict | None) -> bool:
    """Системная задача — та, что уборщик создал сам."""
    if not task:
        return False
    return task.get("task_type") == TASK_TYPE or task.get("title") == TITLE


def _next_run() -> datetime:
    """Ближайшее срабатывание крона по локальным настенным часам."""
    return croniter(CRON, get_now_datetime()).get_next(datetime)


async def find_task(db_tasks) -> dict | None:
    """Ищет строку крона в БД (и по типу, и по названию — на случай переименования)."""
    rows = await db_tasks.db_search_tasks(status=None, limit=500)
    for task in rows:
        if task.get("task_type") == TASK_TYPE or task.get("title") == TITLE:
            return task
    return None


async def ensure_system_task(db_tasks, scheduler=None) -> bool:
    """
    Гарантирует, что крон уборки существует и живой.
    Идемпотентно: можно звать при каждом старте.
    """
    if not db_tasks:
        logger.error("[Cleaner] db_tasks не передан — крон уборки не проверить.")
        return False

    try:
        task = await find_task(db_tasks)
        next_run = _next_run()

        if not task:
            task_id = await db_tasks.db_add_task({
                "tg_id": None,
                "task_type": TASK_TYPE,
                "title": TITLE,
                "agent_instruction": INSTRUCTION,
                "scheduled_at": next_run,
                "cron_expression": CRON,
                "status": "pending",
            })
            if task_id:
                logger.info("[Cleaner] Системный крон уборки создан: #%s, след. запуск %s", task_id, next_run)
                if scheduler:
                    scheduler.notify_new_task()
                return True
            logger.error("[Cleaner] Не удалось создать системный крон уборки.")
            return False

        task_id = task.get("id")
        status = task.get("status")

        if status == "cancelled":
            logger.warning("[Cleaner] Крон уборки #%s на паузе (cancelled) — не трогаем.", task_id)
            return True

        if status in ("expired", "failed"):
            await db_tasks.db_update_task({
                "id": task_id,
                "status": "pending",
                "scheduled_at": next_run,
            })
            logger.warning("[Cleaner] Крон уборки #%s был в '%s' — оживлён, след. запуск %s",
                           task_id, status, next_run)
            if scheduler:
                scheduler.notify_new_task()
            return True

        # Строка жива, но время уехало в прошлое (бот лежал / часы сбились)
        scheduled_at = task.get("scheduled_at")
        if scheduled_at:
            try:
                lag = (get_now_datetime() - to_cleen(scheduled_at)).total_seconds() / 60.0
                if lag > STALE_MINUTES:
                    await db_tasks.db_update_task({"id": task_id, "scheduled_at": next_run})
                    logger.warning("[Cleaner] Крон уборки #%s просрочен на %d мин — время пересчитано.",
                                   task_id, int(lag))
                    if scheduler:
                        scheduler.notify_new_task()
            except Exception as e:
                logger.warning("[Cleaner] Не удалось проверить время крона #%s: %s", task_id, e)

        return True

    except Exception as e:
        logger.error("[Cleaner] Ошибка проверки системного крона: %s", e, exc_info=True)
        return False
