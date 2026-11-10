# jumis/utils/system_cron.py
"""
"Железобетонные" системные кроны в таблице scheduled_tasks.

Один и тот же набор правил нужен нескольким модулям (чистка истории, бэкап БД),
поэтому логика живёт здесь, а модули передают только название, расписание
и инструкцию для агента:

  * строка создаётся один раз и восстанавливается при старте, если пропала;
  * агенту нельзя удалить её (del_task отказывает для task_type='system_cron');
  * нельзя поменять название/cron/тип (update_task разрешает только status);
  * если бот лежал и задача протухла в 'expired'/'failed' — оживляем;
  * 'cancelled' не трогаем: это осознанная пауза владельца.

Ключ файла — `task_type='system_cron'`: именно по нему планировщик отправляет
задачу не агенту, а зарегистрированному обработчику (`register_system_task`).
"""
from datetime import datetime

from croniter import croniter

from logs.set_logger import logger
from utils.common import get_now_datetime, to_cleen


TASK_TYPE = "system_cron"

# Если задача висит в прошедшем времени дольше этого срока — пересчитываем время.
STALE_MINUTES = 12 * 60

# Названия системных кронов, которые нужно защищать от агента.
# Модули регистрируют их при импорте: так задача остаётся защищённой, даже если
# её task_type кто-то испортил вручную.
PROTECTED_TITLES: set[str] = set()


def register_system_title(title: str) -> str:
    """Регистрирует название крона модуля и возвращает его же (удобно для TITLE = ...)."""
    if title:
        PROTECTED_TITLES.add(title)
    return title


def is_system_cron(task: dict | None) -> bool:
    """Системная задача — по типу или по зарегистрированному названию."""
    if not task:
        return False
    return task.get("task_type") == TASK_TYPE or task.get("title") in PROTECTED_TITLES


def next_run(cron_expression: str) -> datetime:
    """Ближайшее срабатывание крона по локальным настенным часам."""
    return croniter(cron_expression, get_now_datetime()).get_next(datetime)


async def find_system_task(db_tasks, title: str) -> dict | None:
    """
    Ищет строку крона по названию.

    Именно по названию, а не по task_type: системных кронов теперь несколько
    (уборка, бэкап), и по типу они неразличимы — иначе один модуль «усыновил»
    бы чужую строку. Название стабильно: агенту менять его запрещено.
    """
    rows = await db_tasks.db_search_tasks(status=None, limit=500)
    for task in rows:
        if task.get("title") == title:
            return task
    return None


async def ensure_system_cron(
        db_tasks,
        title: str,
        cron_expression: str,
        instruction: str,
        scheduler=None,
        stale_minutes: int = STALE_MINUTES,
) -> bool:
    """
    Гарантирует, что крон существует и живой. Идемпотентно: можно звать при каждом старте.
    Возвращает True, если строка на месте (создана, оживлена или уже была живой).
    """
    if not db_tasks:
        logger.error("[SysCron] db_tasks не передан — крон '%s' не проверить.", title)
        return False

    try:
        task = await find_system_task(db_tasks, title)
        when = next_run(cron_expression)

        if not task:
            task_id = await db_tasks.db_add_task({
                "tg_id": None,
                "task_type": TASK_TYPE,
                "title": title,
                "agent_instruction": instruction,
                "scheduled_at": when,
                "cron_expression": cron_expression,
                "status": "pending",
            })
            if task_id:
                logger.info("[SysCron] Создан крон '%s': #%s, след. запуск %s", title, task_id, when)
                if scheduler:
                    scheduler.notify_new_task()
                return True
            logger.error("[SysCron] Не удалось создать крон '%s'.", title)
            return False

        task_id = task.get("id")
        status = task.get("status")

        if status == "cancelled":
            logger.warning("[SysCron] Крон '%s' (#%s) на паузе (cancelled) — не трогаем.", title, task_id)
            return True

        if status in ("expired", "failed"):
            await db_tasks.db_update_task({
                "id": task_id,
                "status": "pending",
                "scheduled_at": when,
            })
            logger.warning("[SysCron] Крон '%s' (#%s) был в '%s' — оживлён, след. запуск %s",
                           title, task_id, status, when)
            if scheduler:
                scheduler.notify_new_task()
            return True

        # Строка жива, но время уехало в прошлое (бот лежал / часы сбились)
        scheduled_at = task.get("scheduled_at")
        if scheduled_at:
            try:
                lag = (get_now_datetime() - to_cleen(scheduled_at)).total_seconds() / 60.0
                if lag > stale_minutes:
                    await db_tasks.db_update_task({"id": task_id, "scheduled_at": when})
                    logger.warning("[SysCron] Крон '%s' (#%s) просрочен на %d мин — время пересчитано.",
                                   title, task_id, int(lag))
                    if scheduler:
                        scheduler.notify_new_task()
            except Exception as e:
                logger.warning("[SysCron] Не удалось проверить время крона '%s' (#%s): %s", title, task_id, e)

        return True

    except Exception as e:
        logger.error("[SysCron] Ошибка проверки крона '%s': %s", title, e, exc_info=True)
        return False
