# ! jumi/run_bot.py
import asyncio
# from datetime import datetime
# import json
from aiogram import Bot, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from config import TELEGRAM_BOT_TOKEN, ADMIN_ID
from logs.set_logger import logger, setup as setup_logger

# Логирование настраивается до импорта тяжёлых модулей (эмбеддер, STT, LLM),
# чтобы их сообщения при инициализации тоже попали в общий файл.
setup_logger()

# from utils.serialize import serialize_for_json
from llm.llm_router import LLMWorker
from database.memories import DBMemories
from database.users import DBUsers
from database.messages import DBMessages
from database.tasks import DBTasks
from scheduler.scheduler import SmartScheduler
# from response.worker import ResponseWorker
from jumis_agent.jumis_agent import JumisAgent
from jsonbackup.json_backup import JsonBackup

from handlers import ALL_ROUTERS
from database import db
from handlers.dispatcher import dp

from telethoner.worker_telethon import myTelethon
from ingest.worker import IngestionWorker









async def init_router() -> None:
    """Инициализация роутеров автоматом 
        из handlers/__init__.py"""
    main_router = Router()
    for router in ALL_ROUTERS:
        main_router.include_router(router)
    dp.include_router(main_router)


async def send_startup_greeting(db_users, jumis_agent):
    """Отправка стартового приветствия администратору."""
    await asyncio.sleep(2)  # Пауза для инициализации воркеров

    lang_user = "en"
    try:
        user_data = await db_users.search_users(tg_id=ADMIN_ID)
        if user_data:
            lang_user = user_data[0].get("lang_code")
    except Exception as e:
        logger.warning("Failed to get lang_code for ADMIN_ID=%s: %s", ADMIN_ID, e)

    prompt_to_agent = (
        f"[SYSTEM TRIGGER: STARTUP]\n"
        f"Briefly greet the user (tg_id: {ADMIN_ID}) and confirm readiness for work.\n"
    )
    if lang_user:
        prompt_to_agent += f"\n[USER LANGUAGE: {lang_user}]"

    try:
        await jumis_agent.process_agent_request(chat_id=ADMIN_ID, prompt_text=prompt_to_agent)
    except Exception as e:
        logger.warning("Failed to send startup greeting: %s", e)






#### Запуск Телеграмм Бота #####
async def main_bot() -> None:
    """ Главная функция запуска и инициализации всей системы """
    logger.info("=== Jumis starting ===")

    # Подключение к базе данных
    await db.connect()
    logger.info("PostgreSQL connected")

    db_messages = DBMessages()
    db_tasks = DBTasks()
    scheduler = SmartScheduler(db_tasks)

    # Инициализация памяти (загружает категории в кэш self)
    db_memory = DBMemories()
    try:
        await db_memory.init()
        logger.info("Memory categories initialized")
    except Exception:
        logger.warning("Memory categories are not initialized", exc_info=True)

    # Инициализация пользователей (загружает категории в кэш self)
    db_users = DBUsers()
    try:
        await db_users.init()
        logger.info("User categories initialized")
    except Exception:
        logger.warning("User categories are not initialized", exc_info=True)

    dp["db_users"] = db_users # для хендлера start ..


    ####### позже у отдельный модуль перенести..
    # Инициализация очереди и сообщений
    queue_messages = asyncio.Queue()

    # Инициализация очереди новых сообщений от peers 
    # для Агента Jumis для вывода в виде сообщения
    queue_new_mess = asyncio.Queue()
    #######

    # Инициализация Telethon
    mytelethon = myTelethon(
        queue_messages
    )

    # Инициализация LLM Воркера и передача в aiogram workflow_data
    llm = LLMWorker(
        db_memory=db_memory, 
        db_users=db_users, 
        db_messages=db_messages,
        db_tasks=db_tasks,
        scheduler=scheduler,
        mytelethon=mytelethon,
        queue_new_mess=queue_new_mess
    )
    dp["llm"] = llm  # Доступен во всех хэндлерах через `llm` или контекст


    # Проверим динамическое получение категорий фактов:
    facts_category = await llm.get_tools_for_agent(["write_fact"])
    logger.debug("Fact categories enum: %s", facts_category[0]["function"]["parameters"]["properties"]["facts_category"].get("enum"))

    # Проверим динамическое получение категорий пользователей:
    user_category = await llm.get_tools_for_agent(["update_user"])
    logger.debug("User categories enum: %s", user_category[0]["function"]["parameters"]["properties"]["user_category"].get("enum"))

    # Создание бота (aiogram сам восстанавливает polling после сетевых сбоев)
    bot = Bot(
        TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp.bot = bot
    await init_router()
    logger.info("Bot created: id=%s", bot.id)

    # Инициализация
    ingestion_worker = IngestionWorker(
        db_messages=db_messages,
        db_users=db_users,
        queue_messages=queue_messages,
        queue_new_mess=queue_new_mess,
        telethon_client=mytelethon
    )

    # Json сохранение таблиц и восстановление
    json_back = JsonBackup(
        bot=dp.bot,
        db_messages=db_messages,
        db_memory=db_memory,
        db_users=db_users,
        db_tasks=db_tasks
    )
    dp["json_back"] = json_back

    jumis_agent = JumisAgent(
        bot=dp.bot,
        llm=llm,
        queue_new_mess=queue_new_mess
    )
    dp["jumis_agent"] = jumis_agent

    llm.set_jumis_agent(jumis_agent)
    await llm.refresh_llm_models()
    scheduler.set_agent(jumis_agent)
    scheduler.set_bot(dp.bot)

    logger.info("Starting services: aiogram polling, telethon, ingest, agent")

    # Флаг для однократного запуска приветствия (защита от перезапусков)
    is_first_start = True

    try:
        # Планировщик сам создаёт свои фоновые воркеры и сразу возвращает управление.
        # Запускаем его один раз, чтобы при перезапуске сервисов не плодить дубли.
        await scheduler.run()

        while True:
            # Запускаем приветствие как неблокирующую фоновую задачу
            # Только при САМОМ ПЕРВОМ входе в рабочий цикл
            if is_first_start:
                asyncio.create_task(
                    send_startup_greeting(db_users, jumis_agent)
                )
                is_first_start = False

            # Сервисы запускаются отдельными задачами: при сбое одного
            # остальные корректно отменяются и всё перезапускается вместе.
            services = [
                asyncio.create_task(
                    dp.start_polling(
                        dp.bot,
                        skip_updates=False,
                        handle_signals=False,
                        close_bot_session=False,
                    ),
                    name="aiogram_polling",
                ),
                asyncio.create_task(mytelethon.run(), name="telethon"),
                asyncio.create_task(ingestion_worker.run(), name="ingest"),
                asyncio.create_task(jumis_agent.run_queue_worker(), name="jumis_agent"),
            ]

            try:
                await asyncio.gather(*services)

            except asyncio.CancelledError:
                # Остановка процесса: гасим сервисы и выходим
                for task in services:
                    task.cancel()
                await asyncio.gather(*services, return_exceptions=True)
                raise

            except Exception:
                # Локальный сбой сервиса: перезапускаем весь набор
                logger.exception("Service crashed; restarting in 5 seconds")
                for task in services:
                    task.cancel()
                await asyncio.gather(*services, return_exceptions=True)
                await asyncio.sleep(5)

    except asyncio.CancelledError:
        logger.info("Stop signal received")
        raise

    finally:
        logger.info("Shutting down: closing Telethon, bot session, DB, scheduler")

        # 1. Отключаем клиент Telethon
        await mytelethon.stop() # или await mytelethon.client.disconnect()

        # 2. Закрываем HTTP-сессию Aiogram бота
        if dp.bot and dp.bot.session:
            await dp.bot.session.close()

        # 3. Закрываем пул подключений к PostgreSQL (asyncpg)
        await db.close()

        # 4. Останавливаем планировщик тасок
        await scheduler.stop()

        logger.info("=== Jumis stopped ===")


if __name__ == "__main__":
    try:
        asyncio.run(main_bot())
    except KeyboardInterrupt:
        logger.info("Ctrl+C - stop")
    except Exception:
        logger.exception("Fatal error")
    finally:
        logger.info("Exit")



