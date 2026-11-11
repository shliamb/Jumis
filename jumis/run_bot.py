# ! jumi/run_bot.py
import asyncio
import signal
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
from database.rss import DBRss
from scheduler.scheduler import SmartScheduler
# from response.worker import ResponseWorker
from jumis_agent.jumis_agent import JumisAgent
from jsonbackup.json_backup import JsonBackup

from handlers import ALL_ROUTERS
from database import db
from handlers.dispatcher import dp

from telethoner.worker_telethon import myTelethon
from telethoner.notify import UserbotNotifier
from ingest.worker import IngestionWorker
from cleaner import Cleaner
from pgbackup import Backups
from rss import RssNews
from memory import Memory









async def init_router() -> None:
    """Инициализация роутеров автоматом 
        из handlers/__init__.py"""
    main_router = Router()
    for router in ALL_ROUTERS:
        main_router.include_router(router)
    dp.include_router(main_router)


async def get_admin_lang(db_users) -> str:
    """Внутренний язык владельца из БД (ru/en); по умолчанию 'en'."""
    try:
        user_data = await db_users.search_users(tg_id=ADMIN_ID)
        if user_data:
            return user_data[0].get("lang_code") or "en"
    except Exception as e:
        logger.warning("Failed to get lang_code for ADMIN_ID=%s: %s", ADMIN_ID, e)
    return "en"


async def send_startup_greeting(db_users, jumis_agent):
    """Отправка стартового приветствия администратору."""
    await asyncio.sleep(2)  # Пауза для инициализации воркеров

    lang_user = await get_admin_lang(db_users)

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
    dp["mytelethon"] = mytelethon  # /admin и /login показывают его состояние

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

    # Модуль памяти: разделы правил/стилей/паспорта, карта архива, подхват.
    # Блоки собираются из БД на каждый запрос — правка видна сразу.
    memory = Memory(db_memory=db_memory, llm=llm)
    llm.memory = memory
    dp["memory"] = memory

    # Модуль чистки истории: уборка мусора, ночная самаризация, системный крон
    cleaner = Cleaner(
        db=db,
        db_users=db_users,
        db_tasks=db_tasks,
        llm=llm,
        scheduler=scheduler,
    )
    cleaner.memory = memory  # ночная уборка дублей памяти идёт тем же кроном
    llm.cleaner = cleaner
    dp["cleaner"] = cleaner
    await cleaner.ensure_schema()

    # Модуль бэкапа БД: pg_dump в jumis/backups, ротация, системный крон 04:30
    backups = Backups(
        db_tasks=db_tasks,
        scheduler=scheduler,
    )
    llm.backups = backups
    dp["backups"] = backups

    # Модуль мировых новостей: фиды в БД, сводки со сносками, архив выжимок
    db_rss = DBRss()
    rss = RssNews(db_rss=db_rss, llm=llm)
    llm.rss = rss
    dp["rss"] = rss
    await rss.ensure_seeded()  # стартовый набор фидов при пустой таблице


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

    # Внутренний язык владельца (ru/en) — подписи кнопок и стартовое приветствие
    lang_admin = await get_admin_lang(db_users)

    # Уведомления о состоянии userbot: одно сообщение на смену состояния
    # (нет сессии / Telegram отозвал / файл занят) и напоминание раз в сутки,
    # пока вход не восстановлен.
    notifier = UserbotNotifier(
        bot=dp.bot,
        admin_id=ADMIN_ID,
        lang=lang_admin,
        state_provider=lambda: mytelethon.auth.state,
    )
    mytelethon.on_state_change = notifier.on_state_change
    dp["userbot_notifier"] = notifier

    jumis_agent = JumisAgent(
        bot=dp.bot,
        llm=llm,
        queue_new_mess=queue_new_mess,
        admin_lang=lang_admin,
    )
    dp["jumis_agent"] = jumis_agent

    llm.set_jumis_agent(jumis_agent)
    await llm.refresh_llm_models()
    scheduler.set_agent(jumis_agent)
    scheduler.set_bot(dp.bot)
    cleaner.register()  # обработчик ночной уборки в планировщике
    backups.register()  # обработчик ночного бэкапа в планировщике

    logger.info("Starting services: aiogram polling, telethon, ingest, agent")

    # Флаг для однократного запуска приветствия (защита от перезапусков)
    is_first_start = True

    try:
        # Планировщик сам создаёт свои фоновые воркеры и сразу возвращает управление.
        # Запускаем его один раз, чтобы при перезапуске сервисов не плодить дубли.
        await scheduler.run()

        # Крон уборки обязан существовать: если его нет — создаём, если протух — оживляем.
        await cleaner.ensure_system_task()

        # То же для ночного бэкапа.
        await backups.ensure_system_task()

        # Напоминания о неподключённом userbot (первое сообщение придёт само,
        # когда `run()` определит состояние сессии).
        await notifier.start()

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

        # 0. Гасим напоминания, пока сессия бота ещё открыта
        await notifier.stop()

        # 1. Отключаем клиент Telethon
        await mytelethon.stop() # или await mytelethon.client.disconnect()

        # 2. Закрываем HTTP-сессию Aiogram бота
        if dp.bot and dp.bot.session:
            await dp.bot.session.close()

        # 3. Закрываем пул подключений к PostgreSQL (asyncpg)
        await db.close()

        # 4. Останавливаем планировщик напоминаний
        await scheduler.stop()

        logger.info("=== Jumis stopped ===")


def _sigterm_handler(signum, frame):
    """`docker stop` шлёт SIGTERM: превращаем его в KeyboardInterrupt.

    Без обработчика Python умирает сразу по умолчанию и `finally` в main_bot
    не выполняется: Telethon не disconnect-ится, пул БД не закрывается. С
    исключением asyncio.run отменяет главную задачу, а shutdown-блок
    отрабатывает как при Ctrl+C.
    """
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _sigterm_handler)
    try:
        asyncio.run(main_bot())
    except KeyboardInterrupt:
        logger.info("Ctrl+C - stop")
    except Exception:
        logger.exception("Fatal error")
    finally:
        logger.info("Exit")



