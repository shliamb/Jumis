# SPEC: utils

## Суть работы

Общие утилиты без состояния:

- `common.py` — работа с настенным временем приложения, очистка текста, нарезка HTML.
- `serialize.py` — JSON-сериализация/десериализация `datetime`, `UUID`, `Decimal`.

## Входные данные

- `datetime` с/без tzinfo, ISO-строки, словари/списки Python.
- `text: str` для очистки/нарезки.
- `TIME_ZONE` из `config.py` (по умолчанию `Europe/Moscow`).

## Выходные данные

- `get_now_datetime()` / `get_date_str()` — naive-время в `TIME_ZONE`; `%Y-%m-%d %H:%M:%S` для второго.
- `to_cleen(dt)` / `to_app_tz_naive(dt)` / `get_date_datetime(str)` — приведение к naive wall-clock.
- `sanitize_human_text(text)` — дефисы вместо тире, удаление эмодзи, Markdown-заголовков и разметки, схлопывание пробелов/пустых строк.
- `split_html_text(text, 4000)` — куски по границе строки/пробела.
- `serialize_for_json` / `deserialize_from_json` — рекурсивное преобразование типов.

## Логика (Вариации)

1. Вся система работает на naive wall-clock: любое время сначала переводится в `TIME_ZONE`, затем `tzinfo` снимается.
2. `deserialize_from_json` пробует по очереди `datetime.fromisoformat`, `UUID`, `Decimal`; при неудаче возвращает исходную строку (риск: строка «2026-01-01» станет datetime).
3. `sanitize_human_text` применяется к исходящим сообщениям собеседнику (Telethon), чтобы отправить «человеческий» текст без маркдауна.
4. `split_html_text` — упрощённая нарезка без восстановления тегов (в агенте используется более безопасная версия).
5. `extract_id_from_message` / `extract_text_after_id` — разбор формата `[123]: текст` (наследие, в текущем рантайме не используется).

## Обработка ошибок

- `get_date_datetime` с невалидной ISO-строкой выбрасывает `ValueError` — вызывающие (`add_task`, `update_task`) перехватывают и возвращают понятную ошибку агенту.
- `deserialize_from_json` может «перепутать» обычную строку с датой/UUID — использовать с осторожностью.
- `serialize_for_json` не обрабатывает вложенные нестандартные объекты — пробрасывает как есть.
