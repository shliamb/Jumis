# SPEC: ingest

## Суть работы

Конвейер сохранения переписки:

- вычитывает события Telethon из `queue_messages`;
- создаёт/обновляет профиль собеседника;
- транскрибирует голосовые;
- сохраняет сообщения в БД и запускает фоновую векторизацию;
- публикует событие в `queue_new_mess` для виджета агента.

## Входные данные

- Элементы `queue_messages`: `{"user": {...}, "message": {...}}` (см. `jumis/telethoner`).
- `db_messages`, `db_users`, `telethon_client` (для эха транскрипта в «Избранное»).
- `stt` (глобальный `FastTranscriber`), `embedder` (глобальный `TextEmbedder`).
- `ADMIN_ID` — разделяет входящие/исходящие.

## Выходные данные

- Строки в `users` и `messages`.
- Вектор `messages.embedding` + `is_embedded = TRUE` (в фоне).
- Элементы `queue_new_mess`: `{chat_id, sender_id, recipient_id, tg_msg_id, msg_db_id, username, content, direction, msg_type, created_at}`.
- Для голосовых из «Избранного» — отправленный обратно текст через Telethon.

## Логика (Вариации)

1. `run()` — бесконечный цикл `queue_messages.get()` → `_process_and_save()` → `task_done()`.
2. `direction = "outbound_owner"`, если `sender_id == ADMIN_ID`, иначе `inbound_peer`.
3. «Избранное» (`sender_id == chat_id == ADMIN_ID`): голосовое транскрибируется, текст отправляется обратно в чат и дальше не сохраняется.
4. Для входящих — `db_users.add_user({tg_id, username, full_name, phone, lang_code})` (ON CONFLICT — без дублей).
5. Голос (`msg_type == "voice"` и `audio_bytes`): `stt.transcribe(bytes, ".ogg")`; пустой результат → `[Голосовое сообщение без речи]`, ошибка → `[Ошибка расшифровки голосового]`.
6. В БД попадают только `voice` и `text` (`white_type`); остальные типы проходят дальше без записи (`msg_db_id = None`).
7. Векторизация — только для текста, не начинающегося с `[`, в фоне (`asyncio.create_task`, набор `background_tasks`).
8. Событие всегда уходит в `queue_new_mess` (даже для медиа без текста).
9. Проверка black/white-листов закомментирована (задел на будущее).

## Обработка ошибок

- Нет `chat_id` или `tg_id` → сообщение пропускается с warning.
- Ошибка сохранения сообщения → лог, событие не публикуется.
- Ошибка STT → текст-заглушка, сообщение сохраняется.
- Ошибка векторизации → лог, сообщение остаётся без вектора (`is_embedded = FALSE`).
- Ошибки `_process_and_save` ловятся в `run`, очередь продолжает работу; `CancelledError` завершает воркер.
