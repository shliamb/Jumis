# jumis/handlers/cleaner.py
"""
Админ-команды управления уборкой истории.

/cleanup      — сухой отчёт + кнопка «Удалить» (ничего не удаляет сама)
/cleanup_run  — уборка сразу, без подтверждения
/digest_now   — порционная самаризация прямо сейчас (/digest_now 5)
/digests      — последние дайджесты (/digests [chat_id])
"""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

from config import ADMIN_ID
from handlers.common import typing, rights_verification
from logs.set_logger import logger


router = Router()


def _lang(message_or_cb) -> str:
    code = getattr(getattr(message_or_cb, "from_user", None), "language_code", "") or ""
    return "ru" if code.lower().startswith("ru") else "en"


def _scan_text(stats: dict, lang: str) -> str:
    total = stats["total"]
    delete = stats["candidates"]
    pct = (delete / total * 100) if total else 0
    empty_media = stats["empty"] + stats["media"]

    if lang == "ru":
        return (
            "🧹 <b>УБОРКА ИСТОРИИ — ПРЕДПРОСМОТР</b>\n"
            "─────────────────\n"
            f"Всего сообщений: {total}\n"
            f"Пустых и медиа без текста: {empty_media}\n"
            f"Болтовня («ок», «)», «привет»): {stats['chatter']}\n"
            f"Точных дублей: {stats['dups']}\n"
            "─────────────────\n"
            f"<b>Итого удалить: {delete} ({pct:.1f}%)</b>\n"
            f"Останется: {stats['after']}"
        )
    return (
        "🧹 <b>HISTORY CLEANUP — PREVIEW</b>\n"
        "─────────────────\n"
        f"Messages total: {total}\n"
        f"Empty and media without text: {empty_media}\n"
        f"Chatter ('ok', ')', 'hi'): {stats['chatter']}\n"
        f"Exact duplicates: {stats['dups']}\n"
        "─────────────────\n"
        f"<b>Total to delete: {delete} ({pct:.1f}%)</b>\n"
        f"Will remain: {stats['after']}"
    )


def _run_text(report: dict, lang: str) -> str:
    vacuum = "ok" if report.get("vacuum") else "skip"
    if lang == "ru":
        return (
            "🧹 <b>УБОРКА ВЫПОЛНЕНА</b>\n"
            "─────────────────\n"
            f"Мусор: {report['deleted_junk']}\n"
            f"Дубли: {report['deleted_dups']}\n"
            f"Сообщений: {report['total_before']} → {report['total_after']}\n"
            f"Размер таблицы: {report['size_before']} → {report['size_after']}\n"
            f"VACUUM: {vacuum}"
        )
    return (
        "🧹 <b>CLEANUP DONE</b>\n"
        "─────────────────\n"
        f"Junk: {report['deleted_junk']}\n"
        f"Duplicates: {report['deleted_dups']}\n"
        f"Messages: {report['total_before']} → {report['total_after']}\n"
        f"Table size: {report['size_before']} → {report['size_after']}\n"
        f"VACUUM: {vacuum}"
    )


def _digest_text(report: dict, lang: str) -> str:
    if report.get("skipped"):
        reason = report.get("reason")
        if reason == "empty_backlog":
            return "🗂 Нечего сжимать: всё свежее или уже сжато." if lang == "ru" else \
                   "🗂 Nothing to compress: history is fresh or already digested."
        if reason == "already_running":
            return "⏳ Прогон уже идёт." if lang == "ru" else "⏳ A run is already in progress."
        return f"🗂 Пропущено: {reason}"

    if lang == "ru":
        return (
            "🗂 <b>САМАРИЗАЦИЯ</b>\n"
            "─────────────────\n"
            f"Батчей сжато: {report.get('batches', 0)}\n"
            f"Сообщений переработано: {report.get('messages', 0)}\n"
            f"Чатов затронуто: {report.get('chats', 0)}\n"
            f"Ошибок: {report.get('failed', 0)}"
        )
    return (
        "🗂 <b>DIGEST RUN</b>\n"
        "─────────────────\n"
        f"Batches: {report.get('batches', 0)}\n"
        f"Messages digested: {report.get('messages', 0)}\n"
        f"Chats touched: {report.get('chats', 0)}\n"
        f"Failed: {report.get('failed', 0)}"
    )


# ---------------------------------------------------------------- /cleanup (scan)

@router.message(Command("cleanup"))
async def cleanup_preview(message: types.Message, cleaner):
    """Сухой отчёт + кнопка подтверждения."""
    await typing(message)
    lang = _lang(message)
    if not await rights_verification(message.from_user.id, lang, message):
        return

    if not cleaner:
        await message.answer("🚫 Модуль чистки недоступен." if lang == "ru"
                             else "🚫 Cleaner service is unavailable.")
        return

    try:
        stats = await cleaner.scan()
    except Exception as e:
        logger.error("[cleanup] Ошибка сканирования: %s", e, exc_info=True)
        await message.answer(f"🚫 Ошибка сканирования: {e}" if lang == "ru"
                             else f"🚫 Scan failed: {e}")
        return

    buttons = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text="✅ Удалить" if lang == "ru" else "✅ Delete",
            callback_data="clean:run",
        ),
        types.InlineKeyboardButton(
            text="❌ Отмена" if lang == "ru" else "❌ Cancel",
            callback_data="clean:cancel",
        ),
    ]])
    await message.answer(_scan_text(stats, lang), reply_markup=buttons)


@router.callback_query(F.data == "clean:run")
async def cleanup_confirm(callback: types.CallbackQuery, cleaner):
    """Подтверждение уборки по кнопке."""
    lang = _lang(callback)
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🔐 Нет доступа" if lang == "ru" else "🔐 Access denied", show_alert=True)
        logger.error("[cleanup] Чужой пользователь нажал «Удалить»: %s", callback.from_user.id)
        return

    if not cleaner:
        await callback.answer("🚫 Нет модуля чистки" if lang == "ru" else "🚫 Cleaner unavailable")
        return

    await callback.answer("🧹 Убираюсь..." if lang == "ru" else "🧹 Cleaning...")

    try:
        report = await cleaner.run_hygiene()
    except Exception as e:
        logger.error("[cleanup] Ошибка уборки: %s", e, exc_info=True)
        await callback.message.answer(f"🚫 Ошибка уборки: {e}" if lang == "ru"
                                      else f"🚫 Cleanup failed: {e}")
        return

    try:
        await callback.message.edit_text(_run_text(report, lang), reply_markup=None)
    except TelegramBadRequest:
        await callback.message.answer(_run_text(report, lang))


@router.callback_query(F.data == "clean:cancel")
async def cleanup_cancel(callback: types.CallbackQuery):
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("Отменено" if _lang(callback) == "ru" else "Cancelled")


# ------------------------------------------------------------ /cleanup_run (сразу)

@router.message(Command("cleanup_run"))
async def cleanup_run_now(message: types.Message, cleaner):
    """Уборка без предпросмотра — для тех, кто уже посмотрел /cleanup."""
    await typing(message)
    lang = _lang(message)
    if not await rights_verification(message.from_user.id, lang, message):
        return

    if not cleaner:
        await message.answer("🚫 Модуль чистки недоступен." if lang == "ru"
                             else "🚫 Cleaner service is unavailable.")
        return

    logger.info("[cleanup] Ручной запуск уборки: user=%s", message.from_user.id)
    try:
        report = await cleaner.run_hygiene()
    except Exception as e:
        logger.error("[cleanup] Ошибка уборки: %s", e, exc_info=True)
        await message.answer(f"🚫 Ошибка уборки: {e}" if lang == "ru" else f"🚫 Cleanup failed: {e}")
        return

    await message.answer(_run_text(report, lang))


# ------------------------------------------------------------------- /digest_now

@router.message(Command("digest_now"))
async def digest_now(message: types.Message, cleaner):
    """Ручной прогон самаризации: /digest_now [N] — сколько батчей (по умолчанию 5)."""
    await typing(message)
    lang = _lang(message)
    if not await rights_verification(message.from_user.id, lang, message):
        return

    if not cleaner:
        await message.answer("🚫 Модуль чистки недоступен." if lang == "ru"
                             else "🚫 Cleaner service is unavailable.")
        return

    parts = (message.text or "").split(maxsplit=1)
    batches = 5
    if len(parts) > 1:
        try:
            batches = max(1, min(int(parts[1].strip()), 20))
        except ValueError:
            batches = 5

    logger.info("[cleanup] Ручная самаризация: батчей=%d", batches)
    await message.answer(
        f"🗂 Сжимаю до {batches} батчей, это может занять пару минут..." if lang == "ru"
        else f"🗂 Compressing up to {batches} batches, this may take a couple of minutes..."
    )

    try:
        report = await cleaner.run_digest(max_batches=batches)
    except Exception as e:
        logger.error("[cleanup] Ошибка самаризации: %s", e, exc_info=True)
        await message.answer(f"🚫 Ошибка: {e}" if lang == "ru" else f"🚫 Failed: {e}")
        return

    await message.answer(_digest_text(report, lang))


# ---------------------------------------------------------------------- /digests

@router.message(Command("digests"))
async def digests_list(message: types.Message, cleaner):
    """Список дайджестов: /digests [chat_id]."""
    await typing(message)
    lang = _lang(message)
    if not await rights_verification(message.from_user.id, lang, message):
        return

    if not cleaner:
        await message.answer("🚫 Модуль чистки недоступен." if lang == "ru"
                             else "🚫 Cleaner service is unavailable.")
        return

    parts = (message.text or "").split(maxsplit=1)
    chat_id = None
    if len(parts) > 1:
        try:
            chat_id = int(parts[1].strip())
        except ValueError:
            chat_id = None

    try:
        rows = await cleaner.list_digests(chat_id=chat_id, limit=5)
    except Exception as e:
        logger.error("[cleanup] Ошибка чтения дайджестов: %s", e, exc_info=True)
        await message.answer(f"🚫 Ошибка: {e}" if lang == "ru" else f"🚫 Failed: {e}")
        return

    if not rows:
        await message.answer("🗂 Дайджестов пока нет." if lang == "ru" else "🗂 No digests yet.")
        return

    lines = []
    for row in rows:
        period_from = row.get("period_from")
        period_to = row.get("period_to")
        period = ""
        if period_from and period_to:
            period = f"{period_from:%d.%m.%Y}—{period_to:%d.%m.%Y}"
        lines.append(
            f"<b>#{row.get('id')}</b> · {row.get('chat_id')} · {period} · {row.get('msg_count')} смс\n"
            f"{str(row.get('summary') or '').strip()[:600]}"
        )

    await message.answer("🗂 <b>ДАЙДЖЕСТЫ</b>\n\n" + "\n\n".join(lines))
