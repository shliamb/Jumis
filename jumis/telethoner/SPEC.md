# SPEC: telethoner

## Суть работы

Userbot-слой на Telethon (работа от имени владельца):

- ловит личные входящие/исходящие сообщения и реакции владельца;
- нормализует события в `task_payload` для `IngestionWorker`;
- отправляет сообщения собеседникам/каналам по `telegram_id` или `username`;
- **сам управляет сессией**: проверяет, что ключ жив, и, если нужно, проводит вход
  (QR → код → пароль 2FA) кнопками в боте через `/login`;
- сообщает владельцу в aiogram-бот о смене состояния сессии и напоминает раз в сутки,
  пока вход не восстановлен;
- переподключается штатным `auto_reconnect=True` Telethon, внешний цикл страхует
  холодный старт и полностью потерянное соединение.

Файл сессии — единственный и по абсолютному пути (`config.PATH_SESSION`).

## Входные данные

- Telethon-события `NewMessage` и `MessageEdited` (только личные чаты).
- Сессия по `config.PATH_SESSION` (по умолчанию `<корень>/session_jumis.session`,
  переопределяется `SESSION_PATH` из окружения), `API_ID`, `API_HASH` из `.env`.
- `send_message(message_text, telegram_id, username)`.
- Шаги входа из `handlers/login.py`: `auth.start_qr()`, `auth.wait_qr(timeout)`,
  `auth.send_code(phone, force_sms)`, `auth.verify_code(code)`,
  `auth.verify_password(password)`, `auth.cancel()`.
- `config.TELETHON_CATCH_UP` — догонять ли пропущенные апдейты (по умолчанию `False`).
- Прокси не используется: подключение прямое.

## Выходные данные

- В `queue_messages` кладётся `{"user": user_payload, "message": message_payload}`.
  - `user`: `tg_id`, `username`, `first_name`, `last_name`, `full_name`, `phone`, `lang_code`, флаги `is_premium/is_verified/is_scam/is_fake/is_restricted/is_mutual_contact`.
  - `message`: `tg_msg_id`, `chat_id`, `is_outgoing`, `content`, `created_at` (naive), `msg_type`, `media_file_id`, `media_name`, для voice/audio — `audio_bytes`.
- `send_message` → `peer_id` (int; отрицательный для канала) или строка с текстом ошибки.
- `health()` → `{"state": AuthState, "detail": str, "me_id": int | None}`;
  `whoami()` → `{"id", "name", "username"}` (пусто, если вход не выполнен).
- Состояние сессии (`AuthState`): `ok`, `not_configured`, `no_session`, `revoked`,
  `waiting_qr`, `waiting_code`, `waiting_password`, `offline`, `locked`, `error`.
- Уведомления владельцу через `telethoner.notify.UserbotNotifier`: одно сообщение на
  смену состояния (`no_session`, `revoked`, `not_configured`, `locked`, `error`)
  и напоминание раз в 24 ч, пока состояние не `ok`/`waiting_*`.
- Файл сессии `session_jumis.session` (создаётся/обновляется Telethon).

## Логика (Вариации)

1. `NewMessage`: отбрасываются не-private чаты и боты; для входящих — `scam`/`fake`; собирается `user_payload`; `detect_media_info` определяет тип (`photo`, `voice`, `audio`, `video`, `sticker`, `document`, `contact`, `location`, `poll`, `other_media`) и качает voice/audio в RAM.
2. Дата события конвертируется в `TIME_ZONE` и очищается от tzinfo (`to_app_tz_naive`), иначе берётся `get_now_datetime()`.
3. `MessageEdited` с реакциями: если реакцию поставил владелец — синтетическое сообщение `msg_type="reaction"`, `content="Реакция: <emoji>"`, `tg_msg_id = -timestamp_ms`, `direction` определит ingest по `sender_id == ADMIN_ID`.
4. `_ensure_client`: ровно один клиент на процесс — создаётся при первом обращении, потом переиспользуется; путь `PATH_SESSION`, `catch_up=TELETHON_CATCH_UP`; без `API_ID`/`API_HASH` падает (машина входа до этого не дойдёт и сообщит `not_configured`); обработчики (`_register_handlers`) навешиваются один раз.
5. `run`: `auth.check()` → если `ok`, то `run_until_disconnected()`; если `no_session`/`revoked`/`not_configured` или `waiting_*` — ждём `auth.ready` до 300 с и перепроверяем (так подхватывается вход, сделанный снаружи через `create_session.py`); прочие не-`ok` (сеть, занятый файл) — пауза 5 с и повтор. `client.start()` не используется никогда: он спрашивает телефон через `input()`, а в боте нет терминала.
6. `TelethonAuth.check`: `get_me()` реальным запросом; при ответе — `ok` и `me_id`; иначе смотрим `client.session.auth_key`: ключа нет → `no_session` (первая настройка), ключ есть → `revoked` (Telegram отозвал). Причина важна для текста владельцу.
7. QR-вход: `start_qr()` → `tg://login?token=…` (кнопка-ссылка в боте), состояние `waiting_qr`; `wait_qr(timeout≈18)` → `ok` / `timeout` (токен истёк, бот берёт новый) / `password` / `flood` / `error`. 2FA после QR: `wait` бросает `SessionPasswordNeededError`, пароль добирается `verify_password`.
8. Вход по коду: `send_code(phone, force_sms)` нормализует номер (`+7900…`, ведущая `8` → `+7`), сохраняет `phone_code_hash` и тип доставки (`app`/`sms`/`call`/`flash`/`missed`/`email`/`fragment`) — бот говорит, где искать код; `verify_code` передаёт hash явно (переживает пересоздание клиента) и режет всё, кроме цифр.
9. 2FA: `verify_password` вызывает `sign_in(password=…)`; пароль нигде не сохраняется и не логируется.
10. Успех любого входа — `_finish`: `me_id`, сброс потока, `ready.set()`, состояние `ok`; `run()` тут же начинает слушать.
11. `cancel()` — сброс потока и перепроверка состояния; `check(ignore_flow=True)` не отвечает `waiting_*`, чтобы статус в `/admin` был настоящим.
12. `send_message`: текст режется на куски ≤4000 по абзацу/строке/пробелу, отправляется по `telegram_id or username`; возвращается `utils.get_peer_id(entity)`. Если состояние не `ok` — строка `❌ Error: … state: X … /login` (агент видит причину, а не сырой `AUTH_KEY_UNREGISTERED`).
13. `health()`/`whoami()` — для `/admin`, мастера входа и сообщения «кто подключён».
14. `create_session.py` — резервный вход из терминала, тот же `PATH_SESSION`; запускать только при остановленном боте (два писателя в один файл Telethon не переживёт).

## Docker Compose (обязательно учитывать)

Файл сессии — **состояние, а не код**. В образ он не кладётся: каталог `./state`
монтируется в контейнер как `/state`, `SESSION_PATH=/state/session_jumis.session`.
Это **один и тот же файл** на хосте и в контейнере (bind-mount каталога, не копия),
поэтому:

- Копировать файл «в контейнер» и «обратно» не нужно и нельзя: копия — второй
  экземпляр, который разъезжается при сбое (OOM, kill, ребут, `docker compose down`),
  и вход, сделанный внутри контейнера, был бы потерян. При bind-mount запись сразу
  видна на хосте, пересборка контейнера файл не трогает.
- Монтировать именно **каталог**: bind-mount несуществующего файла Docker молча
  создаёт каталог, и вход после этого невозможен. Вместе с файлом в каталоге живёт
  sqlite-журнал (`-journal`), ему тоже нужен том.
- Один писатель: не запускать host-версию и контейнер одновременно — sqlite-lock
  (`locked`) в лучшем случае, отзыв ключа Telegram (`revoked`) в худшем.
- `IN_DOCKER=1` (см. `config.py`) переключает пути логов/бэкапов/JSON и `/reset`
  на `sys.exit`; на `PATH_SESSION` он не влияет — путь берётся из `SESSION_PATH`
  или из `<корень>/state/session_jumis.session`.

## Обработка ошибок

- Нет `chat`/`sender` — событие молча пропускается.
- Боты и не-private чаты игнорируются.
- Ошибки классифицируются (`auth.classify_error`): `AuthKeyUnregistered`/`SessionRevoked`/`UserDeactivated*`/`AuthKeyDuplicated` → `revoked`; sqlite `database is locked` → `locked`; `ConnectionError`/`OSError`/таймаут → `offline`; прочее → `error`.
- `client.start()` со `stdin` больше не используется — именно он давал `EOFError`, лог «connection lost» каждые 5 с и мёртвый userbot при живом aiogram.
- `get_me()` у Telethon глотает `UnauthorizedError` и отдаёт `None`; `no_session` и `revoked` различаются по `client.session.auth_key`, иначе владелец не поймёт, первая это настройка или повторное подтверждение.
- `send_code`: `PhoneNumberInvalid` → `invalid_phone`, `PhoneNumberUnoccupied` → `no_account`, `PhoneNumberBanned` → `banned`, `FloodWait` → `flood` с `seconds`; остальное — `error` с кодом состояния.
- `verify_code`: `PhoneCodeInvalid`/`Empty` → `invalid_code` (бот даёт 3 попытки), `PhoneCodeExpired`/`HashEmpty` → `expired_code` (кнопка «прислать ещё»), `SessionPasswordNeeded` → `password`.
- `verify_password`: `PasswordHashInvalid` → `wrong_password` (3 попытки), `FloodWait` → `flood`.
- Перезапуск бота посреди входа теряет `phone_code_hash` (он в памяти) — вход начинается заново с номера; это ожидаемо.
- Ошибка уведомления (нет прав бота, сеть) логируется и на `run()` не влияет.
- `CancelledError` → disconnect и проброс (корректная остановка); `_disconnect` обнуляет клиента, следующий цикл создаст его заново.
- Ошибки скачивания медиа не перехватываются отдельно — событие может быть потеряно (лог в Telethon).
