# SPEC: database

## Суть работы

Асинхронный слой PostgreSQL + pgvector и DDL-схема:

- `common.py` — единый пул `asyncpg` (`db`) и обёртки `fetch/fetchrow/fetchval/execute`.
- `create_tables.py` — синхронное создание схемы и seed-категорий.
- `deleted_tables_db.py` — необратимый сброс схемы.
- `users.py`, `messages.py`, `memories.py`, `tasks.py` — доменные репозитории.

## Входные данные

- Подключение: `HOST`, `PORT` (15433 локально / 5432 в Docker), `USER_DB`, `PASSWORD_DB`, `DB_NAME`, `TIME_ZONE` (server timezone).
- SQL-запросы и параметры (asyncpg `$1...$N`), в т.ч. векторы в виде строки `'[...]'` с кастом `::vector`.
- Данные для вставки: dict с колонками таблиц (`users`, `messages`, `memories`, `scheduled_tasks`, `*_categories`).
- Таблицы: `users_categories`, `users`, `messages`, `facts_categories`, `memories`, `llm_logs`, `scheduled_tasks`.

## Выходные данные

- `Database`: пул (`min_size=5`, `max_size=50`, `max_queries=50000`), статистика `{total, used, idle, percent_used}`.
- Репозитории: `list[dict]` (asyncpg Record → dict), `bool` успеха, `int` нового id, `None` при ошибке.
- `DBMessages.add_message` → `RETURNING id`; `DBMemories.add_fact` → id; `DBTasks.db_add_task` → id.
- `search_users` / `search_similar_messages` / `search_vectors` → список dict (+ поле `similarity` при векторном поиске).
- `create_tables_in_db()` / `drop_all_tables_and_reset_schema()` → bool.

## Логика (Вариации)

1. `Database.connect` создаёт пул asyncpg с таймаутом 30 с и `max_inactive_connection_lifetime=300`; `_ensure_connected` лениво переподключает.
2. `create_tables_in_db` (psycopg2, синхронно): `CREATE EXTENSION vector`; таблицы `IF NOT EXISTS`; seed категорий (`not_defined/client/friend/family/partner/spam`, `fact/preference/hardware/agreement`) через `ON CONFLICT DO NOTHING`.
3. Векторы — `VECTOR(768)` (модель `intfloat/multilingual-e5-base`), HNSW-индексы `vector_cosine_ops` для `users.aliases_vector` и `memories.embedding`.
4. `messages`: уникальность `(chat_id, tg_msg_id)`, `CHECK direction IN ('inbound_peer','outbound_owner')`, индексы по истории и отправителю.
5. `users.search_users`: каскад — точный поиск по `username`/`tg_id` → векторный по `aliases_vector` (порог 0.75) → текстовый `ILIKE` по `username/full_name/aliases/comment`.
6. `messages.search_similar_messages`: только `is_embedded = TRUE`, порог 0.75, опциональный фильтр `chat_id`.
7. `memories.search_vectors`: порог 0.75, фильтры `tg_id`, `category`.
8. `db_update_user`/`db_update_task`/`edit_fact`: динамический `SET`, `updated_at = NOW()`, идентификатор по приоритету (`id` → `tg_id` → `username`).
9. `DBTasks.cleanup_stale_tasks(expire_hours=12)`: `running` → `pending`, старые `pending` → `expired`.
10. `drop_all_tables_and_reset_schema`: `DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ...`.

## Обработка ошибок

- Все методы ловят исключения, пишут `logger.error` и возвращают безопасное значение (`[]`, `False`, `None`).
- `UPDATE 0` в результате asyncpg трактуется как «не найдено» → `False`.
- `add_message` без `sender_id` → `None` + лог.
- `add_user` использует `ON CONFLICT (tg_id) DO NOTHING` — повторная вставка не ошибка.
- `chek_tg_id` фактически делает upsert-заглушку и возвращает результат `execute` (строка) или `False`.
- Ошибка создания таблиц/сброса схемы → `False`, соединение закрывается в `finally`.
