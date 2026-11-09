# SPEC: handlers

## Суть работы

Aiogram-слой Telegram-бота (второй интерфейс, рядом с userbot):

- `start.py` — `/start`: проверка прав, защита от ботов, регистрация единственного админа.
- `admin.py` — `/admin` и сервисные команды: логи, модели, рестарт, создание/удаление таблиц, JSON-бэкап и импорт.
- `get_message.py` — fallback-роутер: любое разрешённое сообщение уходит в `JumisAgent`.
- `options.py` — callback-роутер кнопок-вариантов `ask_options` (`opt:<token>`): выбор становится следующим сообщением агента.
- `inbox.py` — callback-роутер кнопки «Очистить» под виджетом входящих (`inbox:clear`): снимает очередь и удаляет виджет **без запуска агента**.
- `cleaner.py` — обслуживание истории: `/cleanup` (предпросмотр + кнопка «Удалить»), `/cleanup_run`, `/digest_now`, `/digests`.
- `common.py` — `typing()` и `rights_verification()`.
- `dispatcher.py` — общий `Dispatcher`; `__init__.py` — порядок роутеров.

## Входные данные

- `aiogram.types.Message` (текст, голос, документ, callback-состояния FSM).
- `aiogram.types.CallbackQuery` с `data = "opt:<token>"` — нажатие на вариант ответа `ask_options`.
- `aiogram.types.CallbackQuery` с `data = "inbox:clear"` — нажатие кнопки «Очистить» под виджетом входящих (кнопку рисует `JumisAgent._inbox_keyboard`).
- Внедряемые зависимости: `db_users`, `llm`, `json_back`, `jumis_agent`, `bot` (из `dp` workflow data).
- Команды: `/start`, `/admin`, `/logs`, `/dLogs`, `/reset`, `/uplLLM`, `/getMod`, `/setMod`, `/crTabDb`, `/dowjson`, `/up_users_cat`, `/up_facts_cat`, `/up_users`, `/up_facts`, `/up_messages`, `/up_tasks`, `/allDel`, `/cancel`, `/cleanup`, `/cleanup_run`, `/digest_now`, `/digests`.
- Внедряемая зависимость `cleaner` (из `dp["cleaner"]`) для команд обслуживания истории.
- `CallbackQuery` `data = "clean:run"` / `"clean:cancel"` — подтверждение уборки.
- Для импорта — прикреплённый `.json` файл.

## Выходные данные

- Локализованные (ru/en) ответы в чат, HTML/Markdown-разметка.
- Скачивание файлов логов и JSON-бэкапов в чат.
- Изменения в БД: регистрация пользователя, `model_default`, создание/удаление таблиц, импорт строк.
- Перезапуск процесса (`os.execv`) или `sys.exit(0)` в Docker.
- Передача входящего сообщения в агента.
- Нажатие на вариант: снятие клавиатуры, отметка `▸ выбор` в сообщении и новый запрос агента с текстом выбранного варианта.
- Нажатие `inbox:clear`: `jumis_agent.pending_peers` очищен, виджет удалён, тост «🧹 Очищено» / «🧹 Cleared»; агент и LLM не вызываются (0 токенов).
- `/cleanup`: сообщение с числами предпросмотра и inline-кнопками «✅ Удалить» / «❌ Отмена».
- `clean:run`: `cleaner.run_hygiene()` и отчёт (мусор, дубли, было/стало, размер, VACUUM).
- `/digest_now [N]`: до N (1–20, по умолчанию 5) батчей самаризации и отчёт; `/digests [chat_id]`: последние 5 дайджестов.

## Логика (Вариации)

1. Порядок роутеров: `start` → `admin` → `options` → `inbox` → `get_message`; первый совпавший обрабатывает событие.
2. `rights_verification`: только `message.chat.type == "private"` и `user_id == ADMIN_ID`; иначе отказ (ru/en) и запись в лог.
3. `/start`: `typing` → права → отказ ботам → `db_users.chek_tg_id` (INSERT ... ON CONFLICT) → сообщение «уже зарегистрирован» или создание записи `is_admin=True`; факт регистрации пишется в лог.
4. `/admin`: статическое меню команд (включая `/logs` и `/dLogs`).
5. `/setMod`: FSM `SetModelStates.waiting_for_model`; ввод проверяется по `llm.available_models` без учёта регистра, затем `db_users.db_update_user` + `llm.refresh_llm_models()`.
6. Импорт: FSM `RestoreState.waiting_file`, `name_action` из команды (01…06), файл скачивается в `DOWNLOAD`, вызывается `json_back.restore_table_from_file`, файл удаляется.
7. `/logs` отдаёт все непустые файлы из `PATH_LOGS` (основной `jumis.log` и ротации) как `*_utf8.txt` (перекодировка в UTF-8). `/dLogs` фиксирует факт очистки в логе, затем вызывает `logs.set_logger.clear_logs()`: активный файл очищается через `flush → truncate → seek(0)`, остальные файлы обнуляются; после команды `jumis.log` пуст (0 байт), в ответ уходит количество очищенных файлов.
8. `/reset` планирует отложенный рестарт через `asyncio.create_task`.
9. `get_message`: любое сообщение админа → `jumis_agent.send_jumis_mess_handler`.
10. `options`: `callback_query` с `data.startswith("opt:")` → проверка `from_user.id == ADMIN_ID` → если `jumis_agent.agent_busy`, тост «ещё думаю» без запуска → `jumis_agent.consume_option(token)` (одноразовый, TTL 3600 с) → `callback.answer()` → клавиатура снимается, к выбранному варианту добавляется `▸` → `process_agent_request(chat_id, chosen_text)`.
11. `inbox:clear`: проверка `from_user.id == ADMIN_ID` (иначе alert «Нет доступа» + лог) → `callback.answer(тост по jumis_agent.admin_lang)` → `len(pending_peers)` для лога → `jumis_agent.clear_pending_queue()` (очищает очередь и удаляет виджет) → удаление `callback.message` в `try/except` (страховка для устаревшего виджета после рестарта, когда `notif_msg_id = None`). Агент не запускается, токены не тратятся.
12. `/cleanup`: права → `cleaner.scan()` (ничего не удаляет) → текст с разбивкой (пустые/медиа, болтовня, дубли, «итого»/«останется») + клавиатура `clean:run` / `clean:cancel`.
13. `clean:run`: повторная проверка `ADMIN_ID` (иначе alert и error в лог) → `cleaner.run_hygiene()` → результат редактирует сообщение предпросмотра, снимая кнопки; при `TelegramBadRequest` отправляется новым сообщением.
14. `/cleanup_run`: то же без предпросмотра (для тех, кто уже смотрел `/cleanup`). `/digest_now`: парсит число батчей (1–20, по умолчанию 5), сначала предупреждает о длительности, затем отчёт.
15. `/digests [chat_id]`: до 5 дайджестов, каждый — ID, чат, период, число сообщений и первые 600 символов сводки.

## Обработка ошибок

- Доступ запрещён: ответ пользователю + `logger.error` (в т.ч. для групп).
- `/uplLLM`: `uv pip install --upgrade litellm --no-deps`; при `FileNotFoundError` — сообщение «uv не найден», при ненулевом коде — последние 4 строки stderr.
- `/getMod`: список моделей режется по строкам на куски ≤3800 символов (защита от лимита Telegram).
- Ошибки Telegram (`TelegramBadRequest`) логируются, пользователю отправляется нейтральное сообщение.
- Импорт: не-`.json` файл отклоняется; итог — `(success, good_count, bad_count)` с локализованным текстом.
- `/allDel` вызывает `drop_all_tables_and_reset_schema` (DROP SCHEMA public CASCADE) — необратимо.
- `inbox:clear`: повторное удаление сообщения (виджет уже удалён внутри `clear_pending_queue`) → `TelegramBadRequest` молча игнорируется; если `callback.message` недоступен (`InaccessibleMessage`), удаление пропускается, очередь всё равно очищена.
- `options`: просроченный или повторно нажатый токен → тост «вариант устарел»; чужой `from_user` → отказ и запись в лог; `agent_busy` → тост «ещё думаю», токен остаётся; ошибка редактирования сообщения → клавиатура снимается через `edit_reply_markup`, запрос агенту всё равно стартует.
- `/cleanup*`, `/digest*`: без `cleaner` в `dp` → сообщение «модуль чистки недоступен»; ошибка сканирования/уборки → текст исключения в чат и traceback в лог; `clean:cancel` при уже снятой клавиатуре → `TelegramBadRequest` игнорируется.
