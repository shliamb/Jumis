# jumis/rss/analyst.py
"""
Аналитик мировых новостей: превращает пачку записей из фидов в сводку со сносками.

Это единственное место в модуле, где тратятся токены. Всё, что можно сделать
кодом (забор, чистка, обрезка, склейка дублей), уже сделано до этого вызова.

Ответ модели разбирается по трём маркерам, а не парсится как вольный текст:
  ВЫЖИМКА:  короткая выжимка (в базу, для архива за годы)
  СВОДКА:   сюжеты со сносками на источники (главный текст)
  ВЫВОДЫ:   оценки аналитика, отделённые от фактов
"""
import re
from datetime import datetime
from typing import Optional

from logs.set_logger import logger
from utils.common import to_app_tz_naive


# Сколько символов входа отдаём модели. 10 фидов × 10 записей ≈ 26k символов;
# потолок нужен, чтобы один разросшийся фид не съел весь лимит модели.
MAX_INPUT_CHARS = 45000

# Потолок ответа: сводка длиннее в Telegram всё равно не влезет.
MAX_BODY_CHARS = 12000

# Сводка — длинная генерация: 30 секунд общего лимита её обрывают на середине.
LLM_TIMEOUT = 180


SYSTEM_PROMPT = (
    "Ты — аналитик мировых новостей для одного владельца. Ты получаешь записи из новостных "
    "фидов разных стран и сводишь их в одну сводку, честно показывая, кто что утверждает.\n\n"
    "ПРАВИЛА:\n"
    "1. Пиши по-русски. Коротко, без вступлений, без «итак», без вежливости.\n"
    "2. Один сюжет — один пункт. Если о событии пишут несколько фидов, объедини их в один "
    "сюжет и перечисли всех, кто об этом пишет.\n"
    "3. У каждого факта источник в скобках: (TASS), (BBC World, NPR). Сокращай до названия "
    "фида, ровно как написано в скобках рядом с ним.\n"
    "4. Ничего не выдумывай. Нет в записях — нет в сводке. Не добавляй контекст «из головы».\n"
    "5. Если источники расходятся — приведи обе версии рядом с указанием, кто что говорит. "
    "Не выбирай, кто прав, и не сглаживай разницу.\n"
    "6. Если событие упоминает только один фид — пометь: (один источник: X).\n"
    "7. Числа, имена, города, суммы, даты копируй дословно из записей.\n"
    "8. В СВОДКЕ — только факты из записей. Оценки — только в ВЫВОДАХ, и они явно оценка.\n"
    "9. Возле названия фида дана его характеристика (государственное, независимое, страна). "
    "Учитывай её при атрибуции, но не вставляй ярлыки в каждый пункт — только там, где это "
    "объясняет расхождение.\n"
    "10. Если записей мало и картины нет — так и скажи в выводах одной строкой, не раздувай.\n\n"
    "ФОРМАТ ОТВЕТА — ровно три блока, строго в этом порядке, маркеры заглавными буквами, "
    "никаких вступлений до первого маркера:\n\n"
    "ВЫЖИМКА:\n"
    "- 3-5 строк, самая суть: кто, что, где. Без источников и без оценок.\n\n"
    "СВОДКА:\n"
    "### Короткое название сюжета\n"
    "- что произошло (1-3 строки), источники в скобках.\n"
    "- если версии расходятся — строка «По версии X: …» с указанием, кто это говорит.\n"
    "(5-9 самых важных сюжетов, важное вперёд)\n\n"
    "ВЫВОДЫ:\n"
    "- 2-4 пункта: что из этого следует, где картина расходится, за чем следить. Это оценка.\n"
)

_MARKERS = (
    ("gist", "ВЫЖИМКА"),
    ("body", "СВОДКА"),
    ("conclusions", "ВЫВОДЫ"),
)

# LLMWorker при сетевой/API-ошибке не бросает исключение, а подставляет текст
# ошибки вместо ответа модели. Такой текст нельзя сохранять как сводку.
_ERROR_MARKERS = (
    "ошибка сети",
    "ошибка api",
    "litellm.",
    "badrequesterror",
    "apierror",
    "authenticationerror",
    "insufficient_quota",
    "rate_limit",
    "model parameter is required",
)


def looks_like_error(raw: str) -> bool:
    """Похоже ли содержимое ответа на сообщение об ошибке, а не на сводку."""
    if not raw or not raw.strip():
        return True
    head = raw.strip().lower()[:600]
    return any(marker in head for marker in _ERROR_MARKERS)


# ------------------------------------------------------------------ рендеринг

def render_input(entries, period_from: datetime, period_to: datetime,
                 failed_names: str = "") -> str:
    """
    Собирает вход для модели: сгруппировано по фидам, с характеристикой источника.
    Так модель видит, кто это сказал, и может ставить сноски, не угадывая.
    """
    by_feed: dict[str, dict] = {}
    for entry in entries:
        bucket = by_feed.setdefault(entry.feed_title or entry.feed_url, {
            "note": entry.feed_note or "",
            "items": [],
        })
        bucket["items"].append(entry)

    header = [
        f"ПЕРИОД: {to_app_tz_naive(period_from):%d.%m %H:%M} — "
        f"{to_app_tz_naive(period_to):%d.%m.%Y %H:%M} (МСК)",
        f"ЗАПИСЕЙ: {len(entries)}",
        f"ФИДОВ: {len(by_feed)}",
    ]
    if failed_names:
        header.append(f"НЕ ОТВЕТИЛИ: {failed_names}")
    header.append("")

    blocks = ["\n".join(header)]

    for feed_title, bucket in by_feed.items():
        note = f" ({bucket['note']})" if bucket["note"] else ""
        lines = [f"[{feed_title}]{note} — {len(bucket['items'])} записей"]
        for number, entry in enumerate(bucket["items"], 1):
            stamp = ""
            if entry.published_at:
                stamp = f"{to_app_tz_naive(entry.published_at):%H:%M} "
            lines.append(f"{number}. {stamp}{entry.title}")
            if entry.summary:
                lines.append(f"   {entry.summary}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def build_input(entries, period_from, period_to, failed_names: str = "") -> str:
    """render_input + защита от переполнения: режем описания, потом записи."""
    text = render_input(entries, period_from, period_to, failed_names)
    if len(text) <= MAX_INPUT_CHARS:
        return text

    # Первый шаг: обрезаем описания вдвое (заголовки не трогаем — в них суть).
    for entry in entries:
        if entry.summary and len(entry.summary) > 120:
            entry.summary = entry.summary[:120].rstrip(" ,;:—-") + "…"
    text = render_input(entries, period_from, period_to, failed_names)
    if len(text) <= MAX_INPUT_CHARS:
        return text

    # Второй шаг: убираем хвостовые записи, начиная с самых старых.
    trimmed = list(entries)
    while trimmed and len(text) > MAX_INPUT_CHARS:
        trimmed = trimmed[: max(1, int(len(trimmed) * 0.85))]
        text = render_input(trimmed, period_from, period_to, failed_names)

    logger.warning(
        "[RSS] Вход обрезан до %d символов (%d записей из %d)",
        len(text), len(trimmed), len(entries),
    )
    return text


# --------------------------------------------------------------------- разбор

def strip_markers(raw: str) -> dict:
    """
    Разбирает ответ модели на три блока по маркерам.
    Если маркеров нет — весь текст считается сводкой, выжимка берётся из начала.
    """
    result = {"gist": "", "body": "", "conclusions": ""}
    if not raw:
        return result

    if not any(marker in raw for _, marker in _MARKERS):
        result["body"] = raw.strip()[:MAX_BODY_CHARS]
        result["gist"] = _plain_gist(result["body"], limit=5)
        return result

    current = None
    buckets: dict[str, list[str]] = {"gist": [], "body": [], "conclusions": []}

    for raw_line in raw.splitlines():
        line = raw_line.rstrip()
        upper = line.strip().upper()
        matched = False
        for key, marker in _MARKERS:
            if upper.startswith(marker):
                current = key
                rest = line.strip()[len(marker):].lstrip(": ").strip()
                if rest:
                    buckets[current].append(rest)
                matched = True
                break
        if matched:
            continue
        if current:
            buckets[current].append(line)

    result["gist"] = "\n".join(l.strip() for l in buckets["gist"]).strip()
    result["body"] = "\n".join(buckets["body"]).strip()
    result["conclusions"] = "\n".join(buckets["conclusions"]).strip()

    # Страховки: пустая сводка — это провал разбора, отдаём весь текст.
    if not result["body"]:
        result["body"] = raw.strip()[:MAX_BODY_CHARS]
    if not result["gist"]:
        result["gist"] = _plain_gist(result["body"], limit=5)

    result["body"] = result["body"][:MAX_BODY_CHARS]
    return result


def _plain_gist(body: str, limit: int = 5) -> str:
    """Выжимка из первых содержательных строк, если модель её не дала."""
    lines = []
    for line in body.splitlines():
        clean = line.strip().lstrip("#-*• ").strip()
        if not clean or clean.startswith("---"):
            continue
        lines.append(clean)
        if len(lines) >= limit:
            break
    return "\n".join(lines) or (body.strip()[:300])


def compose_body(body: str, conclusions: str) -> str:
    """Финальный текст сводки: сюжеты + отделённый блок выводов."""
    text = body.strip()
    if conclusions.strip():
        text += "\n\n---\n\n**Выводы (оценка)**\n" + conclusions.strip()
    return text[:MAX_BODY_CHARS]


def compose_gist(gist: str) -> str:
    """Короткая выжимка: 3-5 строк, чистый текст без маркдауна."""
    lines = [re.sub(r"^[\s\-•*]+", "", line).strip() for line in (gist or "").splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines[:6])[:900]


async def analyze(llm, entries, period_from, period_to, failed_names: str = "") -> Optional[dict]:
    """
    Один вызов модели. Возвращает {gist, body, raw} либо None, если модель молчит.

    Вызов идёт как субагент: свой system-промпт, пустой диалог, история
    владельца в запрос не попадает (см. LLMWorker.simple_call -> _call_request).
    """
    if not entries:
        return None

    question = build_input(entries, period_from, period_to, failed_names)

    try:
        response = await llm.simple_call(
            system=SYSTEM_PROMPT, dialog=[], question=question, timeout=LLM_TIMEOUT,
        )
    except Exception as e:
        logger.error("[RSS] Модель не ответила: %s", e, exc_info=True)
        return None

    raw = response.get("content") if isinstance(response, dict) else str(response or "")
    if not raw or not raw.strip():
        logger.warning("[RSS] Модель вернула пустой ответ")
        return None

    if looks_like_error(raw):
        logger.error("[RSS] Модель вернула ошибку вместо сводки: %s", raw.strip()[:300])
        return None

    parts = strip_markers(raw)
    return {
        "gist": compose_gist(parts["gist"]),
        "body": compose_body(parts["body"], parts["conclusions"]),
        "raw": raw,
    }
