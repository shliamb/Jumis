# SPEC: jumis

## Суть работы

- `run_bot.py` — сборка и запуск всех сервисов в одном asyncio-процессе, создание `Bot` напрямую, подключение модулей `cleaner` (схема дайджестов + системный крон уборки), `pgbackup` (ночной pg_dump + системный крон бэкапа), `rss` (мировые новости: фиды в БД, сводки со сносками; системного крона нет — сбор только по просьбе), `memory` (разделы промпта, карта архива, подхват, ночная склейка дублей) и `telethoner` (userbot + машина входа в Telegram + уведомления о состоянии сессии). `SIGTERM` от `docker stop` превращается в `KeyboardInterrupt`, поэтому shutdown-блок отрабатывает и в контейнере.
- `config.py` — единственное место настроек: путь к файлу сессии (`PATH_SESSION`, абсолютный, по умолчанию `<корень>/state/session_jumis.session`, переопределяется `SESSION_PATH`), признак контейнера `DOCKER` (из `IN_DOCKER`), адрес БД (`HOST`/`PORT` из `DB_HOST`/`DB_PORT`), контейнерные пути логов/бэкапов/JSON и безопасный разбор `.env` (отсутствие ключа не роняет процесс).
- `create_session.py` — резервный вход из терминала на тот же `PATH_SESSION` (только при остановленном боте).
- `docker-compose.yml` + `Dockerfile.jumis` + `.dockerignore` — запуск бота и базы контейнерами: host-сеть (VPN хоста), GPU, каталог `state/` смонтирован (файл сессии один на хосте и в контейнере), данные — томами `./jumis/logs`, `./jumis/backups`, `./jumis/json`, `./jumis/downloads`, `./models_cache`.

- `install.sh` — идемпотентная установка с нуля: проверка docker/compose/NVIDIA, создание `.env`/`.env.llm` из примеров и интерактивное заполнение (Enter — оставить текущее или пропустить пустое; без TTY вопросы пропускаются), генерация пароля БД, перенос старой сессии в `state/`, пересборка образа только при изменениях кода, `docker compose up -d` и ожидание готовности базы и бота.

## Входные данные

- `.env`: `telegram_bot_token`, `admin_id`, `api_id`, `api_hash`, `USER_DB`, `PASSWORD_DB`, `POSTGRES_DB`, `LOG_LEVEL`; необязательные `TZ`, `PUID`/`PGID` (см. `docker-compose.yml`).
- Переменные окружения/константы в `config.py`: `IN_DOCKER` → `DOCKER` (запуск в контейнере), `DB_HOST`/`DB_PORT` → `HOST`/`PORT` (по умолчанию `localhost:15433`; в compose — `127.0.0.1:15433`, порт публикует сервис postgres), `TIME_ZONE`, `HISTORY_LIMIT`, `LLM_TIMEOUT`, `LLM_TEMPERATURE`, `USE_RICH_MESSAGES`, `BUFFER_IDLE_SEC`, `PATH_SESSION`, `TELETHON_CATCH_UP`; логирование: `LOG_LEVEL`, `LOG_TO_CONSOLE`, `LOG_MAX_SIZE_MB`, `LOG_BACKUP_COUNT`.
- `SESSION_PATH` (окружение) — путь к файлу сессии; в compose `/state/session_jumis.session` (том `./state:/state`); пусто/нет — `<корень>/state/session_jumis.session`.
- Запуск: `python jumis/run_bot.py` из корня проекта или `docker compose up -d --build`; Python добавляет каталог `jumis/` в `sys.path`, поэтому плоские импорты (`from config import ...`) работают.
- `create_session.py` интерактивно запрашивает телефон и код Telegram (резервный путь при живом боте не запускать).

- `install.sh` читает и дополняет `.env`/`.env.llm` (только пустые значения), проверяет состояние `state/`, наличие/актуальность образа и поднимает `docker compose`; базу и тома не трогает.

## Выходные данные

- Работающий процесс с корутинами: `dp.start_polling`, `mytelethon.run`, `ingestion_worker.run`, `jumis_agent.run_queue_worker`, `scheduler.run`.
- Заполненный `Dispatcher` (`dp`) сервисами: `db_users`, `llm`, `json_back`, `jumis_agent`, `cleaner`, `backups`, `rss`, `memory`, `mytelethon`, `userbot_notifier`.
- Таблица `chat_digests` и индекс `idx_messages_embedding` (создаются `database.create_tables.ensure_schema()`, вызывается через `cleaner.ensure_schema()`), две строки системных кронов в `scheduled_tasks` (уборка 03:30, бэкап 04:30) и папка `jumis/backups` (создаётся первым дампом).
- Таблицы `rss_feeds` и `rss_digests` (модуль `rss`) создаются тем же `ensure_schema(db)`; при пустой таблице фидов заводится стартовый набор из 10 источников.
- Файл сессии Telethon по `config.PATH_SESSION` (абсолютный путь, ровно один) и пул asyncpg-соединений.
- Единый лог `jumis/logs/files/jumis.log` (+ консоль) через `logs.set_logger.logger`.
- Уведомления владельцу о состоянии userbot: одно сообщение на смену состояния и напоминание раз в сутки, пока сессия не подключена.

## Логика (Вариации)

1. При импорте `run_bot.py` вызывается `setup_logger()` из `logs.set_logger` до импорта тяжёлых модулей (эмбеддер, STT, LLM), чтобы их стартовые сообщения попали в общий файл.
2. `main_bot()`: `db.connect()` → создание `DBMessages`, `DBTasks`, `SmartScheduler`, `DBMemories`, `DBUsers`; `init()` прогревает кэш категорий.
3. Создаются очереди `queue_messages` (Telethon → ingest) и `queue_new_mess` (ingest/agent → виджет).
4. Создаётся `myTelethon` и сразу кладётся в `dp["mytelethon"]` (его состояние читают `/admin` и мастер входа); затем `LLMWorker` (со ссылками на все сервисы) и `dp["llm"]`; затем `Memory(db_memory, llm)` → `llm.memory`, `dp["memory"]`; затем `Cleaner(db, db_users, db_tasks, llm, scheduler)`: `llm.cleaner = cleaner`, `dp["cleaner"]`, `await cleaner.ensure_schema()` (делегирует в `database.create_tables.ensure_schema`); затем `Backups(db_tasks, scheduler)`: `llm.backups`, `dp["backups"]`.
4a. Память: `Memory(db_memory, llm)` сразу после `LLMWorker` → `llm.memory`, `dp["memory"]`; затем `Cleaner(...)`, которому ставится `cleaner.memory = memory` (ночная уборка памяти идёт тем же кроном). Блоки памяти (правила/стили/паспорт + карта) собираются из БД в `get_tools` на каждый запрос, отдельного прогрева нет.
4b. Userbot: после `dp.bot` и `lang_admin` создаётся `UserbotNotifier(bot, ADMIN_ID, lang, state_provider=lambda: mytelethon.auth.state)` → `mytelethon.on_state_change = notifier.on_state_change`, `dp["userbot_notifier"]`. Первое уведомление придёт само: `run()` определит состояние сессии и вызовет колбэк; `notifier.start()` в рабочем цикле включает только суточное напоминание.
4c. вход в Telegram больше не требует терминала: если сессии нет, бот пишет владельцу и предлагает кнопку `login:start` (см. [SPEC handlers](handlers/SPEC.md) и [SPEC telethoner](telethoner/SPEC.md)).
5. Проверяются динамические enum категорий (`get_tools_for_agent`), вывод — на уровне DEBUG.
6. `Bot(TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=HTML))` → `dp.bot`; подключение роутеров `ALL_ROUTERS`; создание `IngestionWorker`, `JsonBackup`, `JumisAgent`; `llm.set_jumis_agent`, `scheduler.set_agent/set_bot`, `cleaner.register()` и `backups.register()` (обработчики системных кронов в планировщике).
7. Однократно отправляется приветствие админу через агента (`send_startup_greeting`).
8. `scheduler.run()` вызывается один раз (он создаёт воркеры и возвращается), затем `cleaner.ensure_system_task()`, `backups.ensure_system_task()`, `notifier.start()` — кроны создаются или оживляются, — и защитный цикл: сервисы запускаются отдельными `asyncio.Task` (polling, Telethon, ingest, agent). При сбое одного задачи отменяются, через 5 с набор перезапускается; при `CancelledError` — остановка.
9. `dp.start_polling` вызывается с `handle_signals=False` (Ctrl+C обрабатывает `asyncio.run`) и `close_bot_session=False` (сессия живёт между перезапусками, закрывается в `finally`).
10. `finally`: остановка напоминаний (`notifier.stop()`, пока сессия бота открыта), остановка Telethon (`mytelethon.stop()`), закрытие сессии бота, пула БД, планировщика; в лог пишутся старт/стоп и причина сбоя.

- `rss` после `backups`: `DBRss()` + `RssNews(db_rss, llm)` → `llm.rss`, `dp["rss"]`, `await rss.ensure_seeded()` (идемпотентно: список фидов заводится только при пустой таблице, ошибка сети не валит старт). Системного крона у модуля нет — сводка собирается по просьбе владельца, кнопкой или инструментом.

11. Контейнер (`IN_DOCKER=1`): `DOCKER=True` переключает пути на `/jumis/logs`, `/jumis/backups`, `/jumis/json`, `/jumis/downloads` — они совпадают с томами compose, поэтому логи, бэкапы и JSON остаются на хосте в `./jumis/...`, модели берутся из `./models_cache` (`HF_HOME`, `TORCH_HOME`), файл сессии — из `./state` (том `/state`). `/reset` в контейнере делает `sys.exit(0)`, процесс поднимает `restart: unless-stopped`.
12. `SIGTERM` (`docker stop`) обрабатывается в `__main__` и превращается в `KeyboardInterrupt`: `asyncio.run` отменяет главную задачу, `finally` закрывает Telethon, сессию бота, пул БД и планировщик; `stop_grace_period: 30s` даёт на это время.
13. Порядок остановки контейнера: Docker шлёт `SIGTERM` процессу Python (PID 1 — `init: true`), тот идёт тем же путём, что Ctrl+C; при 30 с без завершения Docker добивает контейнер, но сессия и пул уже закрыты штатно.

## Обработка ошибок

- Ночной бэкап без нужной утилиты (в контейнере `pg_dump` 17, на хосте `docker`) — error в лог, дамп не создаётся, файлы не мусорятся (`.part` удаляется).
- Сбой памяти (блоки промпта, подхват, ночная уборка) не валит старт и не ломает ответы: ошибка пишется в лог, соответствующий шаг пропускается.
- Сбой userbot (нет сессии, отозванный ключ, нет сети) не валит бота: `mytelethon.run` живёт своим циклом и сообщает состояние; ошибка уведомления логируется и на работу сервисов не влияет.
- Файл сессии занят вторым процессом → состояние `locked` и предупреждение владельцу вместо бесконечного переподключения.
- В Docker путь к сессии задаётся `SESSION_PATH`; монтируется **каталог** `./state` (bind-mount несуществующего файла Docker заменил бы каталогом). Контейнер и локальный запуск одновременно нельзя — у файла сессии один писатель.
