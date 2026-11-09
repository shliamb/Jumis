# SPEC: jumis_agent

## Суть работы

Ядро персонального секретаря Jumis:

- приём запросов владельца (текст/голос) и выполнение tool-loop со стримингом;
- конвертация Markdown → Telegram Rich Message с фолбэком на HTML и чистый текст;
- ведение «плавающего» виджета входящих (`pending_peers`);
- инъекция контекста неотвеченных сообщений в диалог LLM;
- `JumisAgent` также обслуживает системные триггеры планировщика.

## Входные данные

- `process_agent_request(chat_id: int, prompt_text: str)` — универсальная точка входа.
- `send_jumis_mess_handler(message)` — входящее сообщение aiogram.
- `queue_new_mess` — элементы `{chat_id, sender_id, recipient_id, tg_msg_id, msg_db_id, username, content, direction, msg_type, created_at}`.
- `message.content_type`: `text`, `voice`, `video_note`, `photo`, `document` (три последних отклоняются).
- Зависимости: `llm` (`LLMWorker`), `bot` (aiogram), `stt`, `ADMIN_ID`, `USE_RICH_MESSAGES`.

## Выходные данные

- Ответ в чат: Rich Message (HTML, до 10k символов) или HTML-фрагменты по 4000, в аварии — plain text.
- Записи в историю `LLMWorker.dialog` (user/assistant/tool).
- Состояние: `pending_peers: dict[sender_id -> {username, count, last_text}]`, `notif_msg_id`.
- Побочные эффекты: удаление/редактирование сообщений Telegram, вызовы инструментов (БД, Telethon, планировщик), сброс модели на `default`.
- Команды агента: `clear_pending_queue()` → str, `get_notifs_queue()` → JSON-строка.

## Логика (Вариации)

1. `process_agent_request`: `llm.get_tools("jumis_agent")` → служебная плашка `"..."` → `check_notif_mess()` (mock tool-call `get_pending_queue`) → `llm.add_user_message(prompt)`.
2. Цикл: `llm.refine_stream_tools(...)`.
   - `text` — накопление и редактирование плашки не чаще 1 раза в 1.5 с (только до 4000 символов).
   - `tool` — вызовы собираются, добавляется assistant-сообщение с `tool_calls`, в чат пишется статус «🔧 описание», затем `llm.call_function` для каждого и `llm.add_tool_response`; цикл повторяется.
   - финал — `add_assistant_message`, `send_jumis_response`.
3. `send_jumis_response`: Rich (`markdown_to_rich_html` + `SendRichMessage`) → при исключении HTML-фолбэк (`markdown_to_telegram_html` + split 4000) → аварийный plain text.
4. Голос: `stt.transcribe_telegram_voice`, эхо-транскрипт в чат (`🎙️ ...`), затем обычный запрос.
5. `message_processing`: новый пир → удалить старый виджет, отправить новый со звуком; тот же пир → `edit_message_text`; ответ владельца (`outbound_owner`) → удалить пир из очереди; очередь пуста → удалить виджет; новый пир при существующем виджете → пересоздать.
6. `run_queue_worker`: берёт `queue_new_mess`, обрабатывает с `wait_for(10s)`, до 3 повторов при таймауте.
7. В `finally` модель возвращается на `default`, если была переключена инструментом `change_model`.

## Обработка ошибок

- Пустой ответ LLM → служебная заметка в диалог и сообщение пользователю с просьбой повторить.
- Исключение в логике → `add_assistant_message` с пометкой о сбое и сообщение с типом/деталями ошибки.
- `TelegramRetryAfter` при стриминге → sleep `retry_after`; `TelegramBadRequest` «can't parse entities» игнорируется.
- Rich Message: `ValidationError`/`ClientDecodeError` считаются доставленными (ответ сервера невалиден по схеме) — только warning.
- HTML-фолбэк при `TelegramBadRequest` → аварийный plain text без разметки.
- Неподдерживаемые типы сообщений → отказ с текстом; `video_note`/`photo`/`document` не обрабатываются.
- Воркер: таймаут 10 с × 3 попытки, затем сообщение сбрасывается с логом.
- Ошибки удаления/редактирования виджета логируются, `notif_msg_id` сбрасывается.
