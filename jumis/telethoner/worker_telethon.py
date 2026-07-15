# master/telethoner/mytelethon.py
import asyncio
from urllib.parse import urlparse
from telethon import TelegramClient, events, connection

from config import API_ID, API_HASH, USE_MTPROTO, ERR_PROXY_LIMIT
from proxy.mtprotoproxy import MTPROXY_STRINGS
from logs.set_logger import set_logger

logger = set_logger(name="telethon")




class myTelethon:
    def __init__(self):
        self.use_proxy = USE_MTPROTO
        self.proxy_strings = MTPROXY_STRINGS if self.use_proxy else []
        self.client = None
        self.current_proxy_index = -1   # индекс последнего успешного прокси
        self.error_limit = ERR_PROXY_LIMIT


    def _parse_proxy_str(self, proxy_str: str):
        """Преобразует строку вида mtproxy://host:port:secret в кортеж (host, port, secret)"""
        parts = proxy_str.replace('mtproxy://', '').split(':')
        host = parts[0]
        port = int(parts[1])
        secret = parts[2]   # hex-строка
        return (host, port, secret)


    def _connect_proxy(self, proxy_tuple=None):
        """
        Создаёт клиента Telethon с указанным прокси (или без).
        Регистрирует обработчик сообщений.
        """
        if proxy_tuple:
            host, port, secret = proxy_tuple
            self.client = TelegramClient(
                'session_jumis',
                API_ID,
                API_HASH,
                proxy=(host, port, secret),
                connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
                auto_reconnect=False   # отключаем встроенный reconnect
            )
        else:
            self.client = TelegramClient('session_jumis', API_ID, API_HASH)

        # """ Telethon вылавливает все мои сообщения в телеграмм и кидает в очередь """
        # @self.client.on(events.NewMessage(chats=GROUP_ID))
        # async def handler(event):
        #     sender = await event.get_sender()
        #     message_task = {
        #         'id': event.sender_id,
        #         'is_bot': getattr(sender, 'bot', False),
        #         'bot_message': event.raw_text
        #     }
        #     await workers_mess_queues.put(message_task)
        #     #print(f"📩 New message from group: [{event.sender_id}]: {event.raw_text[-16:]}..")


    async def _try_connect(self):
        """
        Перебирает прокси по кругу (начиная со следующего за последним успешным),
        пытается подключиться. При успехе сохраняет индекс и возвращает True.
        Если ни один не подошёл, возвращает False.
        """
        if not self.proxy_strings:
            # без прокси
            self._connect_proxy(None)
            try:
                await self.client.connect()
                print("✅ Connected without proxy")
                return True
            except Exception as e:
                print(f"❌ Connection without proxy failed: {e}")
                self.client = None
                return False

        # Начинаем со следующего индекса
        start = (self.current_proxy_index + 1) % len(self.proxy_strings)
        for i in range(len(self.proxy_strings)):
            idx = (start + i) % len(self.proxy_strings)
            proxy_str = self.proxy_strings[idx]
            proxy_tuple = self._parse_proxy_str(proxy_str)
            print(f"🔄 Trying MTProxy {proxy_tuple[0]}:{proxy_tuple[1]}...")
            self._connect_proxy(proxy_tuple)
            try:
                await self.client.connect()
                # Успешно – сохраняем индекс
                self.current_proxy_index = idx
                print(f"✅ Connected via MTProxy {proxy_tuple[0]}:{proxy_tuple[1]}")
                return True
            except Exception as e:
                print(f"❌ Failed: {e}")
                await self.client.disconnect()
                self.client = None
                continue

        print("❌ No working MTProxy found")
        return False


    async def run(self):
        """
        Основной цикл: пытается подключиться с ротацией прокси,
        затем запускает run_until_disconnected, переподключаясь при обрыве.
        """
        if not await self._try_connect():
            return

        consecutive_errors = 0
        while True:
            try:
                await self.client.start()
                print("✅ Telethon started, listening...")
                await self.client.run_until_disconnected()
            except Exception as e:
                print(f"⚠️ Telethon connection lost: {e}")
                consecutive_errors += 1
                if consecutive_errors > self.error_limit:
                    # Переключаем прокси
                    if not await self._try_connect():
                        print("❌ No working proxy, exiting")
                        break
                    consecutive_errors = 0
                await asyncio.sleep(1)


    # Тут будет другая логика совершеннно!!!!

    async def send_message(self, message_text: str, telegram_id: int = None, username: str = None):
            """Отправить сообщение клиенту от моего личного имени через Telethon"""

            if not message_text:
                return "Ошибка: текст сообщения пуст / Error: message text is empty"

            # Проверяем, инициализирован ли клиент и подключен ли он к серверам
            if not self.client or not self.client.is_connected():
                print("❌ Telethon client not connected.")
                return "❌ Ошибка: сессия Telethon не подключена. / Telethon client not connected."

            # Определяем приоритетную цель для отправки
            target = None
            clean_username = None

            if telegram_id:
                target = telegram_id
            elif username:
                clean_username = username.strip().replace("@", "")
                target = clean_username

            if not target:
                return "Ошибка: не указан ни ID, ни Юзернейм / Error: no ID or Username provided"

            try:
                # Telethon одинаково хорошо принимает в send_message как int(ID), так и str(username)
                await self.client.send_message(target, message_text)
                
                target_log = f"id: {telegram_id}" if telegram_id else f"@{clean_username}"
                #print(f"Сообщение успешно отправлено на {target_log}")
                
                # Если отправляли по юзернейму, то попутно вытягиваем его железный ID для базы данных
                if clean_username:
                    entity = await self.client.get_entity(clean_username)
                    return entity.id # Возвращаем числовой ID
                
                # Если изначально отправляли по ID, возвращаем его же
                return telegram_id
                
            except Exception as e:
                target_log = f"id: {telegram_id}" if telegram_id else f"@{clean_username}"
                error_msg = f"Не удалось отправить сообщение на {target_log}: {e}"
                print(error_msg)
                
                # Возвращаем понятную мастеру ошибку
                return f"❌ Ошибка отправки на {target_log}.\nВозможно, у вас нет открытого диалога с пользователем или он вас заблокировал."








































# # import asyncio
# import aiohttp
# from telethon import TelegramClient, events, connection
# import socks
# from config import GROUP_ID, API_ID, API_HASH, USE_PROXY
# from proxy.mtprotoproxy import MTPROXY_STRINGS
# from queue_task import workers_mess_queues
# from logs.set_logger import set_logger
# logger = set_logger(name="telethon")













# class myTelethon():
#     def __init__(self):
#         self.use_proxy = USE_PROXY
#         self.client = None
#         self.proxy_strings = MTPROXY_STRINGS

#         self._conect_poxy()
#         @self.client.on(events.NewMessage(chats=GROUP_ID))
#         async def handler(event):
#             """ Вылавливает сооббщения в группе и кладет в очередь """
#             sender = await event.get_sender()
#             # print('---')
#             # print('sender_id:', event.sender_id)
#             # print('is_bot:', getattr(sender, 'bot', False))
#             # print('text:', event.raw_text)

#             message_task = {'id': event.sender_id, 'is_bot': getattr(sender, 'bot', False), 'bot_message': event.raw_text}
#             await workers_mess_queues.put(message_task)
#             print(f"Sent a new message from the group to the queue: [{event.sender_id}]: {event.raw_text[:8]}..")


#     def _get_best_proxy(self):
#         """ """
#         return best_proxy



#     def _conect_poxy(self):
#         """"""
#         if self.use_proxy and self.proxy_strings:
#             proxy_str = self.proxy_strings[2]
#             parts = proxy_str.replace('mtproxy://', '').split(':')
#             host = parts[0]
#             port = int(parts[1])
#             secret_bytes = parts[2]
#             print(f"MTProxy: {host}:{port}, secret={secret_bytes[:8]}..")
#             self.client = TelegramClient(
#                 'session_name',
#                 API_ID,
#                 API_HASH,
#                 proxy=(host, port, secret_bytes),
#                 connection=connection.ConnectionTcpMTProxyRandomizedIntermediate
#             )
#         else:
#             # В идеальном мире, где ничего не блокируют!
#             self.client = TelegramClient('session_name', API_ID, API_HASH)


#     async def reconnect(self):
#         """"""
#         if self.bot:
#             await self.bot.session.close()  # закрыть сессию
#         await self.create_bot()  # пересоздаст с новым прокси


#     async def run(self):
#         """Проверка ip в самом Telethone, работает ли proxy"""
#         await self.client.start()
#         print("Telethon is running, I'm listening to messages...")
#         logger.info("Telethon is running, I'm listening to messages...")
#         await self.client.run_until_disconnected()


#     async def send_to_group(self, text_message: str):
#         """Отправить сообщение в группу от моего лица"""
#         await self.client.send_message(GROUP_ID, text_message)

















# Telethon — это официально допустимая технология Telegram API (MTProto), но:
# - если автоматизировать обычный пользовательский аккаунт, это уже зона риска;
# - не “незаконно”, но Telegram может ограничить/забанить аккаунт, если увидит подозрительную автоматизацию.

# ### Разница:
# - aiogram → Bot API → обычные боты
# - Telethon → MTProto → почти как обычный Telegram-клиент


# ### При первом запуске спросит:
# - номер телефона
# - код из Telegram
# - если есть — пароль 2FA

# После этого создаст файл сессии, и дальше логиниться заново обычно не надо.

# ---

# ## Да, его надо держать запущенным
# Да:
# - запустил процесс,
# - он висит,
# - ловит новые сообщения в группе,
# - нужные сообщения потом сам пихнешь в очередь.

# ---

# ## Важный момент
# Аккаунт, через который запускается Telethon, должен:
# - быть в этой группе
# - иметь доступ к сообщениям группы

# ---

# ## По рискам
# Что реально может быть проблемой:
# - спам
# - много аккаунтов
# - массовая автоматизация
# - подозрительная активность с новых IP
# - фарм / абуз / инвайты / рассылки

# Твой кейс “читать группу и реагировать” сам по себе не выглядит чем-то экстремальным.
