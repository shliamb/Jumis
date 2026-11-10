# jumis/handlers/rss_menu.py
"""
Админка мировых новостей.

/rss — список фидов с состоянием и три кнопки:
    🧪 Сводка сейчас   — собрать и получить в чат
    🔍 Проверить все   — сходить по каждому фиду и показать, кто жив
    ✏️ Заменить список — прислать новый список текстом, старый замещается

Замена списка — ручная и полная, как и просил владелец: прислал текст,
получил отчёт по каждому адресу, список заменён целиком.
"""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from config import ADMIN_ID
from handlers.common import typing, rights_verification
from logs.set_logger import logger


router = Router()

STATUS_ICON = {
    "ok": "✅",
    "new": "🆕",
    "warn": "⚠️",
    "stale": "💤",
    "dead": "❌",
}

STATUS_TEXT = {
    "ok": {"ru": "ок", "en": "ok"},
    "new": {"ru": "не проверялся", "en": "not checked"},
    "warn": {"ru": "сбоит", "en": "flaky"},
    "stale": {"ru": "молчит", "en": "silent"},
    "dead": {"ru": "мёртв", "en": "dead"},
}


class RssStates(StatesGroup):
    waiting_list = State()


def _lang(message_or_cb) -> str:
    code = getattr(getattr(message_or_cb, "from_user", None), "language_code", "") or ""
    return "ru" if code.lower().startswith("ru") else "en"


def _stamp(value, fmt: str = "%d.%m %H:%M") -> str:
    return value.strftime(fmt) if value else "—"


def _plain_markdown(text: str) -> str:
    """Убирает markdown-разметку, чтобы сводка читалась в обычном тексте."""
    lines = []
    for line in (text or "").splitlines():
        clean = line.strip()
        if clean.startswith("###"):
            clean = clean.lstrip("#").strip()
        elif clean in ("---", "***", "___"):
            clean = "─────────────────"
        clean = clean.replace("**", "").replace("__", "")
        lines.append(clean)
    return "\n".join(lines).strip()


def _split(text: str, limit: int = 3500) -> list[str]:
    """Режет длинный текст по абзацам, не разрывая строки внутри блока."""
    chunks, current = [], ""
    for block in (text or "").split("\n\n"):
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(block) > limit:
            chunks.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        chunks.append(current)
    return chunks or [""]


def _buttons(lang: str):
    rows = [
        [
            types.InlineKeyboardButton(
                text="🧪 Сводка сейчас" if lang == "ru" else "🧪 Digest now",
                callback_data="rss:digest",
            ),
            types.InlineKeyboardButton(
                text="🔍 Проверить все" if lang == "ru" else "🔍 Check all",
                callback_data="rss:check",
            ),
        ],
        [
            types.InlineKeyboardButton(
                text="✏️ Заменить список" if lang == "ru" else "✏️ Replace list",
                callback_data="rss:edit",
            ),
        ],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _feeds_text(rss, feeds: list[dict], lang: str) -> str:
    if not feeds:
        if lang == "ru":
            return ("📰 <b>НОВОСТИ МИРА</b>\n"
                    "─────────────────\n"
                    "Источников нет. Нажми «✏️ Заменить список» и пришли адреса.")
        return ("📰 <b>WORLD NEWS</b>\n"
                "─────────────────\n"
                "No sources. Press «✏️ Replace list» and send feed URLs.")

    lines = ["📰 <b>НОВОСТИ МИРА</b>" if lang == "ru" else "📰 <b>WORLD NEWS</b>",
             "─────────────────"]

    for feed in feeds:
        status = feed.get("status") or "new"
        icon = STATUS_ICON.get(status, "•")
        title = feed.get("title") or feed.get("url")
        note = f" · {feed['note']}" if feed.get("note") else ""
        last = feed.get("last_item_at")
        freshness = _stamp(last) if last else ("нет записей" if lang == "ru" else "no items")
        status_text = STATUS_TEXT.get(status, {}).get(lang, status)
        lines.append(f"{icon} <b>{title}</b>{note}")
        lines.append(f"    <code>{feed['url']}</code>")
        lines.append(f"    {status_text} · {freshness}")

        if feed.get("last_error") and status in ("warn", "dead"):
            lines.append(f"    ⤷ <i>{feed['last_error'][:120]}</i>")

    ok = sum(1 for f in feeds if f.get("status") == "ok")
    lines.append("─────────────────")
    if lang == "ru":
        lines.append(f"Источников: {len(feeds)} · живых: {ok}")
        lines.append("Замена списка: пришли по строке на фид — <code>URL</code> или "
                     "<code>URL | заметка</code>.")
    else:
        lines.append(f"Sources: {len(feeds)} · alive: {ok}")
        lines.append("Replace list: one feed per line — <code>URL</code> or "
                     "<code>URL | note</code>.")
    return "\n".join(lines)


def _digest_text(digest: dict, cached: bool, lang: str) -> str:
    head = "🗞 <b>СВОДКА НОВОСТЕЙ</b>" if lang == "ru" else "🗞 <b>NEWS DIGEST</b>"
    stamp_from = _stamp(digest.get("period_from"))
    stamp_to = _stamp(digest.get("period_to"))
    meta = (f"<i>{stamp_from} — {stamp_to} · {digest.get('items_count')} записей · "
            f"{digest.get('feeds_used')} фидов</i>") if lang == "ru" else \
           (f"<i>{stamp_from} — {stamp_to} · {digest.get('items_count')} items · "
            f"{digest.get('feeds_used')} feeds</i>")

    parts = [head, meta]
    if cached:
        parts.append("<i>Свежая сводка уже была собрана — отдаю её.</i>" if lang == "ru"
                     else "<i>A recent digest already existed — showing it.</i>")
    if digest.get("feeds_failed"):
        failed = digest["feeds_failed"]
        parts.append(f"<i>Не ответили: {failed}</i>" if lang == "ru" else f"<i>No answer: {failed}</i>")

    parts.append("─────────────────")
    parts.append(_plain_markdown(digest.get("body") or ""))
    parts.append("─────────────────")
    parts.append(f"<i>id={digest.get('id')} · архив: rss_archive</i>")
    return "\n".join(parts)


def _replace_report(result: dict, lang: str) -> str:
    if not result.get("ok") and not result.get("accepted"):
        reason = result.get("error")
        return (f"🚫 Список не заменён: {reason}" if lang == "ru"
                else f"🚫 List not replaced: {reason}")

    accepted = result.get("accepted") or []
    rejected = result.get("rejected") or []

    if lang == "ru":
        lines = ["✅ <b>СПИСОК ФИДОВ ЗАМЕНЁН</b>", "─────────────────",
                 f"Принято: {len(accepted)}", ""]
        for feed in accepted:
            title = feed.get("title") or feed["url"]
            newest = _stamp(feed.get("newest")) if feed.get("newest") else "?"
            lines.append(f"• <b>{title}</b> — {feed.get('items')} записей, свежая {newest}")
            if feed.get("substituted"):
                lines.append(f"  ⤷ {feed['substituted']}")
        if rejected:
            lines.append("")
            lines.append(f"❌ Отклонено: {len(rejected)}")
            for feed in rejected:
                lines.append(f"• <code>{feed['url']}</code> — {feed.get('error')}")
            lines.append("")
            lines.append("Отклонённые не сохранены: старый список заменён только живыми.")
        return "\n".join(lines)

    lines = ["✅ <b>FEED LIST REPLACED</b>", "─────────────────",
             f"Accepted: {len(accepted)}", ""]
    for feed in accepted:
        title = feed.get("title") or feed["url"]
        newest = _stamp(feed.get("newest")) if feed.get("newest") else "?"
        lines.append(f"• <b>{title}</b> — {feed.get('items')} items, newest {newest}")
        if feed.get("substituted"):
            lines.append(f"  ⤷ {feed['substituted']}")
    if rejected:
        lines.append("")
        lines.append(f"❌ Rejected: {len(rejected)}")
        for feed in rejected:
            lines.append(f"• <code>{feed['url']}</code> — {feed.get('error')}")
        lines.append("")
        lines.append("Rejected feeds were not saved; the old list was replaced by the alive ones only.")
    return "\n".join(lines)


def _check_report(rows: list[dict], lang: str) -> str:
    alive = [row for row in rows if row.get("ok")]
    broken = [row for row in rows if not row.get("ok")]

    head = "🔍 <b>ПРОВЕРКА ИСТОЧНИКОВ</b>" if lang == "ru" else "🔍 <b>FEED CHECK</b>"
    lines = [head, "─────────────────"]

    for row in alive:
        newest = _stamp(row.get("newest")) if row.get("newest") else "?"
        lines.append(f"✅ {row.get('title')} — {row.get('items')} записей, свежая {newest}"
                     if lang == "ru" else
                     f"✅ {row.get('title')} — {row.get('items')} items, newest {newest}")

    for row in broken:
        lines.append(f"❌ {row.get('title')} — {row.get('error')}")

    lines.append("─────────────────")
    if lang == "ru":
        lines.append(f"Живых: {len(alive)} из {len(rows)}")
        if broken:
            lines.append("Мёртвые можно удалить: скажи агенту «убери фид <название>» "
                         "или пришли новый список.")
    else:
        lines.append(f"Alive: {len(alive)} of {len(rows)}")
        if broken:
            lines.append("Dead feeds can be removed: ask the agent to remove a feed, "
                         "or send a new list.")
    return "\n".join(lines)


# ------------------------------------------------------------------------- /rss

@router.message(Command("rss"))
async def rss_menu(message: types.Message, rss):
    """Список фидов и кнопки управления."""
    await typing(message)
    lang = _lang(message)
    if not await rights_verification(message.from_user.id, lang, message):
        return

    if not rss:
        await message.answer("🚫 Модуль новостей недоступен." if lang == "ru"
                             else "🚫 News service is unavailable.")
        return

    feeds = await rss.feeds()
    await message.answer(_feeds_text(rss, feeds, lang), reply_markup=_buttons(lang))


# --------------------------------------------------------------- кнопка: сводка

@router.callback_query(F.data == "rss:digest")
async def rss_digest_now(callback: types.CallbackQuery, rss):
    """Собрать сводку и показать её в чате."""
    lang = _lang(callback)
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🔐 Нет доступа" if lang == "ru" else "🔐 Access denied", show_alert=True)
        return

    if not rss:
        await callback.answer("🚫 Нет модуля новостей" if lang == "ru" else "🚫 News unavailable")
        return

    await callback.answer("🗞 Собираю сводку..." if lang == "ru" else "🗞 Collecting...")
    logger.info("[RSS] Ручной сбор сводки: user=%s", callback.from_user.id)

    result = await rss.collect()

    if not result.get("ok"):
        reason = result.get("error")
        if reason == "busy":
            text = "⏳ Сводка уже собирается." if lang == "ru" else "⏳ A digest is already being collected."
        else:
            failed = result.get("failed") or []
            tail = ""
            if failed:
                names = ", ".join(f"{r.get('title')} ({r.get('error')})" for r in failed)
                tail = f"\n\n<i>Не ответили: {names}</i>" if lang == "ru" else f"\n\n<i>No answer: {names}</i>"
            text = (f"🚫 Сводка не собралась: {reason}" if lang == "ru"
                    else f"🚫 Digest failed: {reason}") + tail
        await callback.message.answer(text)
        return

    digest = result.get("digest") or {}
    for chunk in _split(_digest_text(digest, bool(result.get("cached")), lang)):
        await callback.message.answer(chunk)


# -------------------------------------------------------------- кнопка: проверка

@router.callback_query(F.data == "rss:check")
async def rss_check(callback: types.CallbackQuery, rss):
    """Проверить каждый фид по-настоящему."""
    lang = _lang(callback)
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🔐 Нет доступа" if lang == "ru" else "🔐 Access denied", show_alert=True)
        return

    if not rss:
        await callback.answer("🚫 Нет модуля новостей" if lang == "ru" else "🚫 News unavailable")
        return

    await callback.answer("🔍 Проверяю..." if lang == "ru" else "🔍 Checking...")
    rows = await rss.check_all()
    await callback.message.answer(_check_report(rows, lang))


# ------------------------------------------------------- кнопка: замена списка

@router.callback_query(F.data == "rss:edit")
async def rss_edit_start(callback: types.CallbackQuery, state: FSMContext):
    """Начать полную замену списка фидов."""
    lang = _lang(callback)
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🔐 Нет доступа" if lang == "ru" else "🔐 Access denied", show_alert=True)
        return

    await state.set_state(RssStates.waiting_list)

    if lang == "ru":
        text = ("✏️ <b>ЗАМЕНА СПИСКА ФИДОВ</b>\n"
                "─────────────────\n"
                "Пришли новый список — по строке на источник:\n\n"
                "<code>https://example.com/rss</code>\n"
                "<code>https://example.com/rss | Китай, государственное</code>\n\n"
                "Можно дать адрес сайта целиком — фид найду сам.\n"
                "Каждый адрес проверю до сохранения и покажу отчёт.\n"
                "Старый список будет заменён целиком.\n\n"
                "Отмена: /rss")
    else:
        text = ("✏️ <b>REPLACE FEED LIST</b>\n"
                "─────────────────\n"
                "Send a new list, one source per line:\n\n"
                "<code>https://example.com/rss</code>\n"
                "<code>https://example.com/rss | China, state-owned</code>\n\n"
                "A website URL also works — its feed will be discovered.\n"
                "Every address is checked before saving, and you get a report.\n"
                "The old list will be replaced entirely.\n\n"
                "Cancel: /rss")
    await callback.message.answer(text)


@router.message(RssStates.waiting_list, F.text)
async def rss_edit_apply(message: types.Message, state: FSMContext, rss):
    """Принять список, проверить каждый фид и заменить."""
    lang = _lang(message)
    if message.from_user.id != ADMIN_ID:
        return

    if not rss:
        await state.clear()
        await message.answer("🚫 Модуль новостей недоступен.")
        return

    await state.clear()

    result = await rss.replace_from_text(message.text or "")
    for chunk in _split(_replace_report(result, lang)):
        await message.answer(chunk)

    try:
        feeds = await rss.feeds()
        await message.answer(_feeds_text(rss, feeds, lang), reply_markup=_buttons(lang))
    except TelegramBadRequest as e:
        logger.warning("[RSS] Не удалось показать список после замены: %s", e)
