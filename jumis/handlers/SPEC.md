# SPEC: handlers

## Суть работы

Aiogram-слой Telegram-бота (второй интерфейс, рядом с userbot):

- `start.py` — `/start`: проверка прав, защита от ботов, регистрация единственного админа.
- `admin.py` — `/admin` и сервисные команды: логи, модели, рестарт, создание/удаление таблиц, JSON-бэкап и импорт.
- `get_message.py` — fallback-роутер: любое разрешённое сообщение уходит в `JumisAgent`.
- `common.py` — `typing()` и `rights_verification()`.
- `dispatcher.py` — общий `Dispatcher`; `__init__.py` — порядок роутеров.

## Входные данные

- `aiogram.types.Message` (текст, голос, документ, callback-состояния FSM).
- Внедряемые зависимости: `db_users`, `llm`, `json_back`, `jumis_agent`, `bot` (из `dp` workflow data).
- Команды: `/start`, `/admin`, `/logs`, `/dLogs`, `/reset`, `/uplLLM`, `/getMod`, `/setMod`, `/crTabDb`, `/dowjson`, `/up_users_cat`, `/up_facts_cat`, `/up_users`, `/up_facts`, `/up_messages`, `/up_tasks`, `/allDel`, `/cancel`.
- Для импорта — прикреплённый `.json` файл.

## Выходные данные

- Локализованные (ru/en) ответы в чат, HTML/Markdown-разметка.
- Скачивание файлов логов и JSON-бэкапов в чат.
- Изменения в БД: регистрация пользователя, `model_default`, создание/удаление таблиц, импорт строк.
- Перезапуск процесса (`os.execv`) или `sys.exit(0)` в Docker.
- Передача входящего сообщения в агента.

## Логика (Вариации)

1. Порядок роутеров: `start` → `admin` → `get_message`; первый совпавший обрабатывает сообщение.
2. `rights_verification`: только `message.chat.type == "private"` и `user_id == ADMIN_ID`; иначе отказ (ru/en) и запись в лог.
3. `/start`: `typing` → права → отказ ботам → `db_users.chek_tg_id` (INSERT ... ON CONFLICT) → сообщение «уже зарегистрирован» или создание записи `is_admin=True`; факт регистрации пишется в лог.
4. `/admin`: статическое меню команд (включая `/logs` и `/dLogs`).
5. `/setMod`: FSM `SetModelStates.waiting_for_model`; ввод проверяется по `llm.available_models` без учёта регистра, затем `db_users.db_update_user` + `llm.refresh_llm_models()`.
6. Импорт: FSM `RestoreState.waiting_file`, `name_action` из команды (01…06), файл скачивается в `DOWNLOAD`, вызывается `json_back.restore_table_from_file`, файл удаляется.
7. `/logs` отдаёт все непустые файлы из `PATH_LOGS` (основной `jumis.log` и ротации) как `*_utf8.txt` (перекодировка в UTF-8). `/dLogs` фиксирует факт очистки в логе, затем вызывает `logs.set_logger.clear_logs()`: активный файл очищается через `flush → truncate → seek(0)`, остальные файлы обнуляются; после команды `jumis.log` пуст (0 байт), в ответ уходит количество очищенных файлов.
8. `/reset` планирует отложенный рестарт через `asyncio.create_task`.
9. `get_message`: любое сообщение админа → `jumis_agent.send_jumis_mess_handler`.

## Обработка ошибок

- Доступ запрещён: ответ пользователю + `logger.error` (в т.ч. для групп).
- `/uplLLM`: `uv pip install --upgrade litellm --no-deps`; при `FileNotFoundError` — сообщение «uv не найден», при ненулевом коде — последние 4 строки stderr.
- `/getMod`: список моделей режется по строкам на куски ≤3800 символов (защита от лимита Telegram).
- Ошибки Telegram (`TelegramBadRequest`) логируются, пользователю отправляется нейтральное сообщение.
- Импорт: не-`.json` файл отклоняется; итог — `(success, good_count, bad_count)` с локализованным текстом.
- `/allDel` вызывает `drop_all_tables_and_reset_schema` (DROP SCHEMA public CASCADE) — необратимо.
