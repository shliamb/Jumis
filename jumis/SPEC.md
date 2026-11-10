# SPEC: jumis

## Суть работы

- `run_bot.py` — сборка и запуск всех сервисов в одном asyncio-процессе, создание `Bot` напрямую, подключение модулей `cleaner` (схема дайджестов + системный крон уборки), `pgbackup` (ночной pg_dump + системный крон бэкапа) и `rss` (мировые новости: фиды в БД, сводки со сносками; системного крона нет — сбор только по просьбе).

## Входные данные

- `.env`: `telegram_bot_token`, `admin_id`, `api_id`, `api_hash`, `USER_DB`, `PASSWORD_DB`, `POSTGRES_DB`, `LOG_LEVEL`.
- Переменные окружения/константы в `config.py`: `DOCKER`, `TIME_ZONE`, `HISTORY_LIMIT`, `LLM_TIMEOUT`, `LLM_TEMPERATURE`, `USE_RICH_MESSAGES`, `BUFFER_IDLE_SEC`; логирование: `LOG_LEVEL`, `LOG_TO_CONSOLE`, `LOG_MAX_SIZE_MB`, `LOG_BACKUP_COUNT`.
- Запуск: `python jumis/run_bot.py` из корня проекта; Python добавляет каталог `jumis/` в `sys.path`, поэтому плоские импорты (`from config import ...`) работают.
- `create_session.py` интерактивно запрашивает телефон и код Telegram.

## Выходные данные

- Работающий процесс с корутинами: `dp.start_polling`, `mytelethon.run`, `ingestion_worker.run`, `jumis_agent.run_queue_worker`, `scheduler.run`.
- Заполненный `Dispatcher` (`dp`) сервисами: `db_users`, `llm`, `json_back`, `jumis_agent`, `cleaner`, `backups`.
- Таблица `chat_digests` и индекс `idx_messages_embedding` (создаются `database.create_tables.ensure_schema()`, вызывается через `cleaner.ensure_schema()`), две строки системных кронов в `scheduled_tasks` (уборка 03:30, бэкап 04:30) и папка `jumis/backups` (создаётся первым дампом).
- Файл `session_jumis.session` (Telethon) и пул asyncpg-соединений.
- Единый лог `jumis/logs/files/jumis.log` (+ консоль) через `logs.set_logger.logger`.

- Заполненный `Dispatcher` (`dp`) сервисами: `db_users`, `llm`, `json_back`, `jumis_agent`, `cleaner`, `backups`, `rss`.
- Таблицы `rss_feeds` и `rss_digests` (модуль `rss`) создаются тем же `ensure_schema(db)`; при пустой таблице фидов заводится стартовый набор из 10 источников.

## Логика (Вариации)

1. При импорте `run_bot.py` вызывается `setup_logger()` из `logs.set_logger` до импорта тяжёлых модулей (эмбеддер, STT, LLM), чтобы их стартовые сообщения попали в общий файл.
2. `main_bot()`: `db.connect()` → создание `DBMessages`, `DBTasks`, `SmartScheduler`, `DBMemories`, `DBUsers`; `init()` прогревает кэш категорий.
3. Создаются очереди `queue_messages` (Telethon → ingest) и `queue_new_mess` (ingest/agent → виджет).
4. Создаётся `myTelethon`, затем `LLMWorker` (со ссылками на все сервисы) и кладётся в `dp["llm"]`; сразу после — `Cleaner(db, db_users, db_tasks, llm, scheduler)`: `llm.cleaner = cleaner`, `dp["cleaner"]`, `await cleaner.ensure_schema()` (делегирует в `database.create_tables.ensure_schema`: `chat_digests` + индекс `messages.embedding`, рабочие таблицы не меняются); затем `Backups(db_tasks, scheduler)`: `llm.backups`, `dp["backups"]`.
5. Проверяются динамические enum категорий (`get_tools_for_agent`), вывод — на уровне DEBUG.
6. `Bot(TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=HTML))` → `dp.bot`; подключение роутеров `ALL_ROUTERS`; создание `IngestionWorker`, `JsonBackup`, `JumisAgent`; `llm.set_jumis_agent`, `scheduler.set_agent/set_bot`, `cleaner.register()` и `backups.register()` (обработчики системных кронов в планировщике).
7. Однократно отправляется приветствие админу через агента (`send_startup_greeting`).
8. `scheduler.run()` вызывается один раз (он создаёт воркеры и возвращается), затем `cleaner.ensure_system_task()` и `backups.ensure_system_task()` — кроны создаются или оживляются, — и защитный цикл: сервисы запускаются отдельными `asyncio.Task` (polling, Telethon, ingest, agent). При сбое одного задачи отменяются, через 5 с набор перезапускается; при `CancelledError` — остановка.
9. `dp.start_polling` вызывается с `handle_signals=False` (Ctrl+C обрабатывает `asyncio.run`) и `close_bot_session=False` (сессия живёт между перезапусками, закрывается в `finally`).
10. `finally`: остановка Telethon (`mytelethon.stop()`), закрытие сессии бота, пула БД, планировщика; в лог пишутся старт/стоп и причина сбоя.

- `rss` после `backups`: `DBRss()` + `RssNews(db_rss, llm)` → `llm.rss`, `dp["rss"]`, `await rss.ensure_seeded()` (идемпотентно: список фидов заводится только при пустой таблице, ошибка сети не валит старт). Системного крона у модуля нет — сводка собирается по просьбе владельца, кнопкой или инструментом.

## Обработка ошибок

- Отсутствие `admin_id`/невалидный `.env` → падение на старте (`int(None)`); проверять `.env` до запуска.
- `bot.me()` внутри `start_polling` выполняется до внутреннего retry-цикла aiogram: при холодном старте без интернета первый запуск упадёт, защитный цикл перезапустит сервисы через 5 с (сам `Bot` не пересоздаётся).
- Ошибки внутри корутин: `asyncio.gather` пробрасывает первую, остальные задачи отменяются и ожидаются, затем набор перезапускается; сбой пишется в лог с traceback (`logger.exception`).
- `CancelledError` (Ctrl+C / остановка) гасит сервисы и пробрасывается; `finally` закрывает ресурсы.
- `db_memory.init()`/`db_users.init()` обёрнуты в `try/except` — при пустой БД пишется warning, приложение продолжает старт.
- Повторный вызов `main_bot` не предусмотрен: роутеры и планировщик создаются один раз.
- `cleaner.ensure_schema()`/`ensure_system_task()` и `backups.ensure_system_task()` при сбое пишут error в лог и не останавливают старт (уборка или бэкап просто не сработают).
- Ночной бэкап без docker/контейнера — error в лог, дамп не создаётся, файлы не мусорятся (`.part` удаляется).
