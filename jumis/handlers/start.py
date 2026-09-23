# jumis/handlers/start.py
from handlers.common import typing
from logs.set_logger import set_logger
logger = set_logger(name="admin")

from aiogram.filters import CommandStart
from aiogram import Router, types, F
from datetime import datetime
from zoneinfo import ZoneInfo
from handlers.common import rights_verification
from utils.common import get_now_datetime

# from aiogram.types import Message
# from aiogram.fsm.context import FSMContext
# from aiogram.fsm.state import State, StatesGroup


router = Router()





TEXTS = {
    "ru": {
        "bot_denied": "🤖 Извините, бот предназначен только для пользователей-людей.",
        "already_registered": "👋 Вы уже зарегистрированы в системе.",
        "success": "🎉 Вы успешно зарегистрированы! Добро пожаловать.",
        "error": "❌ Произошла ошибка при регистрации. Пожалуйста, попробуйте позже.",
    },
    "en": {
        "bot_denied": "🤖 Sorry, this bot only works with humans.",
        "already_registered": "👋 You are already registered in the system.",
        "success": "🎉 Registration successful! Welcome aboard.",
        "error": "❌ An error occurred during registration. Please try again later.",
    },
}


def get_msg(key: str, lang_code: str | None) -> str:
    """Возвращает локализованный текст. По умолчанию отдаёт 'en', если не 'ru'."""
    lang = "ru" if lang_code and lang_code.startswith("ru") else "en"
    return TEXTS[lang][key]


@router.message(CommandStart())
async def start_router(message: types.Message, db_users):
    """
    Обработчик команды /start.
    Проверяет права, защищает от ботов и регистрирует нового пользователя, 
    точнее единственного - админа.
    """
    await typing(message)

    user = message.from_user
    if not user:
        return

    user_id = user.id
    lang = user.language_code
    full_name = user.full_name

    # 1. Проверка прав доступа
    if not await rights_verification(user_id, lang, message):
        return

    # 2. Защита от ботов
    if user.is_bot:
        await message.answer(get_msg("bot_denied", lang))
        return

    # 3. Проверка наличия пользователя в базе
    if await db_users.chek_tg_id(tg_id=user_id):
        await message.answer(get_msg("already_registered", lang))
        return

    # 4. Формирование данных и запись нового пользователя
    now = get_now_datetime()
    new_user_data = {
        "tg_id": user_id,
        "full_name": full_name,
        "lang_code": lang,
        "is_admin": True,
        "created_at": now,
    }

    if await db_users.add_user(new_user_data):
        await message.answer(get_msg("success", lang))
    else:
        await message.answer(get_msg("error", lang))

    # 5. Добавить в таблицу llm_logs
    # Еще не работал с таблицей той, позже сделаю.
















