# jumis/logs/set_logger.py
"""
Единое логирование Jumis.

Использование в любом модуле:
    from logs.set_logger import logger
    logger.info("...")

Один раз при запуске (run_bot.py):
    from logs.set_logger import logger, setup as setup_logger
    setup_logger()

Все записи идут в один файл: jumis/logs/files/jumis.log
(плюс дублирование в консоль). Ротация — LOG_MAX_SIZE_MB * LOG_BACKUP_COUNT.
"""
import datetime
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from config import (
    LOG_BACKUP_COUNT,
    LOG_LEVEL,
    LOG_MAX_SIZE_MB,
    LOG_TO_CONSOLE,
    PATH_LOGS,
    TIME_ZONE,
)


class TimezoneFormatter(logging.Formatter):
    """Форматтер, печатающий время в часовом поясе приложения."""

    def __init__(self, time_zone_str: str = TIME_ZONE, **kwargs):
        super().__init__(**kwargs)
        self.tz = ZoneInfo(time_zone_str)

    def formatTime(self, record, datefmt=None):
        dt = datetime.datetime.fromtimestamp(record.created, tz=self.tz)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


# Единственный логгер приложения. Модуль и строка вызова добавляются
# автоматически форматтером (%(module)s:%(lineno)d).
logger = logging.getLogger("jumis")

_configured = False


def setup() -> None:
    """Настраивает единый файл лога и консоль. Вызывать один раз при старте."""
    global _configured
    if _configured:
        return

    level = getattr(logging, str(LOG_LEVEL).upper(), logging.INFO)

    log_file = Path(PATH_LOGS) / "jumis.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = TimezoneFormatter(
        fmt="%(asctime)s [%(levelname)s] %(module)s:%(lineno)d: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Полностью подменяем обработчики root: один файл + одна консоль.
    # Защищает от дублей, если basicConfig уже вызывался кем-то из библиотек.
    root.handlers.clear()

    file_handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=LOG_MAX_SIZE_MB * 1024 * 1024,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if LOG_TO_CONSOLE:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    # Наш логгер пишет в root, поэтому все сообщения попадают в общий файл.
    logger.setLevel(level)
    logger.propagate = True

    # Шумные библиотеки — только предупреждения и ошибки.
    for noisy in ("aiogram", "telethon", "litellm", "httpx", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def clear_logs() -> int:
    """Очищает все файлы логов. Активный файл не ломает позицию записи."""
    cleared = 0
    active_path = None

    # Активный RotatingFileHandler держит открытый файл: сначала flush,
    # затем truncate и seek(0), иначе следующие строки уйдут со старым offset.
    for handler in logging.getLogger().handlers:
        if isinstance(handler, RotatingFileHandler):
            handler.acquire()
            try:
                handler.flush()
                active_path = Path(handler.baseFilename)
                active_path.write_text("", encoding="utf-8")
                if handler.stream:
                    handler.stream.seek(0)
                cleared += 1
            except Exception:
                logger.exception("Failed to clear active log file")
            finally:
                handler.release()

    for entry in Path(PATH_LOGS).iterdir():
        if not entry.is_file() or entry == active_path:
            continue
        try:
            if entry.stat().st_size:
                entry.write_text("", encoding="utf-8")
                cleared += 1
        except Exception:
            logger.exception("Failed to clear log file: %s", entry)

    return cleared
