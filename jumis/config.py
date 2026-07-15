import os
from dotenv import load_dotenv



#### BASIC CONFIG (set it up manually): ####

USE_PROXY = False
USE_MTPROTO = False
DOCKER = False
LOG_TO_FILE = False
HOST = "127.0.0.1" if DOCKER else  "localhost" # app_postgres localhost
PORT = 5432 if DOCKER else 15433  # В докере стучимся на внешний порт, локально — на стандартный
MAX_SIZE_DOC = 2 # 2 мегабайт
TIME_CORRECTION = + 3
TIME_ZONE = 'Europe/Moscow'
ERR_PROXY_LIMIT = 3



# #### FOLDERS: ####

PATH_LOGS = "/jumis/logs/files" if DOCKER else "jumis/logs/files"
# PATH_LOGS = "/logs/" if DOCKER else "../logs/"
DOWNLOAD = "/jumis/downloads/" if DOCKER else "jumis/downloads"
PATH_JSON = "/jumis/json/" if DOCKER else "jumis/json"
# OUTPUT = "/jumis/output/" if DOCKER else "../jumis/output/"
# SYST_CONT_FOLDER = "/" if DOCKER else "../"


#### LLMs: ####

# DeepSeek:
MODEL_DS = "deepseek-chat" #  deepseek-reasoner   deepseek-r1   deepseek-chat - V3   deepseek-vl deepseek-v4-pro deepseek-v4-flash
TIMEOUT = (5.0, 30.0)
HISTORY_LIMIT = 20
USE_MEM = True


#### KEYS: ####

load_dotenv()
# DATABASE:
USER_DB = os.environ.get('USER_DB')
PASSWORD_DB = os.environ.get('PASSWORD_DB')
DB_NAME = os.environ.get('POSTGRES_DB')
# TELEGRAM:
TELEGRAM_BOT_TOKEN = os.environ.get('telegram_bot_token')
ADMIN_ID = int(os.environ.get('admin_id'))
API_ID = int(os.environ.get('api_id'))
API_HASH = str(os.environ.get('api_hash'))




# # PROXY:
# if USE_PROXY: PROXY = os.environ.get('PROXY')
# if not USE_PROXY: PROXY = None

