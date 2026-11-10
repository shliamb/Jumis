# jumis/cleaner/__init__.py
"""
Модуль чистки истории.

Слой 1 (janitor): пустые сообщения, медиа без текста, болтовня, точные дубли.
                  Ноль токенов, удаление из messages + VACUUM.
Слой 2 (digest):  старая история сжимается моделью в дайджест (chat_digests),
                  сырые сообщения удаляются в той же транзакции.
Крон:             системная задача в scheduled_tasks, восстановление при старте.

Рабочие таблицы не меняются: только новая chat_digests и UPDATE users.summary.
"""
from logs.set_logger import logger

from database.create_tables import ensure_schema as ensure_db_schema
from cleaner.janitor import Janitor
from cleaner.digest import Digester
from cleaner.task import (
    TITLE,
    CRON,
    ensure_system_task,
    is_system_task,
)
from cleaner.rules import is_junk, is_searchable

__all__ = [
    "Cleaner", "is_junk", "is_searchable",
    "TITLE", "CRON", "ensure_system_task", "is_system_task",
]


class Cleaner:
    """Фасад: отчёт, уборка, самаризация, системный крон."""

    TITLE = TITLE
    CRON = CRON

    def __init__(self, db, db_users, db_tasks=None, llm=None, scheduler=None):
        self.db = db
        self.db_users = db_users
        self.db_tasks = db_tasks
        self.llm = llm
        self.scheduler = scheduler

        self.janitor = Janitor(db)
        self.digester = Digester(db, db_users, llm)
        self._nightly_running = False

    # ------------------------------------------------------------------ схема
    async def ensure_schema(self) -> bool:
        """Схема дайджестов объявлена в database/create_tables.py — единое место для всех таблиц."""
        return await ensure_db_schema(self.db)

    # ------------------------------------------------------------------ слой 1
    async def scan(self) -> dict:
        """Сухой отчёт по уборке (ничего не удаляет)."""
        return await self.janitor.scan()

    async def run_hygiene(self) -> dict:
        """Боевая уборка мусора и дублей."""
        return await self.janitor.run()

    # ------------------------------------------------------------------ слой 2
    async def run_digest(self, max_batches: int | None = None) -> dict:
        """Порционная самаризация: до `max_batches` вызовов модели."""
        return await self.digester.run_night(max_batches=max_batches)

    async def list_digests(self, chat_id: int | None = None, limit: int = 5) -> list[dict]:
        return await self.digester.list_digests(chat_id=chat_id, limit=limit)

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
            logger.info("[Cleaner] Обработчик системного крона зарегистрирован.")
        else:
            logger.warning("[Cleaner] Планировщик не умеет системные обработчики.")

    async def nightly(self, task: dict | None = None) -> dict:
        """
        Ночной прогон: сначала уборка, потом порция самаризации.
        Никогда не бросает исключение наружу — планировщик не должен умирать.
        """
        if self._nightly_running:
            logger.warning("[Cleaner] Ночной прогон уже идёт — пропуск.")
            return {"skipped": True, "reason": "already_running"}

        self._nightly_running = True
        result: dict = {}
        try:
            logger.info("[Cleaner] Ночной прогон начат (task=%s)", (task or {}).get("id"))

            try:
                result["hygiene"] = await self.run_hygiene()
            except Exception as e:
                result["hygiene"] = {"error": str(e)}
                logger.error("[Cleaner] Уборка упала: %s", e, exc_info=True)

            try:
                result["digest"] = await self.run_digest()
            except Exception as e:
                result["digest"] = {"error": str(e)}
                logger.error("[Cleaner] Самаризация упала: %s", e, exc_info=True)

            logger.info(
                "[Cleaner] Ночной прогон завершён: удалено=%s, батчей=%s",
                (result.get("hygiene") or {}).get("deleted_total"),
                (result.get("digest") or {}).get("batches"),
            )
        finally:
            self._nightly_running = False

        return result
