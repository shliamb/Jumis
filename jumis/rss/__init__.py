# jumis/rss/__init__.py
"""
Модуль мировых новостей.

Два слоя:
  1. Код (fetcher) — забирает фиды, чистит, режет, склеивает дубли. Ноль токенов.
  2. Модель (analyst) — один вызов, который сводит всё в сводку со сносками.

Сводка сохраняется в rss_digests вместе с короткой выжимкой: полный текст нужен
для чтения сегодня, выжимка — чтобы через год одним запросом поднять картину.

Порядок работы собран в `collect()`: кэш -> забор -> анализ -> запись -> статусы.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from logs.set_logger import logger
from utils.common import to_app_tz_naive

from rss import analyst, fetcher


# Свежую сводку не пересобираем: она и так «сейчас».
CACHE_MINUTES = 120

# Окно новостей по умолчанию и пределы, которые нельзя превысить инструментом.
DEFAULT_HOURS = 12
MAX_HOURS = 72
DEFAULT_PER_FEED = 10

# Здоровье фида
DEAD_FAILS = 3          # столько падений подряд -> «мёртвый»
STALE_DAYS = 14         # столько дней без новых записей -> «молчит»

MIN_ENTRIES_TO_ANALYZE = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _app(dt) -> Optional[datetime]:
    return to_app_tz_naive(dt) if dt else None


class RssNews:

    def __init__(self, db_rss, llm):
        self.db = db_rss
        self.llm = llm
        self._lock = asyncio.Lock()


    # ------------------------------------------------------------------ фиды

    async def ensure_seeded(self) -> int:
        """Первый запуск: если фидов нет вообще — заводим стартовый набор."""
        try:
            count = await self.db.count_feeds()
            if count:
                return count
            await self.db.replace_feeds([dict(feed) for feed in fetcher.DEFAULT_FEEDS])
            logger.info("[RSS] Таблица фидов пуста — завёл стартовый набор (%d)", len(fetcher.DEFAULT_FEEDS))
            return len(fetcher.DEFAULT_FEEDS)
        except Exception as e:
            logger.error("[RSS] Не удалось завести стартовый набор фидов: %s", e, exc_info=True)
            return 0


    def status_of(self, feed: dict) -> str:
        """
        Состояние фида по данным таблицы:
          dead  — падает DEAD_FAILS раз подряд;
          stale — отвечает, но новых записей нет STALE_DAYS дней;
          warn  — были падения, но терпимые;
          new   — ещё ни разу не проверялся;
          ok    — всё хорошо.
        """
        fails = int(feed.get("fail_count") or 0)
        if fails >= DEAD_FAILS:
            return "dead"

        last_item = feed.get("last_item_at")
        if last_item and (to_app_tz_naive(last_item) < to_app_tz_naive(_now()) - timedelta(days=STALE_DAYS)):
            return "stale"

        if fails > 0:
            return "warn"
        if not feed.get("last_ok_at"):
            return "new"
        return "ok"


    async def feeds(self, active_only: bool = False) -> list[dict]:
        """Список фидов с вычисленным состоянием."""
        await self.ensure_seeded()
        rows = await self.db.get_feeds(active_only=active_only)
        for row in rows:
            row["status"] = self.status_of(row)
        return rows


    async def check_all(self) -> list[dict]:
        """Проверить каждый фид по-настоящему (сеть) и обновить состояние в базе."""
        rows = await self.feeds()
        if not rows:
            return []

        reports = await asyncio.gather(*[fetcher.validate_feed(row["url"]) for row in rows])
        result = []

        for row, report in zip(rows, reports):
            if report["ok"]:
                await self.db.mark_ok(row["id"], report.get("newest"))
                result.append({
                    "url": row["url"],
                    "title": row.get("title") or report.get("title"),
                    "ok": True,
                    "items": report["items"],
                    "newest": _app(report.get("newest")),
                    "status": "ok",
                    "note": report.get("note"),
                })
            else:
                await self.db.mark_fail(row["id"], report.get("error") or "unknown")
                result.append({
                    "url": row["url"],
                    "title": row.get("title") or row["url"],
                    "ok": False,
                    "items": 0,
                    "error": report.get("error"),
                    "status": "warn",
                    "fails": int(row.get("fail_count") or 0) + 1,
                })

        # Счётчик падений вырос только что — одним чтением уточняем, кто уже «мёртв».
        if any(not item["ok"] for item in result):
            by_url = {row["url"]: row for row in await self.db.get_feeds()}
            for item in result:
                if item["ok"]:
                    continue
                fresh = by_url.get(item["url"]) or {}
                fails = int(fresh.get("fail_count") or item.get("fails") or 0)
                item["fails"] = fails
                item["status"] = "dead" if fails >= DEAD_FAILS else "warn"

        ok_count = sum(1 for item in result if item["ok"])
        logger.info("[RSS] Проверка фидов: живых %d из %d", ok_count, len(result))
        return result


    async def add_one(self, url: str, note: str = None) -> dict:
        """Добавить один фид с проверкой. Возвращает отчёт."""
        report = await fetcher.validate_feed(url)
        if not report["ok"]:
            return {"ok": False, "url": url, "error": report.get("error")}

        feed_id = await self.db.add_feed(report["url"], report.get("title"), note)
        if feed_id is None:
            return {"ok": False, "url": report["url"], "error": "такой фид уже есть"}
        return {
            "ok": True,
            "id": feed_id,
            "url": report["url"],
            "title": report.get("title"),
            "items": report["items"],
            "newest": _app(report.get("newest")),
            "note": report.get("note"),
        }


    async def remove_one(self, ident: str) -> int:
        return await self.db.remove_feed(ident)


    async def replace_from_text(self, text: str) -> dict:
        """
        Полная замена списка из админки. Каждый фид проверяется до сохранения;
        если не прошёл хоть один — список всё равно заменяется живыми,
        а отвергнутые перечисляются с причиной. Если живых нет — старый остаётся.
        """
        parsed = fetcher.parse_feed_list(text)
        if not parsed:
            return {"ok": False, "error": "в тексте не нашлось ни одного адреса", "accepted": [], "rejected": []}

        semaphore = asyncio.Semaphore(5)

        async def guarded(feed):
            async with semaphore:
                return feed, await fetcher.validate_feed(feed["url"])

        results = await asyncio.gather(*[guarded(feed) for feed in parsed])

        accepted, rejected = [], []
        for feed, report in results:
            if report["ok"]:
                accepted.append({
                    "url": report["url"],
                    "title": report.get("title"),
                    "note": feed.get("note"),
                    "items": report["items"],
                    "newest": _app(report.get("newest")),
                    "substituted": report.get("note"),
                })
            else:
                rejected.append({
                    "url": feed["url"],
                    "note": feed.get("note"),
                    "error": report.get("error"),
                })

        if not accepted:
            return {
                "ok": False,
                "error": "ни один источник не ответил — список не тронут",
                "accepted": [],
                "rejected": rejected,
            }

        await self.db.replace_feeds(accepted)

        # После замены сразу проставляем состояние: новые строки ещё «new».
        for feed in await self.db.get_feeds():
            match = next((a for a in accepted if a["url"] == feed["url"]), None)
            if match:
                await self.db.mark_ok(feed["id"], None)

        logger.info(
            "[RSS] Список фидов заменён: принято %d, отклонено %d",
            len(accepted), len(rejected),
        )
        return {"ok": True, "accepted": accepted, "rejected": rejected}


    # ------------------------------------------------------------------ сводки

    async def collect(self, hours: int = DEFAULT_HOURS, fresh: bool = False,
                      per_feed: int = DEFAULT_PER_FEED) -> dict:
        """
        Главный проход: собрать сводку или вернуть свежую из кэша.

        Возвращает {ok, cached, digest, failed, error?}. digest — строка rss_digests
        (id, created_at, gist, body, отчёты по фидам).
        """
        if self._lock.locked():
            return {"ok": False, "error": "busy", "cached": False, "digest": None}

        async with self._lock:
            return await self._collect_locked(hours=hours, fresh=fresh, per_feed=per_feed)


    async def _collect_locked(self, hours: int, fresh: bool, per_feed: int) -> dict:
        hours = max(1, min(int(hours or DEFAULT_HOURS), MAX_HOURS))
        per_feed = max(3, min(int(per_feed or DEFAULT_PER_FEED), fetcher.MAX_PER_FEED))

        if not fresh:
            try:
                cached = await self.db.latest_digest(younger_than_minutes=CACHE_MINUTES, with_body=True)
            except Exception as e:
                logger.warning("[RSS] Не удалось прочитать кэш сводок: %s", e)
                cached = None
            if cached:
                logger.info("[RSS] Отдаю свежую сводку из кэша (id=%s)", cached.get("id"))
                return {"ok": True, "cached": True, "digest": cached, "failed": []}

        feeds = await self.feeds(active_only=True)
        if not feeds:
            return {"ok": False, "cached": False, "digest": None,
                    "error": "нет активных источников — добавьте фиды в админке"}

        period_to = _now()
        period_from = period_to - timedelta(hours=hours)

        fetched = await fetcher.fetch_all(feeds, per_feed=per_feed)

        # Обновляем здоровье источников по факту забора.
        for report in fetched.reports:
            if not report.get("id"):
                continue
            try:
                if report["ok"]:
                    await self.db.mark_ok(report["id"], report.get("newest"))
                else:
                    await self.db.mark_fail(report["id"], report.get("error") or "unknown")
            except Exception as e:
                logger.warning("[RSS] Не удалось обновить статус фида %s: %s", report.get("title"), e)

        entries = self._window(fetched.entries, period_from, period_to)

        if len(entries) < MIN_ENTRIES_TO_ANALYZE:
            return {
                "ok": False, "cached": False, "digest": None,
                "failed": [r for r in fetched.reports if not r.get("ok")],
                "error": f"слишком мало новостей за {hours} ч: {len(entries)} "
                         f"(не ответили: {fetched.failed_names() or 'никто'})",
            }

        analysis = await analyst.analyze(
            self.llm, entries, period_from, period_to,
            failed_names=fetched.failed_names(),
        )
        if not analysis:
            return {
                "ok": False, "cached": False, "digest": None,
                "failed": [r for r in fetched.reports if not r.get("ok")],
                "error": "модель не дала сводку",
            }

        # «Фидов в сводке» — те, чьи записи реально попали в окно.
        # Ответивший, но молчащий с прошлого месяца фид сюда не входит.
        contributing = {entry.feed_url for entry in entries if entry.feed_url}

        digest_id = await self.db.save_digest(
            period_from=period_from,
            period_to=period_to,
            gist=analysis["gist"],
            body=analysis["body"],
            items_count=len(entries),
            feeds_used=len(contributing),
            feeds_failed=fetched.failed_names() or None,
            model=str(getattr(self.llm, "current_model", "") or "")[:100] or None,
        )

        digest = await self.db.get_digest(digest_id) if digest_id else None
        logger.info(
            "[RSS] Сводка id=%s: %d записей, %d фидов, %d не ответили",
            digest_id, len(entries), len(fetched.ok_reports), len(fetched.failed),
        )
        return {
            "ok": True,
            "cached": False,
            "digest": digest,
            "failed": [r for r in fetched.reports if not r.get("ok")],
            "entries_count": len(entries),
        }


    def _window(self, entries, period_from, period_to):
        """Отбирает записи в окне. Записи без даты берём только если дат нет вообще."""
        dated = [
            entry for entry in entries
            if entry.published_at and period_from <= entry.published_at <= period_to + timedelta(minutes=10)
        ]
        if dated:
            return dated
        return [entry for entry in entries if not entry.published_at][:15]


    async def archive(self, days: int = 30, query: str = None, limit: int = 30,
                      with_body: bool = False) -> list[dict]:
        """Накопленные сводки за период: только выжимки (или полные тексты)."""
        return await self.db.list_digests(days=days, query=query, limit=limit, with_body=with_body)


    async def read(self, digest_id: int) -> Optional[dict]:
        return await self.db.get_digest(digest_id)


    async def stats(self) -> dict:
        await self.ensure_seeded()
        feeds = await self.db.get_feeds()
        return {
            "feeds": len(feeds),
            "feeds_active": sum(1 for f in feeds if f.get("active")),
            "feeds_dead": sum(1 for f in feeds if self.status_of(f) == "dead"),
            "digests": await self.db.count_digests(),
        }
