#! master/handler/admin.py
import os
import sys
import json
import asyncio
from handlers.common import typing
from logs.set_logger import set_logger
logger = set_logger(name="admin")
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError, TelegramBadRequest, ClientDecodeError
# from aiogram.types import ReplyKeyboardRemove
from config import DOWNLOAD, ADMIN_ID, PATH_LOGS, DOCKER
from database.create_tables import create_tables_in_db
from database.deleted_tables_db import drop_all_tables_and_reset_schema
from handlers.common import rights_verification
from pathlib import Path
from io import BytesIO


router = Router()



#### ADMIN MENU ####
####################

# ADMIN MENU:
@router.message(Command('admin'))
async def admin_menu(message: types.Message):
    await typing(message)

    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return
    
    admin_menu_ru = "\n".join([
        "<b>🎛 АДМИН-МЕНЮ</b>",
        "─────────────────",
        "<b>📋 ЛОГИ СИСТЕМЫ:</b>",
        "├ /logs — Скачать логи",
        "└ /dLogs — Очистить логи",
        "",
        "<b>🤖 УПРАВЛЕНИЕ AI:</b>",
        "├ /getMod — Список моделей",
        "└ /setMod — Сменить модель",
        "",
        "<b>⚙️ БОТ И СЕРВИС:</b>",
        "├ /reset — Перезапуск бота",
        "└ /uplLLM — Обновить LiteLLM",
        "",
        "<b>📤 БЭКАП (БД ➔ JSON):</b>",
        "├ /crTabDb — Создать таблицы",
        "└ /dowjson — Скачать JSON",
        "",
        "<b>📥 ИМПОРТ (JSON ➔ БД):</b>",
        "├ /up_users_cat — Кат. юзеров",
        "├ /up_facts_cat — Кат. фактов",
        "├ /up_users — Юзеры",
        "├ /up_facts — Факты",
        "├ /up_messages — Сообщения",
        "└ /up_tasks — Задачи",
        "",
        "<b>⚠️ ОПАСНАЯ ЗОНА:</b>",
        "└ /allDel — Сброс БД ☠️",
    ])

    admin_menu_en = "\n".join([
        "<b>🎛 ADMIN MENU</b>",
        "─────────────────",
        "<b>📋 SYSTEM LOGS:</b>",
        "├ /logs — Download logs",
        "└ /dLogs — Clear logs",
        "",
        "<b>🤖 AI MANAGEMENT:</b>",
        "├ /getMod — List models",
        "└ /setMod — Switch model",
        "",
        "<b>⚙️ BOT & SERVICE:</b>",
        "├ /reset — Restart bot",
        "└ /uplLLM — Update LiteLLM",
        "",
        "<b>📤 BACKUP (DB ➔ JSON):</b>",
        "├ /crTabDb — Init tables",
        "└ /dowjson — Export JSON",
        "",
        "<b>📥 RESTORE (JSON ➔ DB):</b>",
        "├ /up_users_cat — User cats",
        "├ /up_facts_cat — Fact cats",
        "├ /up_users — Users",
        "├ /up_facts — Facts",
        "├ /up_messages — Messages",
        "└ /up_tasks — Tasks",
        "",
        "<b>⚠️ DANGER:</b>",
        "└ /allDel — Wipe DB ☠️",
    ])

    if lang == "ru": await message.answer(admin_menu_ru, parse_mode="HTML")
    else: await message.answer(admin_menu_en, parse_mode="HTML")







async def _delayed_restart(bot):
    """Фоновая процедура корректного завершения и перезапуска."""
    # 1. Даем 1.5 секунды, чтобы хэндлер завершился и Telegram получил ответ 200 OK
    await asyncio.sleep(1.5)
    
    # 2. Мягко закрываем HTTP-сессию бота
    try:
        await bot.session.close()
    except Exception as e:
        logger.warning(f"Ошибка при закрытии сессии бота: {e}")

    # 3. Разделение веток перезапуска
    if DOCKER:
        # Не проверял, позже проверю.. пока на горячую все..
        logger.info("🚀 [DOCKER RESTART]: Завершаем процесс sys.exit(0)...")
        # В Docker с политикой restart: always / unless-stopped
        # контейнер мгновенно пересоздастся в чистом окружении
        sys.exit(0)
    else:
        logger.info("💻 [LOCAL RESTART]: Подменяем процесс через os.execv...")
        # В VS Code подменяем процесс Python прямо в текущей консоли
        os.execv(sys.executable, [sys.executable] + sys.argv)


# RESET SYSTEM
@router.message(Command("reset"))
async def reset_system(message: types.Message, bot):
    """ Перезагрузка системы """
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return

    text = "🔄 *Перезапускаю систему...*" if lang == "ru" else "🔄 *Restarting system...*"
    await message.answer(text, parse_mode="Markdown")

    # Запускаем перезапуск асинхронно, чтобы не блокировать завершение хэндлера
    asyncio.create_task(_delayed_restart(bot))


# UPDATE LiteLLM & ML Stack via UV
@router.message(Command("uplLLM"))
async def update_litellm(message: types.Message):
    """ Комплексное обновление LiteLLM и ML-зависимостей """
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return

    start_text = "⏳ *Обновляю LiteLLM и ML-стек через uv...*" if lang == "ru" else "⏳ *Updating LiteLLM & ML stack via uv...*"
    status_msg = await message.answer(start_text, parse_mode="Markdown")

    try:
        # # Обновляем всю связку единым резолвером
        # process = await asyncio.create_subprocess_exec(
        #     "uv", "pip", "install", "--upgrade",
        #     "litellm",
        #     "huggingface_hub",
        #     "transformers",
        #     "tokenizers",
        #     "sentence-transformers",
        #     stdout=asyncio.subprocess.PIPE,
        #     stderr=asyncio.subprocess.PIPE
        # )

        # Обновляем ТОЛЬКО LiteLLM, не прикасаясь к тяжелым зависимостям вектора
        process = await asyncio.create_subprocess_exec(
            "uv", "pip", "install", "--upgrade", "litellm", "--no-deps",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            success_text = (
                "⚡️ *LiteLLM и ML-зависимости успешно обновлены!*\n\n"
                "Выполните /reset, чтобы перезапустить бота."
            ) if lang == "ru" else (
                "⚡️ *LiteLLM & ML stack successfully updated!*\n\n"
                "Run /reset to restart the bot."
            )
            await status_msg.edit_text(success_text, parse_mode="Markdown")
        else:
            err_output = stderr.decode().strip() or stdout.decode().strip()
            short_err = "\n".join(err_output.splitlines()[-4:])
            
            fail_text = (
                f"❌ *Ошибка uv pip:*\n```\n{short_err}\n```"
            ) if lang == "ru" else (
                f"❌ *uv pip error:*\n```\n{short_err}\n```"
            )
            await status_msg.edit_text(fail_text, parse_mode="Markdown")

    except FileNotFoundError:
        err_msg = (
            "❌ *Ошибка:* Команда `uv` не найдена в системе." 
            if lang == "ru" else 
            "❌ *Error:* `uv` command not found in PATH."
        )
        await status_msg.edit_text(err_msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Сбой при обновлении пакетов через uv: {e}")
        err_msg = f"❌ *Сбой выполнения:* `{e}`" if lang == "ru" else f"❌ *Execution failure:* `{e}`"
        await status_msg.edit_text(err_msg, parse_mode="Markdown")



####### MODELS ########

def format_models_to_html(models_data: dict | list | str) -> str:
    """
    Форматирует структуру моделей в красивый HTML-список.
    Каждое название модели оборачивается в <code>...</code>,
    что позволяет скопировать её в буфер обмена в один клик/тап в Telegram.
    """
    if isinstance(models_data, dict):
        lines = []
        for provider, models in models_data.items():
            lines.append(f"<b>{provider}:</b>")
            if isinstance(models, list):
                for model in models:
                    lines.append(f"• <code>{model}</code>")
            else:
                lines.append(f"• <code>{models}</code>")
            lines.append("")  # Пустая строка для визуального разделения провайдеров
        return "\n".join(lines).strip()

    elif isinstance(models_data, list):
        return "\n".join([f"• <code>{model}</code>" for model in models_data])

    # Если пришла просто строка или другой тип
    return f"<code>{models_data}</code>"


def split_by_lines(text: str, max_length: int = 3800) -> list[str]:
    """
    Безопасно разбивает готовый HTML-текст по строкам (по \n).
    Так как каждая модель занимает ровно одну строку (`• <code>model</code>`),
    разрез по строкам ГАРАНТИРУЕТ, что ни один HTML-тег не будет разорван пополам.
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    current_chunk = []
    current_length = 0

    for line in text.split("\n"):
        # +1 учитывает символ переноса строки \n
        if current_length + len(line) + 1 > max_length:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_length = len(line)
        else:
            current_chunk.append(line)
            current_length += len(line) + 1

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


# GET MODELS
@router.message(Command("getMod"))
async def get_models_llm(message: types.Message, llm):
    """Возвращает список доступных моделей LLM с кликабельным копированием."""
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return

    # 1. Заголовок
    header = (
        "<b>Доступные модели LLM:</b>\n\n"
        if lang == "ru"
        else "<b>Available LLM models:</b>\n\n"
    )

    # 2. Преобразуем данные моделей в удобный список с <code> model </code>
    formatted_models = format_models_to_html(llm.available_models)
    full_text = header + formatted_models

    # 3. Нарезаем текст на куски, если моделей слишком много (лимит ~3800 символов)
    chunks = split_by_lines(full_text, max_length=3800)

    # 4. Последовательно отправляем сообщения
    try:
        for chunk in chunks:
            if chunk.strip():
                await message.answer(chunk, parse_mode="HTML")
    except TelegramBadRequest as e:
        logger.error(f"Ошибка при отправке списка моделей в Telegram: {e}")
        await message.answer(
            "⚠️ Ошибка при формировании списка моделей."
            if lang == "ru"
            else "⚠️ Error displaying models list."
        )



# FSM STATES
class SetModelStates(StatesGroup):
    waiting_for_model = State()


# HELPER FUNCTIONS
def _extract_flat_models_list(available_models: dict | list) -> list[str]:
    """Извлекает плоский список всех моделей, независимо от структуры (dict или list)."""
    if isinstance(available_models, dict):
        flat_list = []
        for models in available_models.values():
            if isinstance(models, list):
                flat_list.extend(models)
            elif isinstance(models, str):
                flat_list.append(models)
        return flat_list
    elif isinstance(available_models, list):
        return available_models
    return [str(available_models)]


# START SET MODEL COMMAND
@router.message(Command("setMod"))
async def set_llm_model(message: types.Message, state: FSMContext):
    """Инициализация смены модели по умолчанию."""
    await typing(message)
    
    user_id = message.from_user.id
    lang = message.from_user.language_code

    if not await rights_verification(user_id, lang, message):
        return

    await state.set_state(SetModelStates.waiting_for_model)
    
    prompt = (
        "✏️ <b>Введите название LLM модели:</b>\n\n"
        "<i>Для отмены отправьте «отмена» или «cancel».</i>"
        if lang == "ru"
        else "✏️ <b>Enter the LLM model name:</b>\n\n"
        "<i>To cancel, type 'cancel'.</i>"
    )
    await message.answer(prompt, parse_mode="HTML")


# WRITE TO DB (FSM HANDLER)
@router.message(SetModelStates.waiting_for_model)
async def write_name_llm_to_db(
    message: types.Message, 
    state: FSMContext, 
    llm, 
    db_users
):
    """Валидация и запись выбранной модели в БД."""
    lang = message.from_user.language_code
    user_id = message.from_user.id
    raw_input = message.text.strip() if message.text else ""

    # 1. Проверка на пустой ввод
    if not raw_input:
        msg = "⚠️ Пожалуйста, введите текстовое название модели." if lang == "ru" else "⚠️ Please enter a valid model name."
        await message.answer(msg)
        return

    # 2. Обработка отмены
    if raw_input.lower() in ["cancel", "cansel", "отмена", "отменить"]:
        await state.clear()
        logger.info(f"User {user_id} cancelled model selection.")
        msg = "🚫 Ввод модели отменён." if lang == "ru" else "🚫 Model selection cancelled."
        await message.answer(msg)
        return

    # 3. Достаём полный список моделей и ищем совпадение (без учёта регистра)
    all_models = _extract_flat_models_list(llm.available_models)
    matched_model = next((m for m in all_models if m.lower() == raw_input.lower()), None)

    if not matched_model:
        logger.warning(f"User {user_id} entered invalid model: {raw_input}")
        msg = (
            f"❌ Модель <code>{raw_input}</code> не найдена в списке доступных.\n\n"
            f"Попробуйте ещё раз или введите «отмена»."
            if lang == "ru"
            else f"❌ Model <code>{raw_input}</code> is not in the available models list.\n\n"
            f"Please try again or send 'cancel'."
        )
        await message.answer(msg, parse_mode="HTML")
        return

    # 4. Обновление пользователя в БД
    update_data = {"tg_id": user_id, "model_default": matched_model}
    
    if not await db_users.db_update_user(update_data):
        logger.error(f"Failed to update default model for user {user_id} in DB.")
        msg = "⚠️ Ошибка при сохранении в базу данных." if lang == "ru" else "⚠️ Error saving model to database."
        await message.answer(msg)
        return

    # 5. Обновляем кеш/модели в LLM сервисе
    if hasattr(llm, "refresh_llm_models"):
        await llm.refresh_llm_models()

    # 6. Успешный финал
    logger.info(f"User {user_id} updated default model to: {matched_model}")
    success_msg = (
        f"✅ Модель по умолчанию успешно изменена на <code>{matched_model}</code>"
        if lang == "ru"
        else f"✅ Default model successfully updated to <code>{matched_model}</code>"
    )
    await message.answer(success_msg, parse_mode="HTML")
    
    # Сбрасываем состояние FSM
    await state.clear()







# GET LOGS
@router.message(Command("logs"))
async def get_logs_bot(message: types.Message):
    """ Отдает все файлы логов в папке logs """
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return

    log_dir = Path(PATH_LOGS)
    sent_any = False

    async def send_as_utf8(path: Path):
        # читаем файл
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            text = raw.decode('cp1251', errors='replace')

        # кладём в буфер
        buf = BytesIO(text.encode('utf-8'))
        buf.name = path.stem + '_utf8.txt'   # чтоб iOS показал превью

        await message.reply_document(
            document=types.input_file.BufferedInputFile(buf.getvalue(), filename=buf.name),
            caption=path.stem
        )

    for entry in log_dir.iterdir():
        if entry.is_file() and entry.stat().st_size:
            try:
                await send_as_utf8(entry)
                sent_any = True
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"cant send {entry}: {e}")

    if not sent_any:
        if lang == "ru": await message.answer("🚫 Файлов ведения журнала нет или они пусты")
        else: await message.answer("🚫 There are no logging files or they are empty")

    
# CREATE TABLES in DB
@router.message(Command('crTabDb'))
async def create_tebles_in_db_admin(message: types.Message):
    """ Нарезает таблицы в базе """
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return

    if create_tables_in_db(): # Синхронная
        if lang == "ru": await message.answer("🎉 Таблицы в базе данных были успешно созданы")
        else: await message.answer("🎉 Tables in the DB were created successfully")
    else:
        if lang == "ru": await message.answer("🚫 Ошибка при создании таблиц базы данных. Проверьте логи для получения подробной информации")
        else: await message.answer("🚫 Error creating DB tables. Check logs for details")


# DOWNLOAD JSON DATA out DB
@router.message(Command('dowjson'))
async def download_json(message: types.Message, json_back):
    """ Скачать из базы все таблицы в JSON """
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return

    await json_back.db_to_json()




# RESTORE TABLE IN JSON FILE

class RestoreState(StatesGroup):
    waiting_file = State()


async def answer_bot(message: types.Message, lang: str, name_action: str):
    answer_ru = (
        f"📥 <b>Режим восстановления: {name_action}</b>\n\n"
        f"Отправь мне `.json` файл с бэкапом `{name_action}`.\n"
        "Для отмены нажми /cancel."
    )
    answer_en = (
        f"📥 <b>Recovery mode: {name_action}</b>\n\n"
        f"Send me the `.json` backup file for `{name_action}`.\n"
        "To cancel, press /cancel."
    )
    await message.answer(answer_ru if lang == "ru" else answer_en)


# 01. Категории пользователей
@router.message(Command("up_users_cat"))
async def cmd_up_users_cat(message: types.Message, state: FSMContext):
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return
    name_action = "01_user_categories"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, lang, name_action)


# 02. Категории фактов (памяти)
@router.message(Command("up_facts_cat"))
async def cmd_up_facts_cat(message: types.Message, state: FSMContext):
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return
    name_action = "02_facts_categories"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, lang, name_action)


# 03. Пользователи
@router.message(Command("up_users"))
async def cmd_up_users(message: types.Message, state: FSMContext):
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return
    name_action = "03_users"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, lang, name_action)


# 04. Факты (память)
@router.message(Command("up_facts"))
async def cmd_up_facts(message: types.Message, state: FSMContext):
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return
    name_action = "04_facts"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, lang, name_action)


# 05. Сообщения (история переписки)
@router.message(Command("up_messages"))
async def cmd_up_messages(message: types.Message, state: FSMContext):
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return
    name_action = "05_messages"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, lang, name_action)


# 06. Задачи
@router.message(Command("up_tasks"))
async def cmd_up_tasks(message: types.Message, state: FSMContext):
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return
    name_action = "06_tasks"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, lang, name_action)


# Отказ
@router.message(Command("cancel"), RestoreState.waiting_file)
async def cancel_restore(message: types.Message, state: FSMContext):
    await state.clear()
    lang = message.from_user.language_code
    msg = "❌ Восстановление отменено." if lang == "ru" else "❌ Recovery canceled."
    await message.answer(msg)


# Заливаем в базу
@router.message(RestoreState.waiting_file, F.document)
async def process_json_import_file(
    message: types.Message, 
    state: FSMContext, 
    bot, 
    json_back
):
    lang = message.from_user.language_code
    document = message.document
    send_data = await state.get_data()
    name_action = send_data.get("name_action", "table") # хз нахер table
    
    os.makedirs(DOWNLOAD, exist_ok=True)

    # Проверка формата файла
    if not document or not document.file_name.endswith('.json'):
        msg = (
            f"🚫 Прикрепите JSON-файл для <b>{name_action}</b>" 
            if lang == "ru" 
            else f"🚫 Please attach a JSON file for <b>{name_action}</b>"
        )
        await message.answer(msg)
        return

    # Скачивание файла
    filepath = os.path.join(DOWNLOAD, document.file_name)
    await bot.download(document, destination=filepath)

    wait_msg = "⏳ Обрабатываю данные..." if lang == "ru" else "⏳ Processing data..."
    await message.answer(wait_msg)

    # Запуск универсального метода восстановления
    success, good_count, bad_count = await json_back.restore_table_from_file(name_action, filepath)

    # Удаление временного файла
    if os.path.exists(filepath):
        os.remove(filepath)

    # Сброс состояния FSM
    await state.clear()

    # Формирование локализованного итогового ответа
    if lang == "ru":
        if success:
            res_text = f"🎉 <b>Успешно!</b> Таблица <code>{name_action}</code>:\nУспешно занесено: <b>{good_count}</b>"
        else:
            res_text = f"🚫 <b>Ошибка импорта {name_action}!</b>\nУспешно: <b>{good_count}</b>, с ошибками: <b>{bad_count}</b>"
    else:
        if success:
            res_text = f"🎉 <b>Success!</b> Table <code>{name_action}</code>:\nUploaded: <b>{good_count}</b>"
        else:
            res_text = f"🚫 <b>Import error for {name_action}!</b>\nSuccess: <b>{good_count}</b>, Failed: <b>{bad_count}</b>"

    await message.answer(res_text)





# FAST DELETING all TABLES in DB
@router.message(Command('allDel'))
async def delete_all_tables_in_db_admin(message: types.Message):
    """ Быстрое удаление всех таблиц базы данных """
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return

    if await drop_all_tables_and_reset_schema():
        if lang == "ru": await message.answer("🎉 Таблицы в базе данных были успешно удалены")
        else: await message.answer("🎉 The tables in the database were successfully deleted")
    else:
        if lang == "ru": await message.answer("🚫 Ошибка при удалении всех таблиц")
        else: await message.answer("🚫 Error deleting all tables")
        

# CLEAR LOGS
@router.message(Command("dLogs"))
async def admin_clear_logs(message: types.Message):
    """ Очищение всех фалов в папке logs """
    await typing(message)
    lang = message.from_user.language_code
    user_id = message.from_user.id
    if not await rights_verification(user_id, lang, message): return

    data_folder = Path(PATH_LOGS)
    for entry in data_folder.iterdir():
        if entry.is_file() and entry.stat().st_size > 0:  # Проверяем, что файл не пустой
            file_path = str(entry.absolute())  # Получаем абсолютный путь

            try:
                with open(file_path, 'w'):
                    pass
                if lang == "ru": await message.answer(f"🗑 Файл '{file_path}' был очищен")
                else: await message.answer(f"🗑 The '{file_path}' file has been clearing.")
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Error clearing file log: {file_path}: {e}")
                if lang == "ru": await message.answer("🚫 Ошибка при удалении логов")
                else: await message.answer("🚫 Error deleting logs")



