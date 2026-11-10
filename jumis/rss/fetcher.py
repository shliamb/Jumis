# jumis/rss/fetcher.py
"""
Забор мировых новостей из RSS/Atom.

Здесь нет модели и нет токенов: только HTTP и XML. Задача модуля — превратить
разношёрстные фиды (RSS 2.0, RSS 1.0/RDF, Atom) в один список записей
с заголовком, коротким описанием, ссылкой и временем.

Правила, добытые практикой на живых фидах:
  • часть изданий закрыта для роботов (Reuters, AP, Kyodo) — это не ошибка кода;
  • часть отдаёт «200 OK» и пустое тело — пустой фид считается падением;
  • часть не пишет часовой пояс в дате — считаем время UTC, но будущее
    прижимаем к «сейчас», иначе такие записи навсегда остаются первыми;
  • описание часто содержит HTML и длиннее сути — режем до SUMMARY_LIMIT.
"""
import asyncio
import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx
from lxml import etree, html as lxml_html

from logs.set_logger import logger


# Сетевые параметры
HTTP_TIMEOUT = 20.0
CONCURRENCY = 8
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) JumisNews/1.0 (+personal assistant)"

# Сколько записей берём из одного фида и сколько фидов вообще зовём за раз
DEFAULT_PER_FEED = 10
MAX_PER_FEED = 25
MAX_FEEDS = 20

# Описание режем: суть новости в заголовке и первом предложении,
# а «простыня» на 600 символов от издания-«богатыря» съедает контекст модели.
SUMMARY_LIMIT = 240

# Стартовый набор источников: разные страны и разные углы по одним сюжетам.
# Используется один раз — если таблица фидов пуста.
DEFAULT_FEEDS = [
    {"url": "https://tass.com/rss/v2.xml",                  "title": "TASS",           "note": "РФ, государственное"},
    {"url": "https://meduza.io/rss/all",                    "title": "Meduza",         "note": "РФ, независимое, в изгнании"},
    {"url": "https://www.pravda.com.ua/rss/",               "title": "Ukrainska Pravda", "note": "Украина"},
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",  "title": "BBC World",      "note": "Великобритания, общественное"},
    {"url": "https://feeds.npr.org/1004/rss.xml",           "title": "NPR World",      "note": "США, общественное"},
    {"url": "https://rss.dw.com/rdf/rss-en-world",          "title": "DW English",     "note": "Германия, государственное"},
    {"url": "https://www.lemonde.fr/rss/une.xml",           "title": "Le Monde",       "note": "Франция"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",    "title": "Al Jazeera",     "note": "Катар"},
    {"url": "https://www.scmp.com/rss/91/feed",             "title": "SCMP",           "note": "Гонконг"},
    {"url": "https://www.globaltimes.cn/rss/outbrain.xml",  "title": "Global Times",   "note": "КНР, государственное"},
]

# Теги, из которых берём заголовок и текст
TITLE_TAGS = ("title",)
SUMMARY_TAGS = ("description", "summary", "content", "encoded", "subtitle")
DATE_TAGS = ("pubdate", "published", "updated", "date", "created")


@dataclass
class Entry:
    """Одна новость из фида."""
    title: str
    summary: str
    link: str
    published_at: Optional[datetime]          # aware, UTC
    feed_title: str = ""
    feed_note: str = ""
    feed_url: str = ""

    def haystack(self) -> str:
        return f"{self.title} {self.summary}".lower()


@dataclass
class FetchResult:
    """Итог забора: что собрали и кто не ответил."""
    entries: list[Entry] = field(default_factory=list)
    reports: list[dict] = field(default_factory=list)   # по одному на фид

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.reports if not r.get("ok")]

    @property
    def ok_reports(self) -> list[dict]:
        return [r for r in self.reports if r.get("ok")]

    @property
    def newest_at(self) -> Optional[datetime]:
        stamps = [e.published_at for e in self.entries if e.published_at]
        return max(stamps) if stamps else None

    def failed_names(self) -> str:
        names = [r.get("title") or r.get("url") for r in self.failed]
        return ", ".join(n for n in names if n)


# --------------------------------------------------------------- нормализация

def clean_text(raw: Optional[str], limit: int = 0) -> str:
    """Снимает HTML-теги, раскрывает сущности, сжимает пробелы."""
    if not raw:
        return ""
    text = html_lib.unescape(str(raw))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        cut = text[:limit]
        # Не обрываем посреди слова: отступаем до последнего пробела.
        space = cut.rfind(" ")
        text = (cut[:space] if space > limit * 0.6 else cut).rstrip(" ,;:—-") + "…"
    return text


def _local(name: str) -> str:
    """Локальное имя тега без namespace: {ns}item -> item."""
    return name.rsplit("}", 1)[-1].lower() if isinstance(name, str) else ""


def _element_text(node) -> str:
    """Текст элемента вместе с вложенными узлами (Atom <content> бывает HTML)."""
    try:
        text = " ".join(node.itertext())
    except Exception:
        text = node.text or ""
    return clean_text(text)


def parse_date(raw: Optional[str]) -> Optional[datetime]:
    """
    Дата из фида -> aware datetime в UTC.
    Порядок: RFC 822 (RSS) -> ISO 8601 (Atom) -> «брать нечего».
    """
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None

    parsed: Optional[datetime] = None
    try:
        parsed = parsedate_to_datetime(value)
    except Exception:
        parsed = None

    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None

    if parsed.tzinfo is None:
        # Фид не сказал зону (Global Times, часть RDF-лент). Считаем UTC,
        # а будущее прижмём ниже: иначе запись «из будущего» вечно первая.
        parsed = parsed.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if parsed > now + timedelta(minutes=10):
        parsed = now
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------- разбор XML

def parse_feed(body: bytes) -> list[dict]:
    """
    Разбирает тело фида в список {title, summary, link, published_at}.
    Понимает RSS 2.0, RSS 1.0 (RDF) и Atom.
    """
    if not body:
        return []

    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(body, parser=parser)
    except Exception:
        return []
    if root is None:
        return []

    items = []
    for node in root.iter():
        tag = _local(node.tag)
        if tag not in ("item", "entry"):
            continue

        title, summary, link, date = "", "", "", None
        for child in node:
            ctag = _local(child.tag)

            if ctag in TITLE_TAGS and not title:
                title = _element_text(child)

            elif ctag in SUMMARY_TAGS and not summary:
                summary = _element_text(child)

            elif ctag in DATE_TAGS and date is None:
                date = parse_date(child.text)

            elif ctag == "link":
                href = child.get("href")            # Atom
                if href and not link:
                    link = href.strip()
                elif child.text and not link:       # RSS
                    link = child.text.strip()

        if not title:
            continue

        items.append({
            "title": title,
            "summary": summary,
            "link": link,
            "published_at": date,
        })

    return items


def parse_feed_title(body: bytes) -> str:
    """Название фида из его собственного <title> (для автозаполнения)."""
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(body, parser=parser)
    except Exception:
        return ""
    if root is None:
        return ""

    for node in root.iter():
        if _local(node.tag) in ("channel", "feed"):
            for child in node:
                if _local(child.tag) == "title":
                    return clean_text(_element_text(child), limit=120)
            break
    return ""


# ------------------------------------------------------------- один фид/много

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )


async def fetch_one(client: httpx.AsyncClient, feed: dict, per_feed: int = DEFAULT_PER_FEED) -> dict:
    """
    Тянет один фид. Возвращает отчёт:
    {url, title, note, ok, status, error, items: [Entry], newest}
    """
    url = feed.get("url") or ""
    report = {
        "id": feed.get("id"),
        "url": url,
        "title": feed.get("title") or url,
        "note": feed.get("note") or "",
        "ok": False,
        "status": None,
        "error": None,
        "items": [],
        "newest": None,
    }

    if not url:
        report["error"] = "пустой url"
        return report

    try:
        response = await client.get(url)
    except Exception as e:
        report["error"] = f"{type(e).__name__}: {e}"[:300]
        return report

    report["status"] = response.status_code

    if response.status_code != 200:
        report["error"] = f"HTTP {response.status_code}"
        return report

    parsed = parse_feed(response.content)
    if not parsed:
        report["error"] = "фид ответил, но записей нет"
        return report

    # Свежие записи вперёд; записи без даты — в конец, они запасные.
    parsed.sort(key=lambda row: row["published_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    parsed = parsed[:max(1, min(int(per_feed or DEFAULT_PER_FEED), MAX_PER_FEED))]

    entries = []
    for row in parsed:
        entries.append(Entry(
            title=row["title"],
            summary=clean_text(row["summary"], limit=SUMMARY_LIMIT),
            link=row["link"],
            published_at=row["published_at"],
            feed_title=report["title"],
            feed_note=report["note"],
            feed_url=url,
        ))

    report["items"] = entries
    report["ok"] = True
    report["newest"] = max((e.published_at for e in entries if e.published_at), default=None)
    return report


async def fetch_all(feeds: list[dict], per_feed: int = DEFAULT_PER_FEED) -> FetchResult:
    """
    Забирает все фиды параллельно (не больше CONCURRENCY одновременно).
    Ни одна ошибка не роняет общий забор: битый фид попадает в reports с ok=False.
    """
    feeds = [f for f in (feeds or []) if f.get("url")][:MAX_FEEDS]
    result = FetchResult()
    if not feeds:
        return result

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def guarded(client, feed):
        async with semaphore:
            return await fetch_one(client, feed, per_feed=per_feed)

    async with _client() as client:
        reports = await asyncio.gather(*[guarded(client, feed) for feed in feeds])

    seen_links: set[str] = set()
    seen_titles: set[str] = set()

    for report in reports:
        result.reports.append(report)

        if not report["ok"]:
            logger.warning("[RSS] Фид не отдал новости (%s): %s", report["title"], report["error"])
            continue

        # Внутри одного запуска одна и та же новость может прийти дважды
        # (например, фид отдал её в двух разделах) — берём первый раз.
        for entry in report["items"]:
            key_link = (entry.link or "").strip().lower()
            key_title = re.sub(r"\W+", "", entry.title.lower())[:120]
            if key_link and key_link in seen_links:
                continue
            if key_title and key_title in seen_titles:
                continue
            if key_link:
                seen_links.add(key_link)
            if key_title:
                seen_titles.add(key_title)
            result.entries.append(entry)

    result.entries.sort(
        key=lambda e: e.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    logger.info(
        "[RSS] Забрано: %d записей из %d фидов, не ответили: %d",
        len(result.entries), len(result.ok_reports), len(result.failed),
    )
    return result


# ------------------------------------------------------- проверка/дискавери

def _looks_like_feed(body: bytes) -> bool:
    head = (body or b"")[:4000].lower()
    return b"<rss" in head or b"<feed" in head or b"<rdf" in head


def discover_feed_url(page: bytes, base_url: str) -> Optional[str]:
    """Ищет в HTML ссылку на фид: <link rel="alternate" type="application/rss+xml">."""
    try:
        document = lxml_html.fromstring(page)
    except Exception:
        return None

    for link in document.xpath("//link[@rel='alternate']"):
        kind = (link.get("type") or "").lower()
        href = (link.get("href") or "").strip()
        if href and ("rss" in kind or "atom" in kind or "xml" in kind):
            try:
                return str(httpx.URL(base_url).join(href))
            except Exception:
                return href

    # Ничего не объявлено — пробуем предсказуемые адреса.
    for suffix in ("/rss", "/feed", "/rss.xml", "/feed/"):
        candidate = base_url.rstrip("/") + suffix
        if candidate != base_url:
            return candidate        # проверит вызывающая сторона
    return None


async def validate_feed(url: str) -> dict:
    """
    Проверка источника перед сохранением. Если дали ссылку на сайт —
    ищем его фид (дискавери) и говорим об этом прямо в отчёте.

    Возвращает: {ok, url, title, items, newest, error, note}
    """
    report = {"ok": False, "url": url, "title": "", "items": 0,
              "newest": None, "error": None, "note": None}

    text = (url or "").strip()
    if not re.match(r"^https?://", text, re.I):
        report["error"] = "нужен полный адрес, начиная с http:// или https://"
        return report

    try:
        async with _client() as client:
            response = await client.get(text)
            report["status"] = response.status_code

            if response.status_code != 200:
                report["error"] = f"HTTP {response.status_code}"
                return report

            body = response.content
            final_url = str(response.url)

            if not _looks_like_feed(body):
                # Похоже на обычную страницу — ищем фид внутри.
                candidate = discover_feed_url(body, final_url)
                if not candidate:
                    report["error"] = "это не фид и фида на странице не нашлось"
                    return report

                try:
                    again = await client.get(candidate)
                except Exception as e:
                    report["error"] = f"нашёл фид {candidate}, но он не скачался: {type(e).__name__}"
                    return report

                if again.status_code != 200 or not parse_feed(again.content):
                    report["error"] = f"это не фид, а страница; похожий адрес {candidate} тоже пуст"
                    return report

                body = again.content
                final_url = str(again.url)
                report["note"] = f"дана ссылка на сайт — взял его фид {final_url}"

            items = parse_feed(body)
            if not items:
                report["error"] = "фид пустой"
                return report

            report["ok"] = True
            report["url"] = final_url
            report["title"] = parse_feed_title(body) or final_url
            report["items"] = len(items)
            report["newest"] = max(
                (i["published_at"] for i in items if i["published_at"]),
                default=None,
            )
            return report

    except Exception as e:
        report["error"] = f"{type(e).__name__}: {e}"[:200]
        return report


def parse_feed_list(text: str) -> list[dict]:
    """
    Разбор списка фидов из админки. Формат строки: `URL` или `URL | заметка`.
    Можно дать и просто домен сайта — фид найдётся при проверке.
    Пустые строки и строки с `#` игнорируются.
    """
    feeds, seen = [], set()

    for line in (text or "").splitlines():
        row = line.strip()
        if not row or row.startswith("#"):
            continue

        if "|" in row:
            url, note = row.split("|", 1)
        else:
            url, note = row, ""
        url = url.strip()
        note = note.strip()[:200] or None

        if not url:
            continue
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url

        key = url.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        feeds.append({"url": url, "note": note})

    return feeds
