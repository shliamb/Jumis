# jumis/pgbackup/task.py
"""
Крон ночного бэкапа базы в таблице scheduled_tasks.

Время — 04:30: на час позже ночной чистки (03:30), чтобы дамп снимался
уже с убранной базы. Механику (создание, оживление, защита от удаления)
даёт utils/system_cron.py.
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


TITLE = register_system_title("Ночной бэкап базы данных")
CRON = "30 4 * * *"

INSTRUCTION = (
    "System maintenance. Create a logical pg_dump backup of the database and "
    "rotate old copies (pgbackup.nightly). Do not delete this task."
)


def is_system_task(task: dict | None) -> bool:
    return is_system_cron(task)


def _next_run():
    """Ближайшее срабатывание крона бэкапа."""
    return next_run(CRON)


async def find_task(db_tasks) -> dict | None:
    """Ищет строку крона бэкапа в БД."""
    return await find_system_task(db_tasks, TITLE)


async def ensure_system_task(db_tasks, scheduler=None) -> bool:
    """Гарантирует, что крон бэкапа существует и живой."""
    return await ensure_system_cron(
        db_tasks=db_tasks,
        title=TITLE,
        cron_expression=CRON,
        instruction=INSTRUCTION,
        scheduler=scheduler,
        stale_minutes=STALE_MINUTES,
    )
