#from database.users import get_user_by_tg
from config import ADMIN_ID
from logs.set_logger import set_logger
logger = set_logger(name="handlers")

async def typing(action):
    """ Визуализация подготовки ответа бота """
    await action.bot.send_chat_action(action.chat.id, action='typing')


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