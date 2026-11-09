# SPEC: telethoner

## Суть работы

Userbot-слой на Telethon (работа от имени владельца):

- ловит личные входящие/исходящие сообщения и реакции владельца;
- нормализует события в `task_payload` для `IngestionWorker`;
- отправляет сообщения собеседникам/каналам по `telegram_id` или `username`;
- переподключается штатным `auto_reconnect=True` Telethon, внешний цикл страхует холодный старт.

## Входные данные

- Telethon-события `NewMessage` и `MessageEdited` (только личные чаты).
- Сессия `session_jumis.session`, `API_ID`, `API_HASH` из `.env`.
- `send_message(message_text, telegram_id, username)`.
- Прокси не используется: подключение прямое.

## Выходные данные

- В `queue_messages` кладётся `{"user": user_payload, "message": message_payload}`.
  - `user`: `tg_id`, `username`, `first_name`, `last_name`, `full_name`, `phone`, `lang_code`, флаги `is_premium/is_verified/is_scam/is_fake/is_restricted/is_mutual_contact`.
  - `message`: `tg_msg_id`, `chat_id`, `is_outgoing`, `content`, `created_at` (naive), `msg_type`, `media_file_id`, `media_name`, для voice/audio — `audio_bytes`.
- `send_message` → `peer_id` (int; отрицательный для канала) или строка с текстом ошибки.

## Логика (Вариации)

1. `NewMessage`: отбрасываются не-private чаты и боты; для входящих — `scam`/`fake`; собирается `user_payload`; `detect_media_info` определяет тип (`photo`, `voice`, `audio`, `video`, `sticker`, `document`, `contact`, `location`, `poll`, `other_media`) и качает voice/audio в RAM.
2. Дата события конвертируется в `TIME_ZONE` и очищается от tzinfo (`to_app_tz_naive`), иначе берётся `get_now_datetime()`.
3. `MessageEdited` с реакциями: если реакцию поставил владелец — синтетическое сообщение `msg_type="reaction"`, `content="Реакция: <emoji>"`, `tg_msg_id = -timestamp_ms`, `direction` определит ingest по `sender_id == ADMIN_ID`.
4. `_create_client`: создаёт `TelegramClient('session_jumis', API_ID, API_HASH)` и регистрирует оба обработчика; `auto_reconnect` оставлен по умолчанию (`True`).
5. `run`: бесконечный цикл `_create_client()` → `client.start()` → `run_until_disconnected()`; при исключении — `_disconnect()` и повтор через 5 с; при `CancelledError` — `_disconnect()` и проброс.
6. `_disconnect` обнуляет `self.client`; `stop()` — публичная обёртка для завершения приложения (вызывается из `run_bot`).
7. `send_message`: текст режется на куски ≤4000 по абзацу/строке/пробелу, отправляется по `telegram_id or username`; возвращается `utils.get_peer_id(entity)`.
8. `create_session.py` — отдельный одноразовый скрипт авторизации.

## Обработка ошибок

- Нет `chat`/`sender` — событие молча пропускается.
- Боты и не-private чаты игнорируются.
- Ошибка подключения/обрыва — печать, `_disconnect()`, пауза 5 с и новая попытка с чистым клиентом; цикл не завершается.
- `CancelledError` → disconnect и проброс (корректная остановка).
- `send_message`: пустой текст / нет цели / клиент не подключён / ошибка Telethon → строка с префиксом `❌ Error`; вызывающий (`send_mess_peer`) распознаёт её по типу `str`.
- Ошибки скачивания медиа не перехватываются отдельно — событие может быть потеряно (лог в Telethon).
