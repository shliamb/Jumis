#! master/handlers/get_message.py
from aiogram import Bot
from aiogram import Router, types
from jumis_agent.jumis_agent import JumisAgent # Для анотации
from handlers.common import rights_verification
from logs.set_logger import set_logger
logger = set_logger(name="get_mess_handler")

router = Router()




@router.message()
async def handle_message(message: types.Message, bot: Bot, jumis_agent: JumisAgent):
    """ Входящие в бота сообщения - Агенту Jumis"""
    user_id = message.from_user.id
    if not await rights_verification(user_id, message.from_user.language_code, message):
        return

    # Агент Jumis обраатывает входящие в боте
    await jumis_agent.send_jumis_mess_handler(message=message)
    

