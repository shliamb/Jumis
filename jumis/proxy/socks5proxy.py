#! proxy/socks5proxy.py
import os
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs

load_dotenv('.env.socks5proxy')

def parse_proxy_url(url: str):
    """ Парсер URL прокси """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    server = qs.get('server', [None])[0]
    port = qs.get('port', [None])[0]
    user = qs.get('user', [None])[0]
    password = qs.get('pass', [None])[0]
    if server and port and user and password:
        return f"socks5://{user}:{password}@{server}:{port}"
    return None

proxies_raw = os.environ.get("SOCKS5_PROXY_URLS", "").split(";")
SOCKS5PROXY_STRINGS = [p for p in (parse_proxy_url(u) for u in proxies_raw) if p]






