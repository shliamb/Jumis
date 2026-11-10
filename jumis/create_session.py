# jumi/create_session.py
"""Разовый вход в Telegram из терминала (резервный путь).

Тот же файл сессии, что и у бота: `PATH_SESSION` из `config.py`. Раньше путь был
относительным, и запуск из другой папки создавал вторую копию сессии.
Пользоваться только при остановленном боте: два писателя в один файл Telethon
не переживёт.
"""
from telethon import TelegramClient
from config import API_HASH, API_ID, PATH_SESSION

# 1. Создаем объект клиента на канонический путь сессии
client = TelegramClient(PATH_SESSION, API_ID, API_HASH)

# 2. Она сама поймет, что сессии нет, подключится к Telegram
# и попросит ввести телефон и код в терминале.
client.start()

print(f"Файл {PATH_SESSION} успешно создан и авторизован.")
