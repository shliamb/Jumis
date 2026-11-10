# database/rss.py
"""
Репозиторий мировых новостей: источники (rss_feeds) и накопленные сводки (rss_digests).

Правила:
  • сводки только добавляются — перезаписи нет, терять срезы нельзя;
  • время фильтруется в SQL (NOW(), make_interval), чтобы не зависеть от часов Python;
  • имена колонок не собираются из внешних строк — только из литералов этого файла.
"""
from typing import Optional

from logs.set_logger import logger
from database import db


# Колонки rss_digests, которые отдаём наружу: полный body нужен не всегда,
# поэтому он запрашивается отдельным флагом.
DIGEST_SHORT_COLUMNS = (
    "id, created_at, period_from, period_to, gist, "
    "items_count, feeds_used, feeds_failed, model"
)


class DBRss:

    def __init__(self):
        self.db = db


    ##############
    ##### FEEDS ##
    ##############

    async def get_feeds(self, active_only: bool = False) -> list[dict]:
        """Список фидов. active_only=True — только включённые."""
        query = "SELECT * FROM rss_feeds"
        if active_only:
            query += " WHERE active = TRUE"
        query += " ORDER BY id ASC;"
        records = await self.db.fetch(query)
        return [dict(record) for record in records] if records else []


    async def count_feeds(self) -> int:
        return await self.db.fetchval("SELECT COUNT(*) FROM rss_feeds;") or 0


    async def add_feed(self, url: str, title: str = None, note: str = None) -> Optional[int]:
        """Добавить один фид. Возвращает id, либо None если такой url уже есть."""
        if not url:
            return None
        row = await self.db.fetchrow(
            """
            INSERT INTO rss_feeds (url, title, note)
            VALUES ($1, $2, $3)
            ON CONFLICT (url) DO NOTHING
            RETURNING id;
            """,
            url.strip(), title, note,
        )
        return row["id"] if row else None


    async def remove_feed(self, ident: str) -> int:
        """
        Удалить фид по id, точному url или фрагменту названия.
        Возвращает число удалённых строк (0 — не нашли).
        """
        text = str(ident or "").strip()
        if not text:
            return 0

        if text.isdigit():
            result = await self.db.execute("DELETE FROM rss_feeds WHERE id = $1;", int(text))
        else:
            result = await self.db.execute(
                "DELETE FROM rss_feeds WHERE url = $1 OR title ILIKE $2;",
                text, f"%{text}%",
            )
        try:
            return int(str(result).split()[-1])
        except (ValueError, IndexError):
            return 0


    async def replace_feeds(self, feeds: list[dict]) -> int:
        """
        Полностью заменить список фидов (как в админке: «принять новый список»).
        Атомарно: либо новый список целиком, либо старый без изменений.
        """
        if not feeds:
            raise ValueError("replace_feeds получил пустой список — это стёрло бы все источники")

        if self.db.pool is None:
            await self.db.connect()

        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM rss_feeds;")
                for feed in feeds:
                    await conn.execute(
                        """
                        INSERT INTO rss_feeds (url, title, note)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (url) DO NOTHING;
                        """,
                        feed["url"], feed.get("title"), feed.get("note"),
                    )
        return len(feeds)


    async def mark_ok(self, feed_id: int, last_item_at=None) -> None:
        """Фид ответил: сбрасываем счётчик падений и ошибку."""
        await self.db.execute(
            """
            UPDATE rss_feeds
               SET last_ok_at = NOW(),
                   fail_count = 0,
                   last_error = NULL,
                   last_item_at = COALESCE($2, last_item_at)
             WHERE id = $1;
            """,
            feed_id, last_item_at,
        )


    async def mark_fail(self, feed_id: int, error: str) -> None:
        """Фид не ответил или отдал мусор: копим счётчик и запоминаем причину."""
        await self.db.execute(
            """
            UPDATE rss_feeds
               SET fail_count = fail_count + 1,
                   last_error = $2
             WHERE id = $1;
            """,
            feed_id, str(error or "")[:500],
        )


    ################
    ##### DIGESTS ##
    ################

    async def save_digest(
        self,
        period_from,
        period_to,
        gist: str,
        body: str,
        items_count: int = 0,
        feeds_used: int = 0,
        feeds_failed: str = None,
        model: str = None,
    ) -> Optional[int]:
        """Записать готовую сводку. Возвращает id строки."""
        row = await self.db.fetchrow(
            """
            INSERT INTO rss_digests
                (period_from, period_to, gist, body, items_count, feeds_used, feeds_failed, model)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id;
            """,
            period_from, period_to, gist, body,
            int(items_count or 0), int(feeds_used or 0), feeds_failed, model,
        )
        return row["id"] if row else None


    async def latest_digest(self, younger_than_minutes: int = 0, with_body: bool = False) -> Optional[dict]:
        """Последняя сводка. younger_than_minutes=120 — только если моложе двух часов."""
        columns = DIGEST_SHORT_COLUMNS + (", body" if with_body else "")
        query = f"SELECT {columns} FROM rss_digests"
        args = []
        if younger_than_minutes and younger_than_minutes > 0:
            query += " WHERE created_at > NOW() - make_interval(mins => $1)"
            args.append(int(younger_than_minutes))
        query += " ORDER BY created_at DESC LIMIT 1;"

        row = await self.db.fetchrow(query, *args)
        return dict(row) if row else None


    async def get_digest(self, digest_id: int) -> Optional[dict]:
        """Одна сводка целиком, вместе с полным текстом."""
        row = await self.db.fetchrow(
            "SELECT * FROM rss_digests WHERE id = $1;", int(digest_id)
        )
        return dict(row) if row else None


    async def list_digests(
        self,
        days: int = 30,
        query: str = None,
        limit: int = 30,
        with_body: bool = False,
    ) -> list[dict]:
        """
        Сводки за последние `days` дней, новые сверху.
        query — необязательный фильтр по подстроке (выжимка или полный текст).
        """
        columns = DIGEST_SHORT_COLUMNS + (", body" if with_body else "")
        sql = f"SELECT {columns} FROM rss_digests WHERE created_at >= NOW() - make_interval(days => $1)"
        args = [max(1, int(days or 1)), max(1, int(limit or 30))]

        if query:
            sql += " AND (gist ILIKE $3 OR body ILIKE $3)"
            args.append(f"%{query}%")

        sql += " ORDER BY created_at DESC LIMIT $2;"
        records = await self.db.fetch(sql, *args)
        return [dict(record) for record in records] if records else []


    async def count_digests(self) -> int:
        return await self.db.fetchval("SELECT COUNT(*) FROM rss_digests;") or 0
