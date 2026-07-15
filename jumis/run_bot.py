# ! jumi/run_bot.py
import asyncio
import aiohttp
from datetime import datetime
import json
from aiogram import Router
from config import TELEGRAM_BOT_TOKEN, USE_PROXY
from proxy.socks5proxy import SOCKS5PROXY_STRINGS
from bot_instance import AioBot
from python_socks._errors import ProxyError
from utils.serialize import serialize_for_json
from llm.deepseek import DeepSeek


import logging
logging.basicConfig(level=logging.INFO)
logging.getLogger('aiogram').setLevel(logging.DEBUG)

# from logs.set_logger import set_logger
# logger = set_logger(name="bot")

from handlers import ALL_ROUTERS
from database import db
from handlers.dispatcher import dp

from telethoner import mytelethon


# В AioBot производится ротирование прокси
# При обрыве, прокси должен сам переподключиться к лушему по ping
bot_instance = AioBot(USE_PROXY, SOCKS5PROXY_STRINGS, TELEGRAM_BOT_TOKEN)





async def get_bot_id():
    """ Получить telegram id bot """
    bot = bot_instance.bot
    me = await bot.get_me()
    return me.id


async def init_router() -> None:
    """Инициализация роутеров автоматом 
        из handlers/__init__.py"""
    main_router = Router()
    for router in ALL_ROUTERS:
        main_router.include_router(router)
    dp.include_router(main_router)






#### Запуск Телеграмм Бота #####
async def main_bot() -> None:
    """ Главная функция запуска всего бота """
    await bot_instance.create_bot()
    dp.bot = bot_instance.bot
    await init_router()
    ds = DeepSeek()
    dp["ds"] = ds  # Теперь aiogram знает про этот объект глобально
    await db.connect()
    telethon_task = asyncio.create_task(mytelethon.run())


    try:
        while True:
            try:
                if bot_instance.bot is None:
                    print("Бот не создан, ждём...")
                    await asyncio.sleep(5)
                    continue

                # Запускаем бота и Telethon одновременно
                await asyncio.gather(
                    dp.start_polling(dp.bot, skip_updates=False),
                    telethon_task
                    # messages_from_workers(ds),
                    # resend_unconfirmed_tasks(ds)
                )

            except (aiohttp.ClientConnectorError, aiohttp.ClientProxyConnectionError, ProxyError):
                print("Прокси ошибка, переподключаемся...")
                await bot_instance.reconnect()
                dp.bot = bot_instance.bot
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Другая ошибка: {e}")
                await bot_instance.reconnect()
                dp.bot = bot_instance.bot
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                print("📢 Бот получил сигнал остановки")
                raise

    finally:
        print("🔒 Закрываем пул БД...")
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main_bot())
    except KeyboardInterrupt:
        print("\n🛑 Ctrl+C - остановка")
    finally:
        print("Завершение работы...")



