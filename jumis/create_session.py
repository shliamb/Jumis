# jumi/create_session.py
from telethon import TelegramClient
from config import API_ID, API_HASH

# 1. Создаем объект клиента
client = TelegramClient('session_jumis', API_ID, API_HASH)

# 2. Она сама поймет, что сессии нет, подключится к Telegram 
# и попросит ввести телефон и код в терминале.
client.start()

print("Файл session_jumis.session успешно создан и авторизован.")
