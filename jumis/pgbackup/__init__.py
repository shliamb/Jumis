# jumis/pgbackup/__init__.py
"""
Модуль бэкапа базы данных.

Что делает: `pg_dump` внутри docker-контейнера пишет сжатый дамп в папку
`jumis/backups/`, после успешного дампа старые копии сворачиваются до трёх.
Крон — 04:30, системная задача в scheduled_tasks: агент её видит, но удалить
или переписать не может (utils/system_cron.py).

Восстановление (вручную, при остановленном боте):
    docker exec -i jumis_postgres pg_restore -U <user> -d <db> --clean --if-exists < файл.dump

Рабочие таблицы модуль не трогает вообще: только читает базу и пишет файлы.
"""
from config import PATH_BACKUPS
from logs.set_logger import logger

from pgbackup.runner import KEEP_COPIES, DBRunner, human_size
from pgbackup.task import (
    CRON,
    TITLE,
    ensure_system_task,
    find_task,
    is_system_task,
)


__all__ = [
    "Backups", "TITLE", "CRON", "KEEP_COPIES",
    "ensure_system_task", "is_system_task", "find_task",
]

RESTORE_HINT = (
    "docker exec -i jumis_postgres pg_restore "
    "-U <user> -d <db> --clean --if-exists < jumis_YYYY-MM-DD_HHMMSS.dump"
)


class Backups:
    """Фасад: создать копию, показать список, системный крон."""

    TITLE = TITLE
    CRON = CRON
    KEEP = KEEP_COPIES
    RESTORE_HINT = RESTORE_HINT

    def __init__(
            self,
            db_tasks=None,
            scheduler=None,
            directory: str = PATH_BACKUPS,
            keep: int = KEEP_COPIES,
    ):
        self.db_tasks = db_tasks
        self.scheduler = scheduler
        self.runner = DBRunner(directory=directory, keep=keep)
        self._nightly_running = False

    # --------------------------------------------------------------- операции
    @property
    def directory(self):
        return self.runner.directory

    @property
    def keep(self) -> int:
        return self.runner.keep

    async def create(self) -> dict:
        """Сделать копию сейчас (ротация внутри)."""
        return await self.runner.create()

    def list(self, limit: int = 10) -> list[dict]:
        """Копии от новой к старой."""
        return self.runner.list_files(limit=limit)

    def total_size(self) -> int:
        return self.runner.total_size()

    @staticmethod
    def human_size(size: int) -> str:
        return human_size(size)

    # ------------------------------------------------------------------- крон
    async def ensure_system_task(self) -> bool:
        return await ensure_system_task(self.db_tasks, self.scheduler)

    def register(self) -> None:
        """Регистрирует обработчик системного крона в планировщике."""
        if not self.scheduler:
            return
        register = getattr(self.scheduler, "register_system_task", None)
        if register:
            register(self.TITLE, self.nightly)
            logger.info("[Backup] Обработчик системного крона зарегистрирован.")
        else:
            logger.warning("[Backup] Планировщик не умеет системные обработчики.")

    async def nightly(self, task: dict | None = None) -> dict:
        """
        Ночной прогон: один дамп и ротация.
        Никогда не бросает исключение наружу — планировщик не должен умирать.
        """
        if self._nightly_running:
            logger.warning("[Backup] Ночной прогон уже идёт — пропуск.")
            return {"ok": False, "error": "already_running"}

        self._nightly_running = True
        try:
            logger.info("[Backup] Ночной прогон начат (task=%s)", (task or {}).get("id"))
            report = await self.create()
            if not report.get("ok"):
                logger.error("[Backup] Ночной прогон не удался: %s", report.get("error"))
            return report
        except Exception as e:
            logger.error("[Backup] Ночной прогон упал: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}
        finally:
            self._nightly_running = False
