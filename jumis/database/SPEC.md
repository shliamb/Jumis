# SPEC: database

## Суть работы

Асинхронный слой PostgreSQL + pgvector и DDL-схема:

- `common.py` — единый пул `asyncpg` (`db`) и обёртки `fetch/fetchrow/fetchval/execute`.
- `create_tables.py` — **единственное место схемы БД**: DDL всех таблиц и индексов, синхронное создание (`/crTabDb`) и async-гарантия служебных объектов на старте (`ensure_schema`).
- `deleted_tables_db.py` — необратимый сброс схемы.
- `users.py`, `messages.py`, `memories.py`, `tasks.py` — доменные репозитории.

- `rss.py` — доменный репозиторий новостей: `rss_feeds` (источники и их здоровье) и `rss_digests` (накопленные сводки, только добавление).
- `memories.py` — ещё и слой «память в промпте»: разделы (`facts_categories.in_prompt`), карта архива, поиск пар-дублей и статусы фактов (`memory_status`: `live` / `superseded` / `archived`).

## Входные данные

- Подключение: `HOST`, `PORT` (15433 локально / 5432 в Docker), `USER_DB`, `PASSWORD_DB`, `DB_NAME`, `TIME_ZONE` (server timezone).
- SQL-запросы и параметры (asyncpg `$1...$N`), в т.ч. векторы в виде строки `'[...]'` с кастом `::vector`.
- Данные для вставки: dict с колонками таблиц (`users`, `messages`, `memories`, `scheduled_tasks`, `*_categories`).
- Таблицы: `users_categories`, `users`, `messages`, `facts_categories`, `memories`, `llm_logs`, `scheduled_tasks`, `chat_digests`.
- Ключи словарей для вставки/обновления (приходят в том числе из инструментов агента).

- Таблицы `rss_feeds` (`url` unique, `title`, `note`, `active`, `last_ok_at`, `last_item_at`, `last_error`, `fail_count`, `created_at`) и `rss_digests` (`period_from`, `period_to`, `gist`, `body`, `items_count`, `feeds_used`, `feeds_failed`, `model`).

## Выходные данные

- `Database`: пул (`min_size=2`, `max_size=20`, `max_queries=50000`, `command_timeout=60`), статистика `{total, used, idle, percent_used}`.
- Репозитории: `list[dict]` (asyncpg Record → dict), `bool` успеха, `int` нового id, `None` при ошибке.
- `DBMessages.add_message` → `RETURNING id`; `DBMemories.add_fact` → id; `DBTasks.db_add_task` → id.
- `search_users` / `search_similar_messages` / `search_vectors` → список dict (+ поле `similarity` при векторном поиске).
- `create_tables_in_db()` / `drop_all_tables_and_reset_schema()` → bool.
- `ensure_schema(db)` → bool: `chat_digests` + индекс `idx_messages_embedding` через пул (идемпотентно, на старте).

- `DBUsers.is_blocked(tg_id)` → `bool`: один `SELECT is_blocked` по `tg_id` (нужен `ingest` для фильтра виджета).

- `DBMemories` (память в промпте): `get_prompt_sections` (разделы `in_prompt` по `prompt_order`), `get_section_facts(category)` (только живые), `get_map_rows()` (полки архива + число живых), `get_facts_by_ids`, `fetch_relative_dated(days)` (кандидаты в архив), `find_similar_pairs(min_similarity)` (пары живых фактов), `mark_superseded(ids, target_id, status)` → сколько обновлено, `live_stats()` → `{live, archived, total}`.
- `DBRss`: `get_feeds(active_only)` / `count_feeds` / `add_feed` / `remove_feed` (id, точный url или фрагмент названия) / `replace_feeds` / `mark_ok(feed_id, last_item_at)` / `mark_fail(feed_id, error)`; `save_digest` → id / `latest_digest(younger_than_minutes, with_body)` / `get_digest` / `list_digests(days, query, limit, with_body)` / `count_digests`. `list_digests` без `with_body` не тянет полный текст.
- `ensure_schema(db)` → bool: гоняет `SQL_CHAT_DIGESTS` и `SQL_RSS` (`rss_feeds`, `rss_digests` + индексы) через пул, затем индекс `idx_messages_embedding`.

## Логика (Вариации)

1. `Database.connect` создаёт пул asyncpg: `min_size=2`, `max_size=20`, `timeout=10` (установка соединения), `command_timeout=60` (запрос), `max_inactive_connection_lifetime=300`. Ожидание свободного соединения ограничено `pool.acquire(timeout=30)`. `_ensure_connected` переподключает лениво, но **не** после `close()` (флаг `_closed` → `RuntimeError`).
2. `create_tables_in_db` (psycopg2, синхронно): `CREATE EXTENSION vector`; таблицы `IF NOT EXISTS`; seed категорий (`not_defined/client/friend/family/partner/spam`, `fact/preference/hardware/agreement`) через `ON CONFLICT DO NOTHING`.
3. Векторы — `VECTOR(768)` (модель `intfloat/multilingual-e5-base`), HNSW-индексы `vector_cosine_ops` для `users.aliases_vector`, `memories.embedding` и `messages.embedding` (`idx_messages_embedding`).
4. `messages`: уникальность `(chat_id, tg_msg_id)`, `CHECK direction IN ('inbound_peer','outbound_owner')`, индексы по истории, отправителю и вектору.
4a. `chat_digests` (модуль `cleaner`) объявлена здесь же — модули не создают таблиц в своих папках.
4b. `rss_feeds` / `rss_digests` (модуль `rss`) объявлены здесь же: `url` уникален, `fail_count` копится при падениях и сбрасывается при успехе, индекс `idx_rss_digests_created` по `created_at DESC` (архив читается последними сверху). Сводки не перезаписываются — только `INSERT`.
4c. Память в промпте (модуль `memory`) объявлена здесь же, в `SQL_MEMORY_PROMPT`: `facts_categories.in_prompt/prompt_order/prompt_title`, `memories.memory_status/superseded_by`, частичный индекс `idx_memories_live` и сид пяти разделов (`rule`, `style_owner`, `style_client`, `style_personal`, `profile`). Разделы добавляются `ON CONFLICT DO NOTHING` — повторный старт их не перезаписывает и не воскрешает отключённые вручную.
5. `users.search_users`: каскад — точный поиск по `username`/`tg_id` → векторный по `aliases_vector` (порог 0.75) → текстовый `ILIKE` по `username/full_name/aliases/comment`.
6. `messages.search_similar_messages`: только `is_embedded = TRUE`, порог 0.75, опциональный фильтр `chat_id`.
7. `memories.search_vectors`: порог 0.75, фильтры `tg_id`, `category`; **всегда** `memory_status = 'live'` (устаревшее в поиск не попадает), `include_sections=False` исключает разделы, которые и так в промпте (для подхвата).
7a. `find_similar_pairs`: self-join `a.id < b.id` по живым фактам с векторами, порог 0.93 по умолчанию, сортировка по близости. При 300–500 фактах это дешёвый запрос, HNSW для пар «a < b» не помогает.
7b. `mark_superseded`: `UPDATE … SET memory_status, superseded_by, updated_at` по массиву id, уже отложенные не трогает. Ничего не удаляет: история остаётся в базе, её видит `get_all_facts` для бэкапа.
8. `db_update_user`/`db_update_task`/`db_add_task`/`add_message`/`edit_fact`: динамический `SET`/`INSERT`, `updated_at = NOW()`, идентификатор по приоритету (`id` → `tg_id` → `username`). Имена колонок берутся **только** из белого списка (`USER_UPDATABLE_COLUMNS`, `TASK_COLUMNS` / `TASK_INSERT_COLUMNS`, `MESSAGE_COLUMNS`): неизвестный ключ отбрасывается с warning, инъекция в имени поля невозможна.
8a. `ensure_schema(db)`: гоняет `SQL_CHAT_DIGESTS`, `SQL_RSS`, `SQL_MEMORY_PROMPT` и `SQL_MESSAGES_EMBEDDING_INDEX` из `create_tables.py`; индекс ставится только при наличии таблицы `messages` (проверка `to_regclass('messages')` по `search_path`), при её отсутствии — warning и пропуск.
9. `DBTasks.cleanup_stale_tasks(expire_hours=12)`: `running` → `pending`, старые `pending` → `expired`.
10. `drop_all_tables_and_reset_schema`: `DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ...`.

11. `DBUsers.is_blocked(tg_id)`: `SELECT is_blocked FROM users WHERE tg_id = $1`; `fetchval` → `bool`, ошибка → `False` (fail-open). Используется в `ingest` для скрытия заблокированных из виджета.

## Обработка ошибок

- Все методы ловят исключения, пишут `logger.error` и возвращают безопасное значение (`[]`, `False`, `None`).
- `UPDATE 0` в результате asyncpg трактуется как «не найдено» → `False`.
- `add_message` без `sender_id` → `None` + лог.
- `add_user` использует `ON CONFLICT (tg_id) DO NOTHING` — повторная вставка не ошибка.
- `chek_tg_id` фактически делает upsert-заглушку и возвращает результат `execute` (строка) или `False`.
- Ошибка создания таблиц/сброса схемы → `False`, соединение закрывается в `finally`.
- `ensure_schema` не валит старт: каждый оператор в своём `try`, при ошибке — `logger.error` и `False`.
- Запрос дольше `command_timeout` → `TimeoutError`, соединение не остаётся занятым.
- Запрос после `close()` → `RuntimeError("Database pool is closed")`, а не тихое воскрешение пула.
- `/crTabDb` вызывает синхронный psycopg2 через `asyncio.to_thread` — event loop бота не блокируется.

- `replace_feeds([])` → `ValueError`: пустой список стёр бы все источники, поэтому не применяется.
- Сбой сети при заборе фидов не всплывает наружу как исключение: причина пишется в `rss_feeds.last_error` (`fail_count += 1`), остальные источники продолжают работать.
- `mark_superseded` по несуществующим id → `UPDATE 0` → `0`, исключения нет.
- `find_similar_pairs` при фактах без вектора: NULL-строки отсеиваются условием `embedding IS NOT NULL`, ложных пар нет.
