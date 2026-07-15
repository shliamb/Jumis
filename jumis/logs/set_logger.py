import logging
from config import TIME_CORRECTION, LOG_TO_FILE, PATH_LOGS
from pathlib import Path
from logging.handlers import RotatingFileHandler
import datetime



class TimezoneFormatter(logging.Formatter):
    """Форматтер с коррекцией времени"""
    def __init__(self, timezone_hours: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.timezone_offset = datetime.timedelta(hours=timezone_hours)
    
    def formatTime(self, record, datefmt=None):
        dt = datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc)
        dt_local = dt + self.timezone_offset
        return dt_local.strftime(datefmt or '%Y-%m-%d %H:%M:%S')



def set_logger(
        name: str,
        log_to_file: bool = LOG_TO_FILE,
        timezone_hours: int = TIME_CORRECTION,
        log_dir: str = PATH_LOGS,
        level = logging.INFO,
        max_size_mb: int = 10
    ) -> logging.Logger:
    """Настройка логгера с коррекцией времени"""
    
    # Если не пишем в файл - включаем стандартные логи aiogram
    if not log_to_file:
        logging.getLogger("aiogram").setLevel(logging.INFO)
        return logging
    
    # Создаем директорию для логов
    log_path = Path(log_dir) / f"{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Создаем логгер
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(level)
    
    # Файловый обработчик с ротацией
    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    
    # Форматтер с коррекцией времени
    formatter = TimezoneFormatter(
        timezone_hours=timezone_hours,
        fmt='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    return logger




# DEBUG — подробная отладочная информация

# INFO — общая информация о работе

# WARNING — предупреждения

# ERROR — ошибки

# CRITICAL — критические ошибки
