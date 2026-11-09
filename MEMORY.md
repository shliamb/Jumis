# MEMORY.md — Atlas

## Обзор

**Jumis** — персональный ИИ-секретарь, живущий в Telegram. Userbot (Telethon) перехватывает личную переписку владельца, `ingest` сохраняет её в PostgreSQL + pgvector и векторизует, а `JumisAgent` через LiteLLM отвечает, ведёт память и вызывает инструменты (поиск, факты, отправка сообщений, задачи). Второй интерфейс — aiogram-бот владельца с админ-панелью.

Ключевая идея: узкие инструменты, экономия токенов, работа от имени владельца без автопилота (отправка только после подтверждения). Проект рабочий, сейчас выключен; любые изменения кода — только по явной просьбе.

## Модули

- [jumis — точка входа и конфигурация](jumis/SPEC.md) — `run_bot.py`, `config.py`, `create_session.py`.
- [handlers](jumis/handlers/SPEC.md) — aiogram-роутеры: `/start`, `/admin`, кнопки-варианты `ask_options` (callback), кнопка «Очистить» на виджете входящих (`inbox:clear`), fallback в агента.
- [jumis_agent](jumis/jumis_agent/SPEC.md) — ядро агента: стриминг, tool-loop, финальный ответ с кнопками `ask_options`, рендер ответов, виджет входящих с кнопкой «Очистить», `admin_lang` для подписей.
- [llm](jumis/llm/SPEC.md) — LiteLLM: каталог моделей, промпт, 23 инструмента (включая `think`, `ask_options` и обслуживание истории), диалог, токены.
- [database](jumis/database/SPEC.md) — пул asyncpg, схема, репозитории users/messages/memories/tasks, быстрая проверка `is_blocked`.
- [telethoner](jumis/telethoner/SPEC.md) — userbot: приём/отправка личных сообщений, реакции, авто-реконнект.
- [ingest](jumis/ingest/SPEC.md) — конвейер: очередь → БД, STT голосовых, векторизация, очередь виджета; чёрный список (`is_blocked`) сохраняет в историю, но не пускает в виджет.
- [scheduler](jumis/scheduler/SPEC.md) — напоминания, будильники, cron-задачи.
- [vector](jumis/vector/SPEC.md) — эмбеддинги `multilingual-e5-base` (768).
- [stt_sense](jumis/stt_sense/SPEC.md) — локальный ASR Kairos на GPU.
- [jsonbackup](jumis/jsonbackup/SPEC.md) — экспорт/импорт таблиц в JSON.
- [utils](jumis/utils/SPEC.md) — время, очистка текста, сериализация.
- [logs](jumis/logs/SPEC.md) — единый `logger`, один файл `jumis.log` + консоль, `setup()` в run_bot.

- [cleaner](jumis/cleaner/SPEC.md) — обслуживание истории: уборка мусора и дублей (`janitor`), ночная самаризация старых батчей в `chat_digests` (`digest`), железобетонный системный крон (`task`).

## Логические цепочки (Scenarios)

### Входящее сообщение от собеседника
Telethon ловит личное сообщение, сохраняет его в БД и векторизует, затем обновляет виджет входящих у владельца.
**Шаги:** NewMessage (Telethoner -> [SPEC](jumis/telethoner/SPEC.md)) -> `queue_messages` -> `_process_and_save` (Ingest -> [SPEC](jumis/ingest/SPEC.md)) -> `users`/`messages` + фоновая векторизация (Database -> [SPEC](jumis/database/SPEC.md), Vector -> [SPEC](jumis/vector/SPEC.md)) -> `queue_new_mess` -> виджет Inbox (JumisAgent -> [SPEC](jumis/jumis_agent/SPEC.md)). Голосовое дополнительно проходит STT ([SPEC](jumis/stt_sense/SPEC.md)); «Избранное» транскрибируется и возвращается в чат.

### Запрос владельца боту (текст или голос)
Сообщение владельца в aiogram-бота превращается в ответ агента с возможными вызовами инструментов.
**Шаги:** Message (Handlers -> [SPEC](jumis/handlers/SPEC.md)) -> `send_jumis_mess_handler` -> STT при голосе -> `process_agent_request` (JumisAgent -> [SPEC](jumis/jumis_agent/SPEC.md)) -> `get_tools` + `refine_stream_tools` (LLM -> [SPEC](jumis/llm/SPEC.md)) -> вызовы `call_function` (SQL/Telethon/Scheduler) -> `send_jumis_response` (Rich → HTML → plain).

### Владелец ответил собеседнику
Ответ снимает собеседника с очереди неотвеченных, виджет обновляется или удаляется.
**Шаги:** исходящее через Telethon или `send_mess_peer` (LLM-инструмент) -> `outbound_owner` в `queue_messages` (Ingest) -> `queue_new_mess` -> `message_processing` удаляет пир из `pending_peers` (JumisAgent). Явная очистка — инструмент `clear_inbox_notifs` или кнопка «Очистить» (сценарий ниже).

### Очистка виджета входящих кнопкой
Владелец жмёт «🧹 Очистить» под виджетом: очередь снимается, виджет исчезает, агент не запускается.
**Шаги:** `inbox:clear` (Handlers -> [SPEC](jumis/handlers/SPEC.md)) -> проверка `ADMIN_ID` -> тост по `jumis_agent.admin_lang` -> `clear_pending_queue()` (JumisAgent -> [SPEC](jumis/jumis_agent/SPEC.md)): `pending_peers` очищен, виджет удалён -> удаление сообщения с кнопкой (страховка для устаревшего виджета). 0 токенов; `check_notif_mess` в следующем запуске агента инжектить нечего. Сообщения в БД остаются доступными через `msg_search`.

### Срабатывание напоминания
Планировщик находит задачу, меняет статус и запускает агента с системным триггером.
**Шаги:** `get_pending_task` (Scheduler -> [SPEC](jumis/scheduler/SPEC.md)) -> `_execute_task` → `running` -> `agent_queue`/`cron_queue` -> `process_agent_request` (JumisAgent) -> ответ в чат -> `completed` (разовая) или пересчёт `croniter` → `pending`.

### Бэкап и восстановление БД
Админ выгружает таблицы в JSON и при необходимости заливает их обратно.
**Шаги:** `/dowjson` (Handlers) -> `db_to_json` (JsonBackup -> [SPEC](jumis/jsonbackup/SPEC.md)) -> файлы в `jumis/json` + документы в чат. Восстановление: `/up_*` -> FSM ожидания файла -> `restore_table_from_file` -> репозиторий соответствующей таблицы (Database). `id` пересоздаются, даты парсятся из ISO.

### Первый запуск и регистрация владельца
**Шаги:** `main_bot` поднимает пул БД, сервисы, бота и Telethon ([SPEC](jumis/SPEC.md)) -> приветствие админу от агента -> `/start` создаёт запись `users.is_admin=True` (Handlers). Модели по умолчанию берутся из профиля, иначе `DEFAULT_FALLBACK_MODEL` (LLM).

### Быстрый выбор варианта ответа
Агент завершает итерацию финальным ответом с inline-кнопками `ask_options`; выбор владельца становится его следующим сообщением.
**Шаги:** `ask_options` (LLM -> [SPEC](jumis/llm/SPEC.md)) -> `call_function` возвращает `{"status": "options_shown"}` -> `show_options` (JumisAgent -> [SPEC](jumis/jumis_agent/SPEC.md)) рисует текст + кнопки и прерывает tool-loop -> нажатие `opt:<token>` (Handlers -> [SPEC](jumis/handlers/SPEC.md)) -> `consume_option` -> `process_agent_request` с выбранным текстом.

### Ночная уборка и сжатие истории
Каждую ночь система чистит мусор и сжимает самый старый кусок переписки в дайджест, а сырые сообщения удаляет.
**Шаги:** `scheduled_tasks` (`system_cron`, 03:30, [SPEC](jumis/scheduler/SPEC.md)) -> `_execute_task` → `cron_queue` -> `execute_system_cron` по `title` -> `Cleaner.nightly` ([SPEC](jumis/cleaner/SPEC.md)) -> `Janitor.run`: мусор и дубли + `VACUUM` -> `Digester.run_night`: батч старше 7 дней → `chat_digests` + `DELETE messages` + `users.summary` в одной транзакции. Удаление батча и есть курсор: следующий прогон берёт следующие сообщения.

### Ручная чистка истории
Владелец смотрит предпросмотр и запускает уборку кнопкой, либо просит агента.
**Шаги:** `/cleanup` (Handlers -> [SPEC](jumis/handlers/SPEC.md)) -> `cleaner.scan()` без удаления -> inline «✅ Удалить» (`clean:run`) -> `cleaner.run_hygiene()`. Альтернативы: `/cleanup_run` (сразу), `/digest_now N`, `/digests [chat_id]`; агенту — инструменты `cleanup_scan`, `cleanup_run`, `digest_run`, `digest_list` (LLM -> [SPEC](jumis/llm/SPEC.md)).

## Roadmap

- [ ] Режим «Дятла»: повторные напоминания через дочерние cron-задачи (описан в `scheduler.py`, не реализован).
- [ ] Сохранение `llm_logs` в БД (сейчас `DBTokenLogger` только печатает токены в консоль).
- [ ] Whitelist в `ingest` (сейчас не влияет: `is_whitelisted` — задел на приоритеты).
- [ ] Обработка фото, документов и видеосообщений (сейчас отклоняются).
- [ ] Парсеры РС-рассылок и Telegram-каналов.
- [ ] Выход в интернет через компьютерное зрение (скриншот → смысл).
- [ ] Многопоточные вызовы инструментов.
- [ ] Поиск по дайджестам: сейчас `chat_digests` читаются только по `chat_id`, семантического поиска по сжатой истории нет.
- [ ] Удаление старых `completed`/`cancelled` задач планировщика (старше 30 дней) — пока чистятся только сообщения.
- [ ] Мультиязычность сверх ru/en при необходимости.
- [ ] Починить импорт задач из JSON: `JsonBackup` ищет `db_tasks.add_task`, а метод называется `db_add_task` (сейчас `/up_tasks` не работает).

## Ограничения (Rules)

- **Проект рабочий.** Без явной просьбы не запускать бота/Telethon, не менять `.env*` и схему БД. README.md не трогать — по словам пользователя, он будет переписан в конце всех обновлений.
- В рабочем дереве есть незакоммиченный diff в `jumis/llm/llm_router.py` (удалены комментарии) — не откатывать и не «чинить».
- Вся работа со временем — naive wall-clock в `TIME_ZONE` (`Europe/Moscow`); `scheduled_at` в БД — `TIMESTAMP` без tz.
- Векторы строго 768 измерений (`multilingual-e5-base`), косинусное расстояние; префиксы `query:`/`passage:` важны для качества.
- Дубли сообщений запрещены `UNIQUE (chat_id, tg_msg_id)`; `direction` только `inbound_peer`/`outbound_owner`; категории — FK с `ON DELETE RESTRICT`.
- Обрабатываются только личные чаты; бот доступен только `ADMIN_ID`; сообщения ботам и скам/фейк-аккаунтам игнорируются.
- Секреты — только в `.env*` (gitignored). Меняются вручную, не коммитятся.
- Импорты в коде плоские (`from config import ...`), запуск из корня: `python jumis/run_bot.py`.
- `DROP SCHEMA public CASCADE` (`/allDel`) необратим — трогать только осознанно.
- Прокси (SOCKS5/MTProto) удалён: транспорт прямой. `aiohttp-socks`, `pysocks`, `bot_instance.py` и env-файлы прокси больше не используются.
- aiogram сам ретраит polling с backoff после старта; защитный цикл в `run_bot.py` нужен только для холодного старта и сбоя сервиса. Telethon использует `auto_reconnect=True` + свой внешний цикл.
- Логирование: только `from logs.set_logger import logger`; `setup()` вызывается один раз в `run_bot.py` до импорта тяжёлых модулей. Один файл `jumis/logs/files/jumis.log` + консоль, формат с `module:lineno`. В INFO не пишем текст сообщений — только `chat_id`/`msg_id`/длины/имена инструментов.

- LLM-температура задаётся только в `config.py` (`LLM_TEMPERATURE = 0.1`) и применяется централизованно ко всем чат-вызовам через `LLMWorker._call_request`; `None` отключает передачу параметра.

- `users.is_blocked` — режим A: сообщение сохраняется в историю и векторизуется, но не попадает в `queue_new_mess` (нет виджета и инжекта агенту); проверка `db_users.is_blocked` в `ingest`, ошибка БД — fail-open. `is_whitelisted` не фильтрует.

- `think` — свободный scratchpad: агент сам решает, когда и сколько раз думать; в промпте нет запретов, в коде нет счётчиков вызовов. Статус в чате — `Thinking it over...`; пустая мысль отклоняется.

- `cleaner` не меняет схему рабочих таблиц: только новая `chat_digests` (создаётся `ensure_schema()`), `UPDATE users.summary` и `DELETE` из `messages`. `/crTabDb` и `create_tables.py` не трогаются.
- Системный крон уборки защищён на трёх уровнях: `del_task` отказывает для `task_type='system_cron'`, `update_task` разрешает только `status`, `ensure_system_task()` при старте создаёт строку заново или оживляет `expired`/`failed` (но не снимает осознанный `cancelled`).
- Курсор самаризации — само удаление: дайджест покрывает ровно тот батч, который удалён в той же транзакции. Отдельного watermark в схеме нет.
- Мусор (`is_junk`) никогда не включает сообщения с цифрами, ссылками, `@` или почтой — «700₽» и «завтра в 15» остаются всегда.

## Стиль пользователя (User Style)

- Общение по-русски, коротко и по существу: что сделано, где, какие риски.
- Перед изменением кода — показать план/код и дождаться подтверждения; правки делать минимальные, не переписывать работающее без причины.
- Не плодить подтверждения и навязчивые напоминания, не добавлять поведение, о котором не просили.
- Комментарии и докстринги — по-русски, объясняют «зачем», а не пересказывают код; сложные места — с явной цепочкой вызовов.
- Ошибки и «магию» объяснять простыми словами, без академизма и без воды.
- В ответах уместны точные ссылки на файлы/функции, чтобы можно было проверить.

- **Всё, что читает LLM, — на английском.** Систем-контенты, промпты субагентов, описания инструментов, инструкции после ответа админа и тексты ошибок/результатов, которые возвращаются агенту, писать по-английски: это заметно экономит токены и модель лучше слушается. Русский — только для сообщений человеку (админу/пользователю) и для внутренних логов/комментариев, которые модель не видит.