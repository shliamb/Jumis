#! jumis/bot_instance.py
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from urllib.parse import urlparse
from config import ERR_PROXY_LIMIT
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode





class AioBot:
    def __init__(self, use_proxy: bool, proxy_strings: list, bot_token: str):
        self.use_proxy = use_proxy
        self.proxy_strings = proxy_strings
        self.bot_token = bot_token
        self.bot = None
        self.session = None

        # Для ротации
        self.current_proxy_index = 0
        self.error_counts = [0] * len(proxy_strings) if proxy_strings else []
        self.error_limit = ERR_PROXY_LIMIT

        if self.proxy_strings:
            self.error_counts = [0] * len(self.proxy_strings)


    def _get_current_proxy(self) -> str | None:
        """Вернуть строку текущего прокси или None"""
        if not self.use_proxy or not self.proxy_strings:
            return None
        if self.current_proxy_index < 0:
            # начальное состояние – берём первый
            self.current_proxy_index = 0
        return self.proxy_strings[self.current_proxy_index]


    def _switch_to_next_proxy(self):
        """Переключиться на следующий прокси по кругу и сбросить его счётчик ошибок"""
        if not self.proxy_strings:
            return
        old_index = self.current_proxy_index
        self.current_proxy_index = (self.current_proxy_index + 1) % len(self.proxy_strings)
        self.error_counts[self.current_proxy_index] = 0
        proxy_str = self._get_current_proxy()
        proxy_ip = urlparse(proxy_str).hostname if proxy_str else None
        print(f"🔄 Переключился на следующий прокси: {proxy_ip}... (с {old_index} на {self.current_proxy_index})")


    def _record_error(self):
        """Увеличить счётчик ошибок текущего прокси, если превышен — переключиться"""
        if self.current_proxy_index < 0 or not self.proxy_strings:
            return
        self.error_counts[self.current_proxy_index] += 1
        print(f"❌ Ошибка прокси {self.current_proxy_index}, счётчик: {self.error_counts[self.current_proxy_index]}")
        if self.error_counts[self.current_proxy_index] >= self.error_limit:
            self._switch_to_next_proxy()


    def _reset_error_counter(self):
        """Сбросить счётчик ошибок текущего прокси (при успешном использовании)"""
        if self.current_proxy_index >= 0 and self.proxy_strings:
            self.error_counts[self.current_proxy_index] = 0
            print(f"✅ Счётчик ошибок прокси {self.current_proxy_index} сброшен")


    async def create_bot(self):
        proxy_str = self._get_current_proxy()
        try:
            new_session = AiohttpSession(proxy=proxy_str) if proxy_str else None
            self.bot = Bot(self.bot_token, session=new_session, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) # MARKDOWN_V2 * MARKDOWN * HTML
            self.session = new_session
            proxy_ip = urlparse(proxy_str).hostname if proxy_str else None
            print(f"Бот создан с прокси: {proxy_ip if proxy_str else 'без прокси'}")
            # Убираем сброс счётчика отсюда
        except Exception as e:
            print(f"Ошибка создания бота: {e}")
            self.bot = None
            self.session = None
            # Не переключаем здесь, полагаемся на reconnect
            raise


    async def reconnect(self):
        """Переподключение: увеличить счётчик ошибок текущего прокси, при необходимости сменить, затем создать бота заново"""
        self._record_error()
        if self.bot:
            await self.bot.session.close()
            self.bot = None
            self.session = None
        # Повторяем попытку с новым прокси (или с тем же, если лимит не превышен)
        await self.create_bot()


    async def close(self):
        if self.bot:
            await self.bot.session.close()
            self.bot = None
            self.session = None


    async def get_bot(self):
        return self.bot












































# def extract_ip_from_proxy(proxy_url: str) -> str:
#     """ Извлекает IP-адрес из строки прокси формата:
#         socks5://user:pass@host:port
#         Возвращает hostname (IP или домен). """
#     parsed = urlparse(proxy_url)
#     return parsed.hostname


# def format_ping_report(proxies_with_pings: list) -> str:
#     """ Форматирует список словарей с информацией о прокси и пинге в читаемый отчёт.
#         Каждый словарь должен содержать ключи 'proxy' (строка) и 'ping_ms' (число или None).
#         Возвращает строку с построчным перечислением IP и пинга. """
#     lines = []
#     for item in proxies_with_pings:
#         proxy_url = item.get('proxy')
#         ping = item.get('ping_ms')
#         if proxy_url and ping is not None:
#             ip = extract_ip_from_proxy(proxy_url)
#             lines.append(f"{ip}: {round(ping)} ms")
#         else:
#             # Если пинга нет, можно пропустить или отметить как недоступный
#             lines.append(f"{proxy_url}: недоступен")
#     return "\n".join(lines) + "\n" if lines else "Нет данных"


# async def test_socks5_proxy(proxy_string: str, bot_token: str, timeout: float = 5.0):
#     """ Проверяет SOCKS5 прокси, делая запрос getMe к Telegram API.
#         Возвращает (proxy_string, ping_ms) или (proxy_string, None) при ошибке. """
#     connector = ProxyConnector.from_url(proxy_string)
#     start = time.time()
#     try:
#         async with aiohttp.ClientSession(connector=connector) as session:
#             url = f"https://api.telegram.org/bot{bot_token}/getMe"
#             async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
#                 await resp.json()
#                 ping = (time.time() - start) * 1000
#                 return proxy_string, ping
#     except Exception:
#         return proxy_string, None
#     finally:
#         await connector.close()

# async def get_socks5_pings(proxy_list: list, bot_token: str, timeout: float = 5.0):
#     """ Для каждого SOCKS5 прокси из списка запускает тест и возвращает список словарей:
#         [{'proxy': 'socks5://...', 'ping_ms': 123.4}, ...] """
#     tasks = [test_socks5_proxy(proxy, bot_token, timeout) for proxy in proxy_list]
#     results = await asyncio.gather(*tasks)
#     output = []
#     for proxy, ping in results:
#         if ping is not None:
#             output.append({'proxy': proxy, 'ping_ms': round(ping, 2)})
#         else:
#             output.append({'proxy': proxy, 'ping_ms': None})
#     return output


# async def get_best_socks5_proxy(proxy_list: list, bot_token: str, timeout: float = 5.0):
#     """ Возвращает строку лучшего SOCKS5 прокси (с минимальным пингом) или 
#         None, если ни один не работает. """
#     if not proxy_list:
#         return None
#     pings = await get_socks5_pings(proxy_list, bot_token, timeout)
#     valid = [p for p in pings if p['ping_ms'] is not None]

#     verified_proxies = format_ping_report(valid)
#     print(f"Aiogram: Verified proxies:\n{verified_proxies}")

#     if not valid:
#         return None
#     best = min(valid, key=lambda x: x['ping_ms'])
#     print("🤖 Aiogram: Proxy selected:", extract_ip_from_proxy(best['proxy']))
#     return best['proxy']


# async def get_best_proxy():
#     best = await get_best_socks5_proxy(SOCKS5PROXY_STRINGS, TELEGRAM_BOT_TOKEN)
#     return best









# class AioBot:
#     def __init__(self, use_proxy: bool, proxy_strings: list, bot_token: str):
#         self.use_proxy = use_proxy
#         self.proxy_strings = proxy_strings
#         self.bot_token = bot_token
#         self.bot = None
#         self.session = None

#     async def _get_best_proxy(self) -> str | None:
#         """Возвращает лучший прокси из списка (асинхронно)."""
#         if not self.use_proxy or not self.proxy_strings:
#             print("The proxy is not used")
#             return None
#         # Здесь вызываем get_best_socks5_proxy (асинхронную) с переданным токеном
#         best = await get_best_socks5_proxy(self.proxy_strings, self.bot_token)
#         return best

#     async def create_bot(self):
#         try:
#             best_proxy = await self._get_best_proxy()
#             #print(f"DEBUG best_proxy = {best_proxy}")
#             self.session = AiohttpSession(proxy=best_proxy) if best_proxy else None
#             self.bot = Bot(self.bot_token, session=self.session, parse_mode=None)
#             #print(f"DEBUG bot created: {self.bot}")
#         except Exception as e:
#             print(f"DEBUG exception: {e}")
#             self.bot = None
#             self.session = None


#     async def reconnect(self):
#         """Закрывает старый бот, находит новый прокси, создаёт нового бота."""
#         if self.bot:
#             await self.bot.session.close()  # закрыть сессию
#         await self.create_bot()  # пересоздаст с новым прокси

#     async def close(self):
#         """Закрывает сессию бота."""
#         if self.bot:
#             await self.bot.session.close()

#     async def get_bot(self):
#         return self.bot























# if USE_PROXY and SOCKS5PROXY_STRINGS:
#     best_proxy = asyncio.run(get_best_proxy())
#     session = AiohttpSession(proxy=best_proxy) if best_proxy else None
# else:
#     session = None

# bot = Bot(TELEGRAM_BOT_TOKEN, session=session, parse_mode=None)



# class AioBot():
#     def __init__(self, USE_PROXY, SOCKS5PROXY_STRINGS, TELEGRAM_BOT_TOKEN):
#         self.bot = None
#         self.session = None
#         self.use_proxy = USE_PROXY
#         self.proxy = SOCKS5PROXY_STRINGS
#         self.bot_token = TELEGRAM_BOT_TOKEN


#     async def create_bot(self):
#         if self.use_proxy and self.proxy:
#             best_proxy = asyncio.run(get_best_proxy())
#             session = AiohttpSession(proxy=best_proxy) if best_proxy else None
#         else:
#             session = None

#         self.bot = Bot(TELEGRAM_BOT_TOKEN, session=session, parse_mode=None)










# def create_bot(proxy=None):
#     session = AiohttpSession(proxy=proxy) if proxy else None
#     return Bot(TELEGRAM_BOT_TOKEN, session=session, parse_mode=None)






# valid: [{'proxy': 'socks5://14a368e1f0fb8:3f48aeba51@45.149.100.135:11324', 'ping_ms': 1014.34}, {'proxy': 'socks5://14ad79a2e5a9f:31bba1f4c7@89.117.164.236:12324', 'ping_ms': 361.3}]



# async def test_socks5_proxy(proxy_string: str, bot_token: str, timeout: float = 5.0):
#     """
#     Проверяет SOCKS5 прокси, делая запрос getMe к Telegram API.
#     Возвращает (proxy_string, ping_ms) или (proxy_string, None) при ошибке.
#     """
#     # Создаём коннектор с прокси
#     connector = ProxyConnector.from_url(proxy_string)
#     start = time.time()
#     try:
#         async with aiohttp.ClientSession(connector=connector) as session:
#             url = f"https://api.telegram.org/bot{bot_token}/getMe"
#             async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
#                 await resp.json()
#                 ping = (time.time() - start) * 1000
#                 return proxy_string, ping
#     except Exception:
#         return proxy_string, None
#     finally:
#         await connector.close()


# async def get_socks5_pings(proxy_list, bot_token, timeout=5.0):
#     """
#     Для каждого SOCKS5 прокси из списка запускает тест и возвращает список словарей:
#     [{'proxy': 'socks5://...', 'ping_ms': 123.4}, ...]
#     """
#     tasks = [test_socks5_proxy(proxy, bot_token, timeout) for proxy in proxy_list]
#     results = await asyncio.gather(*tasks)
#     output = []
#     for proxy, ping in results:
#         if ping is not None:
#             output.append({'proxy': proxy, 'ping_ms': round(ping, 2)})
#         else:
#             output.append({'proxy': proxy, 'ping_ms': None})
#     return output

# async def getting_quality(proxy_list):
#     """Проверка каждого socks5 из списка и получение пинга в новый список."""
#     # Токен бота должен быть определён ранее
#     if not TELEGRAM_BOT_TOKEN:
#         print("Ошибка: TELEGRAM_BOT_TOKEN не задан в переменных окружения")
#         return
#     results = await get_socks5_pings(proxy_list, TELEGRAM_BOT_TOKEN)
#     print(results)
#     return results


# if __name__ == '__main__':
#     asyncio.run(getting_quality(SOCKS5PROXY_STRINGS))



















# session = AiohttpSession(proxy=PROXY) if PROXY else None

# bot = Bot(
#     TELEGRAM_BOT_TOKEN,
#     session=session,
#     parse_mode=None
# )




# async def get_public_ip(proxy_url: str = None) -> str:
#     """ Возвращаю IP с https://api.ipify.org"""

#     from aiohttp import ClientSession
#     from aiohttp_socks import ProxyConnector

#     connector = None
#     if proxy_url:
#         try:
#             connector = ProxyConnector.from_url(proxy_url)
#         except Exception as e:
#             print(f"⚠️ Proxy connector failed: {e}")
    
#     async with ClientSession(connector=connector) as session:
#         try:
#             async with session.get('https://api.ipify.org') as resp:
#                 ip = await resp.text()
#                 return ip
            
#         except Exception as e:
#             print(f"⚠️ Failed to get IP: {e}")
#             return "unknown"
        
        

# async def check_proxy():
#     """ Показываю IP соединения """
#     if PROXY:
#         ip = await get_public_ip(PROXY)
#         print(f"🤖 Bot IP through proxy: {ip}")
#         return ip
#     else:
#         ip = await get_public_ip()
#         print(f"🤖 Bot IP (direct): {ip}")
#         return ip







