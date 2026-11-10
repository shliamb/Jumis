# jumis/cleaner/task.py
"""
Крон ночной уборки в таблице scheduled_tasks.

Вся "железобетонность" (создание, оживление, защита от удаления, уважение к
'cancelled') реализована в utils/system_cron.py — здесь только параметры
этого модуля: название, расписание и инструкция для агента.
"""
from utils.system_cron import (
    STALE_MINUTES,
    TASK_TYPE,
    ensure_system_cron,
    find_system_task,
    is_system_cron,
    next_run,
    register_system_title,
)


TITLE = register_system_title("Ночная чистка и самаризация БД")
CRON = "30 3 * * *"

INSTRUCTION = (
    "System maintenance. Remove junk messages, then compress the oldest "
    "history batches into digests (cleaner.nightly). Do not delete this task."
)


def is_system_task(task: dict | None) -> bool:
    """Системная задача — та, что модули создали сами (без владельца)."""
    return is_system_cron(task)


def _next_run():
    """Ближайшее срабатывание крона уборки."""
    return next_run(CRON)


async def find_task(db_tasks) -> dict | None:
    """Ищет строку крона уборки в БД."""
    return await find_system_task(db_tasks, TITLE)


async def ensure_system_task(db_tasks, scheduler=None) -> bool:
    """Гарантирует, что крон уборки существует и живой."""
    return await ensure_system_cron(
        db_tasks=db_tasks,
        title=TITLE,
        cron_expression=CRON,
        instruction=INSTRUCTION,
        scheduler=scheduler,
        stale_minutes=STALE_MINUTES,
    )
