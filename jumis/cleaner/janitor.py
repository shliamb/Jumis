# jumis/cleaner/janitor.py
"""
Слой 1: детерминированная уборка. Ноль токенов, ноль модели.

Что удаляется:
  1. пустые сообщения и медиа без подписи/расшифровки;
  2. болтовня по правилам из rules.py ("ок", ")", "привет", огрызки);
  3. точные дубли внутри «чат + день» (до 40 символов, без цифр) — остаётся первая.

Чего НЕ трогаем: всё, где есть цифра, ссылка, @ или почта.
После удаления делаем VACUUM ANALYZE, иначе место на диске не вернётся.
"""
from logs.set_logger import logger
from cleaner.rules import is_junk


# Супермножество кандидатов: пустое / медиа / короткое.
# Точное решение всё равно принимает is_junk() в Python.
CANDIDATES_SQL = """
SELECT id, content, msg_type
FROM messages
WHERE coalesce(trim(content), '') = ''
   OR msg_type NOT IN ('text', 'voice')
   OR length(trim(coalesce(content, ''))) <= 24
"""

# Точные дубли в одном чате за один день. Первую оставляем, остальные — в утиль.
DUPS_CTE = """
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY chat_id, created_at::date, lower(trim(content))
               ORDER BY id
           ) AS rn
    FROM messages
    WHERE length(trim(coalesce(content, ''))) BETWEEN 1 AND 40
      AND content !~ '[0-9]'
      AND content !~* 'https?://'
      AND content !~ '@'
)
"""

DELETE_CHUNK = 500


class Janitor:
    """Уборщик. Умеет только считать и удалять — модель не вызывает."""

    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ scan
    async def scan(self) -> dict:
        """Сухой отчёт: сколько удалим и почему, без единого DELETE."""
        rows = await self.db.fetch(CANDIDATES_SQL)
        total = int(await self.db.fetchval("SELECT count(*) FROM messages") or 0)

        empty = media = chatter = meaningful = 0
        junk_ids: list[int] = []

        for row in rows:
            content = (row["content"] or "").strip()
            msg_type = (row["msg_type"] or "text").lower()

            if not is_junk(content, msg_type):
                meaningful += 1
                continue

            junk_ids.append(row["id"])
            if msg_type not in ("text", "voice"):
                media += 1
            elif not content:
                empty += 1
            else:
                chatter += 1

        dups = len(await self._dup_ids(exclude=set(junk_ids)))

        return {
            "total": total,
            "empty": empty,
            "media": media,
            "chatter": chatter,
            "meaningful_short": meaningful,
            "junk": len(junk_ids),
            "dups": dups,
            "candidates": len(junk_ids) + dups,
            "after": total - (len(junk_ids) + dups),
        }

    async def _dup_ids(self, exclude: set[int] | None = None) -> list[int]:
        """ID дублей, кроме тех, что и так считаются мусором (чтобы не двойной счёт)."""
        rows = await self.db.fetch(f"{DUPS_CTE} SELECT id FROM ranked WHERE rn > 1")
        ids = {row["id"] for row in rows}
        if exclude:
            ids -= exclude
        return sorted(ids)

    # ------------------------------------------------------------------- run
    async def run(self) -> dict:
        """Боевая уборка: удаляет мусор и дубли, затем VACUUM ANALYZE."""
        total_before = int(await self.db.fetchval("SELECT count(*) FROM messages") or 0)
        size_before = await self._size()
        logger.info("[Cleaner] Уборка начата: сообщений=%d, размер=%s", total_before, size_before)

        # Сначала дубли (пока живы их «первые» копии), потом мусор.
        # Множества не пересекаются: дубль, который уже мусор, считается мусором.
        junk_ids = await self._junk_ids()
        dup_ids = await self._dup_ids(exclude=set(junk_ids))

        dups = await self._delete_ids(dup_ids, "дубли")
        junk = await self._delete_ids(junk_ids, "мусор")

        # Место возвращает только VACUUM: без него удалённые строки остаются мертвецами.
        vacuum_ok = True
        try:
            await self.db.execute("VACUUM (ANALYZE) messages")
        except Exception as e:
            vacuum_ok = False
            logger.warning("[Cleaner] VACUUM не выполнен: %s", e)

        total_after = int(await self.db.fetchval("SELECT count(*) FROM messages") or 0)
        size_after = await self._size()

        report = {
            "deleted_junk": junk,
            "deleted_dups": dups,
            "deleted_total": junk + dups,
            "total_before": total_before,
            "total_after": total_after,
            "size_before": size_before,
            "size_after": size_after,
            "vacuum": vacuum_ok,
        }
        logger.info(
            "[Cleaner] Уборка завершена: мусор=%d, дубли=%d, было=%d, стало=%d, размер=%s -> %s",
            junk, dups, total_before, total_after, size_before, size_after,
        )
        return report

    async def _junk_ids(self) -> list[int]:
        rows = await self.db.fetch(CANDIDATES_SQL)
        return [row["id"] for row in rows if is_junk(row["content"], row["msg_type"])]

    async def _delete_ids(self, ids: list, label: str) -> int:
        """Удаляет строки пачками и возвращает фактическое число удалённых."""
        if not ids:
            return 0

        deleted = 0
        for start in range(0, len(ids), DELETE_CHUNK):
            chunk = ids[start:start + DELETE_CHUNK]
            try:
                result = await self.db.execute(
                    "DELETE FROM messages WHERE id = ANY($1::bigint[])", chunk
                )
                # asyncpg возвращает строку вида "DELETE 500"
                if result and result.startswith("DELETE"):
                    deleted += int(result.split()[1])
            except Exception as e:
                logger.error("[Cleaner] Ошибка удаления (%s) на чанке %s: %s", label, start, e)
                continue

        logger.info("[Cleaner] Удалено (%s): %d строк", label, deleted)
        return deleted

    async def _size(self) -> str:
        try:
            return await self.db.fetchval(
                "SELECT pg_size_pretty(pg_total_relation_size('messages'))"
            )
        except Exception:
            return "n/a"
