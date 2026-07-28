#from database.users import get_user_by_tg
from aiogram import types
from config import ADMIN_ID
from logs.set_logger import set_logger
logger = set_logger(name="handlers")

async def typing(action):
    """ Визуализация подготовки ответа бота """
    await action.bot.send_chat_action(action.chat.id, action='typing')


# CHECK RIGHTS USER TELEGRAM
async def rights_verification(user_id: int, lang: str, message: types.Message) -> bool:
    """ Проверка прав доступа """

    if message.chat.type != 'private':  # group
        return False

    if user_id in {ADMIN_ID}:
        return True
    
    if lang == "ru": await message.answer("🔐 Вы не имеете доступа!")
    else: await message.answer("🔐 You don't have access")

    logger.error(f"This {user_id} shit made an attempt to enter to Admin Panel.")
    return False


# async def is_manager(user_id):
#     """ Проверка прав для входа в workshop """
#     user = await get_user_by_tg(user_id)
#     if user.get("is_admin") or user.get("is_manager") or user.get("is_master"):
#         return True
#     return False


# async def is_admin(user_id):
#     """ Проверка прав администратора"""
#     user = await get_user_by_tg(user_id)
#     if user.get("is_admin"):
#         return True
#     return False


# async def is_super_admin(user_id):
#     """ Проверка прав супер администратора"""
#     if user_id in {ADMIN_ID}:
#         return True
#     return False


# async def is_master(user_id):
#     """ Проверка прав мастера"""
#     user = await get_user_by_tg(user_id)
#     if user.get("is_master"):
#         return True
#     return False