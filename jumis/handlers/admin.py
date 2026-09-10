#! master/handler/admin.py
from handlers.common import typing
from logs.set_logger import set_logger
logger = set_logger(name="admin")
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
# from aiogram.types import ReplyKeyboardRemove
from config import DOWNLOAD, ADMIN_ID, PATH_LOGS
from database.create_tables import create_tables_in_db
from database.deleted_tables_db import drop_all_tables_and_reset_schema
from handlers.common import rights_verification
import os
import asyncio
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
        "<b>📝 ЛОГИ:</b>",
        "├ /logs — Скачать",
        "└ /dLogs — Очистить",
        "",
        "<b>📤 БЭКАП (DB ➔ JSON):</b>",
        "├ /crTabDb — Создать таблицы",
        "└ /dowjson — Скачать JSON",
        "",
        "<b>📥 ИМПОРТ (JSON ➔ DB):</b>",
        "├ /up_users_cat — Кат. юзеров",
        "├ /up_facts_cat — Кат. фактов",
        "├ /up_users — Юзеры",
        "├ /up_facts — Факты",
        "├ /up_messages — Сообщения",
        "└ /up_tasks — Задачи",
        "",
        "<b>🗑 ОПАСНО:</b>",
        "└ /allDel — Сброс БД ☠️",
    ])

    admin_menu_en = "\n".join([
        "<b>🎛 ADMIN MENU</b>",
        "─────────────────",
        "<b>📝 LOGS:</b>",
        "├ /logs — Download",
        "└ /dLogs — Clear logs",
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
        "<b>🗑 DANGER:</b>",
        "└ /allDel — Wipe DB ☠️",
    ])

    # menu_ru = "\n".join(admin_menu_ru)
    # menu_en = "\n".join(admin_menu_en)

    if lang == "ru": await message.answer(admin_menu_ru, parse_mode="HTML")
    else: await message.answer(admin_menu_en, parse_mode="HTML")


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

    # if create_tables_in_db(): # Синхронная
    #     if lang == "ru": await message.answer("🎉 Таблицы в базе данных были успешно созданы")
    #     else: await message.answer("🎉 Tables in the DB were created successfully")
    # else:
    #     if lang == "ru": await message.answer("🚫 Ошибка при создании таблиц базы данных. Проверьте логи для получения подробной информации")
    #     else: await message.answer("🚫 Error creating DB tables. Check logs for details")






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
    name_action = "01_user_categories"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, message.from_user.language_code, name_action)


# 02. Категории фактов (памяти)
@router.message(Command("up_facts_cat"))
async def cmd_up_facts_cat(message: types.Message, state: FSMContext):
    name_action = "02_facts_categories"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, message.from_user.language_code, name_action)


# 03. Пользователи
@router.message(Command("up_users"))
async def cmd_up_users(message: types.Message, state: FSMContext):
    name_action = "03_users"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, message.from_user.language_code, name_action)


# 04. Факты (память)
@router.message(Command("up_facts"))
async def cmd_up_facts(message: types.Message, state: FSMContext):
    name_action = "04_facts"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, message.from_user.language_code, name_action)


# 05. Сообщения (история переписки)
@router.message(Command("up_messages"))
async def cmd_up_messages(message: types.Message, state: FSMContext):
    name_action = "05_messages"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, message.from_user.language_code, name_action)


# 06. Задачи
@router.message(Command("up_tasks"))
async def cmd_up_tasks(message: types.Message, state: FSMContext):
    name_action = "06_tasks"
    await state.update_data(name_action=name_action)
    await state.set_state(RestoreState.waiting_file)
    await answer_bot(message, message.from_user.language_code, name_action)


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





# # RESTORE TABLE in JSON file

# # Объявляем группу состояний
# class RestoreState(StatesGroup):
#     waiting_file = State()


# # Абстрактная функция для всех
# async def answer_bot(message: types.Message, lang: str, name_action: str):
#     answer_ru = (
#         f"📥 <b>Режим восстановления таблицы {name_action}</b>\n\n"
#         f"Отправь мне `.json` файл с бэкапом `{name_action}`.\n"
#         "Для отмены нажми /cancel."
#     )
#     answer_en = (
#         f"📥 <b>Table recovery mode {name_action}</b>\n\n"
#         f"Send me the `.json` file with the backup `{name_action}`.\n"
#         "To cancel, press /cancel."
#     )
#     answ_text = answer_ru if lang == "ru" else answer_en
#     await message.answer(answ_text)


# # Нажатие /up_tasks — включаем режим ожидания файла
# @router.message(Command("up_tasks"))
# async def cmd_up_tasks(message: types.Message, state: FSMContext):
#     name_action="06_tasks"
#     await state.update_data(name_action=name_action)
#     await state.set_state(RestoreState.waiting_file)
#     await answer_bot(
#         message, 
#         lang=message.from_user.language_code, 
#         name_action=name_action
#     )


# # Нажатие /up_users_cat — включаем режим ожидания файла
# @router.message(Command("up_users_cat"))
# async def cmd_up_users_cat(message: types.Message, state: FSMContext):
#     name_action="01_user_categories"
#     await state.update_data(name_action=name_action)
#     await state.set_state(RestoreState.waiting_file)
#     await answer_bot(
#         message, 
#         lang=message.from_user.language_code, 
#         name_action=name_action
#     )

# ...


# # Отмена, если передумал
# @router.message(Command("cancel"), RestoreState.waiting_file)
# async def cancel_restore(message: types.Message, state: FSMContext):
#     await state.clear()
#     await message.answer("❌ Восстановление отменено.")


# # Ловим файл, ПОКА находимся в состоянии waiting_file
# @router.message(RestoreState.waiting_file, F.document)
# async def process_tasks_json_file(
#     message: types.Message, 
#     state: FSMContext, 
#     bot, 
#     json_back
# ):
#     lang=message.from_user.language_code
#     document = message.document
#     send_data = await state.get_data()
#     name_action = send_data.get("name_action")
#     os.makedirs(DOWNLOAD, exist_ok=True)

#     # Проверка расширения файла
#     if not document or not document.file_name.endswith('.json'):
#         if lang == "ru": await message.answer("🚫 Прикрепите JSON файл пользователей")
#         else: await message.answer("🚫 Attach a JSON file users")
#         return

#     # Скачиваем файл во временную директорию
#     filename = os.path.join(DOWNLOAD, document.file_name)
#     filepath = os.path.join(os.getcwd(), filename)
#     await message.bot.download(document, destination=filepath)

#     await message.answer("⏳ Обрабатываю и загружаю данные в БД...")

#     # Запускаем наш метод восстановления
#     success, good_count, bad_count = await json_back.restore_tasks_from_file(name_action, filepath)

#     # Удаляем временный файл
#     if os.path.exists(filepath):
#         os.remove(filepath)

#     # Сбрасываем состояние FSM!
#     await state.clear()

#     # Разложить на lang
#     if success:
#         await message.answer(f"🎉 <b>Успешно!</b> Загружено записей {name_action} в BD: <code>{good_count}</code>.")
#     else:
#         await message.answer(f"🚫 <b>Ошибка загрузки {name_action} в BD: Из них успешно: {good_count}, с ошибкой: {bad_count}/b>")














        

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


# # CLEAR MEMORIES
# @router.message(Command("dMem"))
# async def push_clear_memories(message: types.Message):
#     """ Очищение Воспоминаний Агента """
#     await typing(message)
#     lang = message.from_user.language_code
#     user_id = message.from_user.id

#     if not await rights_verification(user_id, lang, message): return

#     conf = await clear_memories()
#     await message.answer(conf)



