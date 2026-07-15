#! master/llm/ds_keys.py
import os
from dotenv import load_dotenv

load_dotenv('.env.llm')
DS_KEYS = os.environ.get("deepseek_key", "").split(";")




