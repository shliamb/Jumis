# SPEC: jumis_agent

## Суть работы

Ядро персонального секретаря Jumis:

- приём запросов владельца (текст/голос) и выполнение tool-loop со стримингом;
- финальный ответ с кнопками-вариантами (`ask_options`): текст ответа + inline-клавиатура, после чего итерация агента завершается;
- конвертация Markdown → Telegram Rich Message с фолбэком на HTML и чистый текст;
- ведение «плавающего» виджета входящих (`pending_peers`) с кнопкой «Очистить» (`inbox:clear`), которая снимает очередь и удаляет виджет без запуска агента;
- инъекция контекста неотвеченных сообщений в диалог LLM;
- `JumisAgent` также обслуживает системные триггеры планировщика.

## Входные данные

- `process_agent_request(chat_id: int, prompt_text: str)` — универсальная точка входа.
- `send_jumis_mess_handler(message)` — входящее сообщение aiogram.
- `queue_new_mess` — элементы `{chat_id, sender_id, recipient_id, tg_msg_id, msg_db_id, username, content, direction, msg_type, created_at}`.
- `message.content_type`: `text`, `voice`, `video_note`, `photo`, `document` (три последних отклоняются).
- Зависимости: `llm` (`LLMWorker`), `bot` (aiogram), `stt`, `ADMIN_ID`, `USE_RICH_MESSAGES`.

- `admin_lang: str | None` — язык владельца для подписи кнопки «Очистить»; передаёт `run_bot.get_admin_lang()` из `users.lang_code` (по умолчанию `en`).

## Выходные данные

- Ответ в чат: Rich Message (HTML, до 10k символов) или HTML-фрагменты по 4000, в аварии — plain text; для `ask_options` — то же сообщение с inline-клавиатурой.
- Виджет входящих: HTML-сообщение в чате владельца с inline-кнопкой `inbox:clear` (подпись `🧹 Очистить` / `🧹 Clear` по `admin_lang`); клавиатура передаётся и при `send_message`, и при каждом `edit_message_text`.
- Записи в историю `LLMWorker.dialog` (user/assistant/tool).
- Состояние: `pending_peers: dict[sender_id -> {username, count, last_text}]`, `notif_msg_id`, `admin_lang` (`ru`/`en`, нормализуется в `_norm_lang`), `option_tokens: dict[token -> {chat_id, question, option, created_at}]`, `options_msg_id: dict[chat_id -> message_id]`, флаг `agent_busy`.
- Побочные эффекты: удаление/редактирование сообщений Telegram, вызовы инструментов (БД, Telethon, планировщик), сброс модели на `default`.
- Команды агента: `clear_pending_queue()` → str, `get_notifs_queue()` → JSON-строка.

## Логика (Вариации)

1. `process_agent_request`: `llm.get_tools("jumis_agent")` → служебная плашка `"..."` → `check_notif_mess()` (mock tool-call `get_pending_queue`) → `llm.add_user_message(prompt)`.
2. Цикл: `llm.refine_stream_tools(...)`.
   - `text` — накопление и редактирование плашки не чаще 1 раза в 1.5 с (только до 4000 символов).
   - `tool` — вызовы собираются, добавляется assistant-сообщение с `tool_calls`, в чат пишется статус «🔧 описание», затем `llm.call_function` для каждого и `llm.add_tool_response`.
   - если результат инструмента — `{"status": "options_shown", ...}` (это `ask_options`), вызывается `show_options` и цикл **прерывается**; иначе цикл повторяется.
   - финал — `add_assistant_message`, `send_jumis_response`.
3. `show_options`: финальный текст + вопрос рендерятся в HTML и редактируют плашку с `InlineKeyboardMarkup`; кнопки получают одноразовые токены `secrets.token_hex(4)` (`callback_data = "opt:<token>"`, ≤64 байт), токен живёт 3600 с. В каждом чате активна только последняя пачка вариантов: старые токены удаляются, клавиатура предыдущего сообщения снимается. Длинный ответ (>4000 символов HTML) отправляется обычным путём, кнопки — отдельным сообщением. В чатах, отличных от `ADMIN_ID`, кнопки не создаются (ответ уходит как обычно).
4. `consume_option(token)`: атомарно извлекает вариант (одноразовый), удаляет остальные токены чата и проверяет TTL; при просрочке/повторе возвращает `None`.
5. `send_jumis_response`: Rich (`markdown_to_rich_html` + `SendRichMessage`) → при исключении HTML-фолбэк (`markdown_to_telegram_html` + split 4000) → аварийный plain text.
6. Голос: `stt.transcribe_telegram_voice`, эхо-транскрипт в чат (`🎙️ ...`), затем обычный запрос.
7. `message_processing`: новый пир → удалить старый виджет, отправить новый со звуком; тот же пир → `edit_message_text`; ответ владельца (`outbound_owner`) → удалить пир из очереди; очередь пуста → удалить виджет; новый пир при существующем виджете → пересоздать. В обоих путях (`send_message` и `edit_message_text`) передаётся `reply_markup=self._inbox_keyboard()` — кнопка `inbox:clear` не теряется при перерисовке.
8. `run_queue_worker`: берёт `queue_new_mess`, обрабатывает с `wait_for(10s)`, до 3 повторов при таймауте.
9. В `finally` сбрасывается `agent_busy` и модель возвращается на `default`, если была переключена инструментом `change_model`.
10. `admin_lang`: читается в `run_bot.get_admin_lang()` из `users.lang_code` админа и передаётся в конструктор; определяет только подписи кнопок (`INBOX_LABELS`) и тост в `handlers/inbox.py`.

## Обработка ошибок

- Пустой ответ LLM → служебная заметка в диалог и сообщение пользователю с просьбой повторить.
- Исключение в логике → `add_assistant_message` с пометкой о сбое и сообщение с типом/деталями ошибки.
- `TelegramRetryAfter` при стриминге → sleep `retry_after`; `TelegramBadRequest` «can't parse entities» игнорируется.
- Rich Message: `ValidationError`/`ClientDecodeError` считаются доставленными (ответ сервера невалиден по схеме) — только warning.
- HTML-фолбэк при `TelegramBadRequest` → аварийный plain text без разметки.
- `ask_options`: тема/кнопки не отрисовались (удалённая плашка, ошибка парсинга HTML) → плашка удаляется, сообщение отправляется заново (HTML, при неудаче — plain text); невалидный результат инструмента → кнопок нет, цикл продолжается.
- Неподдерживаемые типы сообщений → отказ с текстом; `video_note`/`photo`/`document` не обрабатываются.
- Воркер: таймаут 10 с × 3 попытки, затем сообщение сбрасывается с логом.
- Ошибки удаления/редактирования виджета логируются, `notif_msg_id` сбрасывается.
- `inbox:clear`: удаление виджета внутри `clear_pending_queue` и повторное удаление сообщения из хендлера — второе падает с `TelegramBadRequest` «message not found» и молча игнорируется; после рестарта (`notif_msg_id = None`) хендлер удаляет именно сообщение, на котором нажали.
