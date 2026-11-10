# jumis/handlers/backup.py
"""
Админ-команды бэкапа базы данных.

/backup      — список копий + кнопка «Сделать сейчас»
/backup_now  — сделать копию сразу
/backups     — список копий без кнопки

Копии делает pg_dump внутри контейнера, ротация — до трёх последних.
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


def _list_text(backups, lang: str, with_hint: bool = True) -> str:
    """Таблица копий: имя, размер, время."""
    items = backups.list(limit=10)

    if not items:
        if lang == "ru":
            return ("💾 <b>БЭКАПЫ БАЗЫ</b>\n"
                    "─────────────────\n"
                    "Копий пока нет.\n"
                    f"Папка: <code>{backups.directory}</code>")
        return ("💾 <b>DATABASE BACKUPS</b>\n"
                "─────────────────\n"
                "No copies yet.\n"
                f"Folder: <code>{backups.directory}</code>")

    lines = ["💾 <b>БЭКАПЫ БАЗЫ</b>" if lang == "ru" else "💾 <b>DATABASE BACKUPS</b>",
             "─────────────────"]
    for item in items:
        stamp = item["created_at"].strftime("%d.%m %H:%M")
        lines.append(f"• <code>{item['name']}</code> · {item['size_human']} · {stamp}")

    lines.append("─────────────────")
    if lang == "ru":
        lines.append(f"Копий: {len(items)} · всего {backups.human_size(backups.total_size())}")
        lines.append(f"Храним последние: {backups.keep}")
        if with_hint:
            lines.append("\n♻️ Восстановление — вручную:\n"
                         f"<code>{backups.RESTORE_HINT}</code>")
    else:
        lines.append(f"Copies: {len(items)} · total {backups.human_size(backups.total_size())}")
        lines.append(f"Keep last: {backups.keep}")
        if with_hint:
            lines.append("\n♻️ Restore is manual:\n"
                         f"<code>{backups.RESTORE_HINT}</code>")
    return "\n".join(lines)


def _report_text(report: dict, lang: str) -> str:
    if not report.get("ok"):
        reason = report.get("error")
        if reason == "already_running":
            return "⏳ Бэкап уже идёт." if lang == "ru" else "⏳ A backup is already running."
        return (f"🚫 Бэкап не удался: {reason}" if lang == "ru"
                else f"🚫 Backup failed: {reason}")

    deleted = report.get("deleted") or []
    if lang == "ru":
        return (
            "💾 <b>БЭКАП ГОТОВ</b>\n"
            "─────────────────\n"
            f"Файл: <code>{report['name']}</code>\n"
            f"Размер: {report['size_human']}\n"
            f"Время: {report['duration']} с\n"
            f"Удалено старых: {len(deleted)}\n"
            f"Храним последние: {report.get('keep')}"
        )
    return (
        "💾 <b>BACKUP DONE</b>\n"
        "─────────────────\n"
        f"File: <code>{report['name']}</code>\n"
        f"Size: {report['size_human']}\n"
        f"Time: {report['duration']} s\n"
        f"Old copies deleted: {len(deleted)}\n"
        f"Keep last: {report.get('keep')}"
    )


def _buttons(lang: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text="💾 Сделать копию" if lang == "ru" else "💾 Create a copy",
            callback_data="backup:run",
        ),
    ]])


# -------------------------------------------------------------------- /backup

@router.message(Command("backup"))
async def backup_menu(message: types.Message, backups):
    """Список копий и кнопка запуска."""
    await typing(message)
    lang = _lang(message)
    if not await rights_verification(message.from_user.id, lang, message):
        return

    if not backups:
        await message.answer("🚫 Модуль бэкапа недоступен." if lang == "ru"
                             else "🚫 Backup service is unavailable.")
        return

    await message.answer(_list_text(backups, lang), reply_markup=_buttons(lang))


# ----------------------------------------------------------------- /backups

@router.message(Command("backups"))
async def backups_list(message: types.Message, backups):
    """Только список копий."""
    await typing(message)
    lang = _lang(message)
    if not await rights_verification(message.from_user.id, lang, message):
        return

    if not backups:
        await message.answer("🚫 Модуль бэкапа недоступен." if lang == "ru"
                             else "🚫 Backup service is unavailable.")
        return

    await message.answer(_list_text(backups, lang))


# ---------------------------------------------------------------- /backup_now

@router.message(Command("backup_now"))
async def backup_now(message: types.Message, backups):
    """Сделать копию немедленно, не дожидаясь 04:30."""
    await typing(message)
    lang = _lang(message)
    if not await rights_verification(message.from_user.id, lang, message):
        return

    if not backups:
        await message.answer("🚫 Модуль бэкапа недоступен." if lang == "ru"
                             else "🚫 Backup service is unavailable.")
        return

    logger.info("[Backup] Ручной запуск дампа: user=%s", message.from_user.id)
    await message.answer("💾 Снимаю дамп базы..." if lang == "ru" else "💾 Dumping the database...")

    report = await backups.create()
    await message.answer(_report_text(report, lang))


# ------------------------------------------------------- кнопка «Сделать копию»

@router.callback_query(F.data == "backup:run")
async def backup_confirm(callback: types.CallbackQuery, backups):
    """Копия по кнопке."""
    lang = _lang(callback)
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🔐 Нет доступа" if lang == "ru" else "🔐 Access denied", show_alert=True)
        logger.error("[Backup] Чужой пользователь нажал «Сделать копию»: %s", callback.from_user.id)
        return

    if not backups:
        await callback.answer("🚫 Нет модуля бэкапа" if lang == "ru" else "🚫 Backup unavailable")
        return

    await callback.answer("💾 Снимаю дамп..." if lang == "ru" else "💾 Dumping...")

    report = await backups.create()
    text = _report_text(report, lang)

    try:
        await callback.message.edit_text(text, reply_markup=None)
    except TelegramBadRequest:
        await callback.message.answer(text)
