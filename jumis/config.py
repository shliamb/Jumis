import os
from dotenv import load_dotenv

# Читаем .env до любых вычислений: ниже от окружения зависит, например, путь
# к файлу сессии (в Docker его монтируют отдельным томом).
load_dotenv()


def _env_int(name: str):
    """int из окружения без падения на старте: нет значения или мусор → None.

    Раньше `int(os.environ.get(...))` ронял импорт config.py, то есть бот не
    поднимался вообще и сказать владельцу «в .env нет api_id» было некому.
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


# =====================================================================
# TIMEZONE & TIME MANAGEMENT ARCHITECTURE / АРХИТЕКТУРА ВРЕМЕНИ
# =====================================================================
#
# RU:
# 1. База данных (docker-compose.yml): Задайте `TZ` (напр. TZ=Europe/Moscow).
#    PostgreSQL будет генерировать правильное локальное время для NOW().
# 2. Приложение (config.py): Переменная `TIME_ZONE` управляет временем Python.
#    Если сервер в другой часовой зоне — укажите здесь нужную зону.
# 3. Принцип: Код переводит всё в целевую зону и сдирает `tzinfo` (.replace(tzinfo=None)).
#    Вся система работает строго на чистом настенном времени (naive datetime).
#
# EN:
# 1. Database (docker-compose.yml): Set `TZ` env (e.g. TZ=Europe/Moscow).
#    PostgreSQL will generate the correct local wall-clock time for NOW().
# 2. Application (config.py): `TIME_ZONE` controls Python wall-clock time.
#    If server physical location differs, set target timezone here.
# 3. Concept: All datetimes are converted to `TIME_ZONE` and stripped of tzinfo.
#    The entire app operates strictly on clean naive wall-clock datetimes.
# =====================================================================


#### BASIC CONFIG (set it up manually): ####

# Признак запуска внутри контейнера: docker-compose.yml выставляет IN_DOCKER=1.
# От него зависят пути к логам/бэкапам/JSON и поведение /reset (sys.exit вместо
# os.execv — процесс поднимает restart-политика Docker).
DOCKER = (os.environ.get("IN_DOCKER") or "").strip() == "1"

# Куда стучимся за PostgreSQL. Переопределяется из окружения: в контейнере
# сеть host и база доступна на 127.0.0.1:15433 (внешний порт сервиса postgres).
HOST = (os.environ.get("DB_HOST") or "").strip() or ("127.0.0.1" if DOCKER else "localhost")
PORT = _env_int("DB_PORT") or (5432 if DOCKER else 15433)
MAX_SIZE_DOC = 2 # 2 мегабайт
TIME_ZONE = 'Europe/Moscow' # +03:00


#### LOGGING: ####
# Один общий файл лога (ротация) + дублирование в консоль.
# Уровень читается из .env (LOG_LEVEL), по умолчанию INFO.
LOG_TO_CONSOLE = True
LOG_MAX_SIZE_MB = 10
LOG_BACKUP_COUNT = 5



# #### FOLDERS: ####

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH_LOGS = "/jumis/logs/files" if DOCKER else os.path.join(BASE_DIR, "jumis", "logs", "files")

# Каталог состояния: единственный файл сессии Telethon.
# Путь одинаковый для локального запуска и для контейнера: в compose каталог
# `./state` монтируется в `/state`, поэтому файл физически один — что записано
# в контейнере, сразу лежит на хосте. Копировать туда-обратно нечего.
# Переопределяется `SESSION_PATH` (нужно, если каталог смонтирован в другое место).
STATE_DIR = os.path.join(BASE_DIR, "state")
PATH_SESSION = (os.environ.get("SESSION_PATH") or "").strip() or os.path.join(
    STATE_DIR, "session_jumis.session"
)
try:
    os.makedirs(os.path.dirname(PATH_SESSION), exist_ok=True)
except OSError:
    # Каталог недоступен (read-only окружение): Telethon скажет об этом сам.
    pass

# Догонять ли сообщения, пропущенные пока бот был выключен (catch_up в Telethon).
# По умолчанию выключено: Telethon заново отдаёт старые апдейты, а дайджест их
# уже удалял — история может «воскреснуть» дублем. Включать осознанно.
TELETHON_CATCH_UP = False

# PATH_LOGS = "/logs/" if DOCKER else "../logs/"
DOWNLOAD = "/jumis/downloads/" if DOCKER else "jumis/downloads"
PATH_JSON = "/jumis/json/" if DOCKER else "jumis/json"
PATH_BACKUPS = "/jumis/backups" if DOCKER else os.path.join(BASE_DIR, "jumis", "backups")
# OUTPUT = "/jumis/output/" if DOCKER else "../jumis/output/"
# SYST_CONT_FOLDER = "/" if DOCKER else "../"


#### BUFFERING INPUT MESSAGES ####
BUFFER_IDLE_SEC: int = 30  # Пауза молчания (в сек) перед отправкой накопителя


#### LLMs: ####

# DeepSeek:
DEFAULT_FALLBACK_MODEL = "deepseek/deepseek-v4-flash" # "gemini/gemini-3.5-flash-lite"
LLM_TIMEOUT = 30
LLM_TEMPERATURE = 0.1  # Для всех чат-вызовов; None — не передавать параметр (для моделей без temperature)
HISTORY_LIMIT = 30
""" 10–15 Строгие узкие ассистенты (только выполнить команду и дать ответ).
    20–30 Идеальный диапазон для Юмис. Естественный разговор + активный тулинг.
    50+ Оверкост, высокая задержка (latency) и риск путаницы в цепочках tool_calls."""
# USE_MEM = True


# Флаг переключения режимов (по умолчанию новый)
USE_RICH_MESSAGES = True


#### KEYS: ####

# `.env` уже прочитан в начале файла (см. load_dotenv() выше): от него зависит
# и PATH_SESSION. Здесь только разбор значений.
# `_env_int` определён в начале файла: им же читается DB_PORT.


# LOGGING (после load_dotenv, чтобы можно было переопределить из .env):
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
# DATABASE:
USER_DB = os.environ.get('USER_DB')
PASSWORD_DB = os.environ.get('PASSWORD_DB')
DB_NAME = os.environ.get('POSTGRES_DB')
# TELEGRAM:
TELEGRAM_BOT_TOKEN = os.environ.get('telegram_bot_token')
ADMIN_ID = _env_int('admin_id') or 0
API_ID = _env_int('api_id')
# str(os.environ.get(...)) превращал отсутствие ключа в строку "None"
API_HASH = (os.environ.get('api_hash') or '').strip() or None

