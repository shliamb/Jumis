# SPEC: logs

## Суть работы

Единое логирование всего приложения:

- один общий логгер `logger` (`logging.getLogger("jumis")`) для всех модулей;
- один файл `jumis/logs/files/jumis.log` с ротацией;
- дублирование в консоль;
- `setup()` вызывается один раз в `run_bot.py` до импорта тяжёлых модулей.

## Входные данные

- `LOG_LEVEL` — из `.env` (по умолчанию `INFO`), приводится к верхнему регистру.
- `LOG_TO_CONSOLE` (по умолчанию `True`), `LOG_MAX_SIZE_MB` (10), `LOG_BACKUP_COUNT` (5).
- `PATH_LOGS` (абсолютный путь `<project>/jumis/logs/files`), `TIME_ZONE`.
- Модули импортируют только `logger`: `from logs.set_logger import logger`.
- `run_bot.py` импортирует `logger` и `setup as setup_logger` и вызывает `setup_logger()`.

## Выходные данные

- `logger: logging.Logger` — единый объект, доступный во всех модулях.
- Файл `jumis/logs/files/jumis.log` (UTF-8) + сегменты `jumis.log.1…5` при ротации.
- Дублирование строк в `stdout`.
- Формат: `%(asctime)s [%(levelname)s] %(module)s:%(lineno)d: %(message)s`, время в `TIME_ZONE`.
- `clear_logs() -> int` — очистка всех файлов логов (для `/dLogs`), возвращает число очищенных файлов.

## Логика (Вариации)

1. `setup()` идемпотентен (флаг `_configured`): повторный вызов ничего не делает.
2. Корневой логгер очищается от старых handlers и получает `RotatingFileHandler` + `StreamHandler`.
3. `logger` пишет через `propagate=True` в корневые handlers; имя записи остаётся `jumis`.
4. `TimezoneFormatter.formatTime` переводит `record.created` в `TIME_ZONE`.
5. `%(module)s:%(lineno)d` в формате автоматически показывает файл и строку вызова, поэтому отдельные логгеры на модуль не нужны.
6. Шумные библиотеки (`aiogram`, `telethon`, `litellm`, `httpx`, `asyncio`, `urllib3`) переводятся на `WARNING`.
7. `clear_logs()`: для активного handler — `flush` → truncate файла → `seek(0)` (без смены offset), остальные файлы папки (ротации, `test.md`) просто обнуляются.

## Обработка ошибок

- Ошибка создания каталога/файла лога не перехватывается — падение на старте (проверить права на `jumis/logs/files`).
- Если `setup()` не был вызван, у `logger` нет handlers; сообщения уровня WARNING+ уходят в `lastResort` (stderr), INFO теряется.
- Повторный `setup()` безопасен и не дублирует вывод.
- Ротация: при превышении `LOG_MAX_SIZE_MB` создаются `jumis.log.1…N` (N = `LOG_BACKUP_COUNT`).
- Логирование потокобезопасно (стандартный `logging`), отдельная синхронизация не нужна.
