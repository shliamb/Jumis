# SPEC: utils

## Суть работы

Общие утилиты без состояния:

- `common.py` — работа с настенным временем приложения, очистка текста, нарезка HTML.
- `serialize.py` — JSON-сериализация/десериализация `datetime`, `UUID`, `Decimal`.
- `system_cron.py` — «железобетонные» системные кроны в `scheduled_tasks`: создание, оживление, защита от агента (общая механика для `cleaner` и `pgbackup`).

## Входные данные

- `datetime` с/без tzinfo, ISO-строки, словари/списки Python.
- `text: str` для очистки/нарезки.
- `TIME_ZONE` из `config.py` (по умолчанию `Europe/Moscow`).
- Для кронов: `db_tasks` (`db_search_tasks`/`db_add_task`/`db_update_task`), `scheduler` (`notify_new_task`), название, cron-выражение и инструкция модуля.

## Выходные данные

- `get_now_datetime()` / `get_date_str()` — naive-время в `TIME_ZONE`; `%Y-%m-%d %H:%M:%S` для второго.
- `to_cleen(dt)` / `to_app_tz_naive(dt)` / `get_date_datetime(str)` — приведение к naive wall-clock.
- `sanitize_human_text(text)` — дефисы вместо тире, удаление эмодзи, Markdown-заголовков и разметки, схлопывание пробелов/пустых строк.
- `split_html_text(text, 4000)` — куски по границе строки/пробела.
- `serialize_for_json` / `deserialize_from_json` — рекурсивное преобразование типов.
- `ensure_system_cron(...)` — гарантия живой строки крона (bool); `find_system_task(db_tasks, title)` — поиск по названию; `next_run(cron)` — ближайшее срабатывание; `is_system_cron(task)` — проверка «системности»; `register_system_title(title)` — регистрация защищённого названия модулем.

## Логика (Вариации)

1. Вся система работает на naive wall-clock: любое время сначала переводится в `TIME_ZONE`, затем `tzinfo` снимается.
2. `deserialize_from_json` пробует по очереди `datetime.fromisoformat`, `UUID`, `Decimal`; при неудаче возвращает исходную строку (риск: строка «2026-01-01» станет datetime).
3. `sanitize_human_text` применяется к исходящим сообщениям собеседнику (Telethon), чтобы отправить «человеческий» текст без маркдауна.
4. `split_html_text` — упрощённая нарезка без восстановления тегов (в агенте используется более безопасная версия).
5. `extract_id_from_message` / `extract_text_after_id` — разбор формата `[123]: текст` (наследие, в текущем рантайме не используется).
6. Системный крон ищется **по названию**, а не по `task_type='system_cron'`: кронов несколько (уборка 03:30, бэкап 04:30), по типу они неразличимы.
7. `is_system_cron` считает задачу системной по типу ИЛИ по зарегистрированному названию — защита работает, даже если `task_type` испорчен вручную.
8. `ensure_system_cron`: нет строки — создаёт (`tg_id = NULL`); `expired`/`failed` — оживляет; `cancelled` — уважает как осознанную паузу; `scheduled_at` уехал в прошлое >12 ч — пересчитывает.

## Обработка ошибок

- `get_date_datetime` с невалидной ISO-строкой выбрасывает `ValueError` — вызывающие (`add_task`, `update_task`) перехватывают и возвращают понятную ошибку агенту.
- `deserialize_from_json` может «перепутать» обычную строку с датой/UUID — использовать с осторожностью.
- `serialize_for_json` не обрабатывает вложенные нестандартные объекты — пробрасывает как есть.
- `ensure_system_cron` не бросает исключений: без `db_tasks` или при ошибке БД пишет error в лог и возвращает `False` — сервисы продолжают работать.
