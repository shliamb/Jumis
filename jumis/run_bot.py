# ! jumi/run_bot.py
import asyncio
import aiohttp
# from datetime import datetime
# import json
from aiogram import Router
from config import TELEGRAM_BOT_TOKEN, USE_PROXY
from proxy.socks5proxy import SOCKS5PROXY_STRINGS
from bot_instance import AioBot
from python_socks._errors import ProxyError
# from utils.serialize import serialize_for_json
from llm.llm_router import LLMWorker
from database.memories import DBMemories
from database.users import DBUsers
from database.messages import DBMessages
from response.worker import ResponseWorker
from jumis_agent.jumis_agent import JumisAgent


import logging
logging.basicConfig(level=logging.INFO)
logging.getLogger('aiogram').setLevel(logging.DEBUG)

# from logs.set_logger import set_logger
# logger = set_logger(name="bot")

from handlers import ALL_ROUTERS
from database import db
from handlers.dispatcher import dp

from telethoner.worker_telethon import myTelethon
from ingest.worker import IngestionWorker


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
    """ Главная функция запуска и инициализации всей системы """
    print("Запуск системы...")

    # Подключение к базе данных
    await db.connect()
    print("Успешное подключение к PostgreSQL.")

    # Инициализация памяти (загружает категории в кэш self)
    db_memory = DBMemories()
    try:
        await db_memory.init()
        print("Инициализация памяти успешно прошла")
    except:
        print("База памяти не инициализирована")

    # Инициализация пользователей (загружает категории в кэш self)
    db_users = DBUsers()
    try:
        await db_users.init()
        print("Инициализация пользователей успешно прошла")
    except:
        print("База пользователей не инициализирована")

    dp["db_users"] = db_users # для хендлера start ..

    # Инициализация очереди и сообщений
    queue_messages = asyncio.Queue()
    db_messages = DBMessages()

    # Инициализация очереди сообщений требующих ответов
    queue_response = asyncio.Queue()

    # Инициализация очереди внутренних сообщений для Jumis Agent
    queue_req_jum = asyncio.Queue()

    # Инициализация LLM Воркера и передача в aiogram workflow_data
    llm = LLMWorker(
        db_memory=db_memory, 
        db_users=db_users, 
        db_messages=db_messages
    )
    dp["llm"] = llm  # Доступен во всех хэндлерах через `llm` или контекст


    # Проверим динамическое получение категорий фактов:
    facts_category = await llm.get_tools_for_agent(["write_fact"])
    print("Сгенерированный enum фактов:", facts_category[0]["function"]["parameters"]["properties"]["facts_category"].get("enum"))

    # Проверим динамическое получение категорий пользователей:
    user_category = await llm.get_tools_for_agent(["update_user"])
    print("Сгенерированный enum пользователей:", user_category[0]["function"]["parameters"]["properties"]["user_category"].get("enum"))

    # Создание бота и подключение роутеров
    await bot_instance.create_bot()
    dp.bot = bot_instance.bot
    await init_router()

    if bot_instance.bot is None:
        print("Не удалось создать экземпляр бота! Завершение работы.")
        return

    # Инициализация Telethon и входящего обработчика
    mytelethon = myTelethon(
        queue_messages
    )
    ingestion_worker = IngestionWorker(
        db_messages=db_messages,
        db_users=db_users,
        queue_messages=queue_messages,
        queue_response=queue_response
    )
    response_worker = ResponseWorker(
        bot=dp.bot,
        queue_response=queue_response, 
        telethon_client=mytelethon,
        queue_req_jum=queue_req_jum,
        llm=llm
    )
    jumis_agent = JumisAgent(
        bot=dp.bot,
        llm=llm,
        queue_req_jum=queue_req_jum
    )
    dp["jumis_agent"] = jumis_agent

    print("Все сервисы запускаются...")

    try:
        while True:
            try:
                if bot_instance.bot is None:
                    print("Бот не создан, ждём...")
                    await asyncio.sleep(5)
                    continue

                # asyncio.gather сам обернет их в таски и запустит параллельно
                await asyncio.gather(
                    dp.start_polling(dp.bot, skip_updates=False),
                    mytelethon.run(),
                    ingestion_worker.run(),
                    response_worker.run(),
                    jumis_agent.run_queue_worker()
                )

            except (aiohttp.ClientConnectorError, aiohttp.ClientProxyConnectionError, ProxyError):
                # Перепроверить позже, чую там пизда..
                print("Прокси ошибка, переподключаемся...")
                await bot_instance.reconnect()
                dp.bot = bot_instance.bot
                await asyncio.sleep(5)
            except Exception as e:
                # Перепроверить позже, чую там пизда..
                print(f"Другая ошибка: {e}")
                await bot_instance.reconnect()
                dp.bot = bot_instance.bot
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                print("📢 Бот получил сигнал остановки")
                raise

    finally:
        print("Остановка сервисов и очистка ресурсов...")

        # 1. Отключаем клиент Telethon
        await mytelethon.stop() # или await mytelethon.client.disconnect()

        # 2. Закрываем HTTP-сессию Aiogram бота
        if dp.bot and dp.bot.session:
            await dp.bot.session.close()

        # 3. Закрываем пул подключений к PostgreSQL (asyncpg)
        await db.close()

        print("Все соединения закрыты. Завершение работы выполнено успешно.")


if __name__ == "__main__":
    try:
        asyncio.run(main_bot())
    except KeyboardInterrupt:
        print("\n🛑 Ctrl+C - остановка")
    finally:
        print("Завершение работы...")



