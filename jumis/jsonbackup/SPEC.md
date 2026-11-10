# SPEC: jsonbackup

## Суть работы

Экспорт и импорт всех таблиц БД через JSON-файлы (резервное копирование и перенос):

- `db_to_json()` — выгрузка 6 таблиц с разбиением на части и отправкой админу в Telegram;
- `restore_table_from_file(name_action, filepath)` — восстановление конкретной таблицы из файла.

## Входные данные

- Сервисы: `db_users`, `db_memory`, `db_messages`, `db_tasks`, `bot`, `ADMIN_ID`.
- Каталог выгрузки: `PATH_JSON` (`jumis/json`).
- Файлы вида `01_user_categories_YYYY-MM-DD_HH-MM-SS.json` (при >N записей — `_partNN_of_MM`).
- Импорт: JSON-массив объектов, ключ `name_action`: `01_user_categories`, `02_facts_categories`, `03_users`, `04_facts`, `05_messages`, `06_tasks`.

## Выходные данные

- `db_to_json()` — файлы в `PATH_JSON`, документы в чат админа (с иконками/подписями), финальное сообщение.
- `restore_table_from_file(...) -> tuple[bool, int, int]` — `(overall_success, good_count, bad_count)`; `overall_success = bad_count == 0 and good_count > 0`.
- `_prepare_for_db` убирает `id` (SERIAL пересоздастся) и парсит `*_at`, `*_date`, `due_*` обратно в naive datetime.

## Логика (Вариации)

1. Порядок экспорта соблюдает FK: `01_user_categories` → `02_facts_categories` → `03_users` → `04_facts` → `05_messages` → `06_tasks`.
2. Категории берутся из кэшей `db_users.users_categories` / `db_memory.fact_categories`, остальные — через `get_all_*`/`get_users`/`get_tasks`.
3. `save_json_files`: список режется по `max_records_per_file` (500 / 1000), каждый чанк — отдельный файл, `indent=4`, `ensure_ascii=False`.
4. `_push_files`: `send_document` на каждый файл с паузой 0.7 с (защита от flood).
5. `restore_table_from_file`: чтение JSON → диспетчер `save_handlers` (`db_users.add_category`, `db_memory.add_category`, `db_users.add_user`, `db_memory.add_fact`, `db_messages.add_message`, `db_tasks.db_add_task`) → построчная вставка → подсчёт успехов/ошибок.
6. Восстановление идёт по одному файлу за команду (`/up_*`), ручной выбор таблицы.
7. Время: `_json_serializer` переводит aware-datetime (asyncpg отдаёт `TIMESTAMPTZ` в UTC) в настенное время `TIME_ZONE` — иначе при восстановлении штампы уезжают на смещение пояса. Naive-datetime (`TIMESTAMP`, например `scheduled_at`) пишется как есть.
8. `_prepare_for_db` — обратное преобразование: `id` выбрасывается (SERIAL/BIGSERIAL создаётся заново), строки `*_at`/`*_date`/`due_*` парсятся; значение со смещением приводится к `TIME_ZONE`, без смещения считается уже настенным временем приложения (совместимо со старыми файлами, где был `+00:00`).
9. `db_add_task` восстанавливает и `created_at`/`updated_at` (вставка их разрешает), поэтому у восстановленных задач не подменяется время создания.

## Обработка ошибок

- Нет данных в таблице → выгрузка пропускается с warning.
- Ошибка записи/отправки файла → лог, `success = False`, остальные файлы продолжают отправляться.
- Файл не найден/не читается/структура не список → `(False, 0, 0)`.
- Неизвестный `name_action` → `(False, 0, 0)`.
- Ошибка вставки строки → `bad_count += 1`, лог, импорт продолжается.
- Битые даты при восстановлении остаются строкой (ValueError перехватывается).
