#! proxy/mtprotoproxy.py
import os
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs

load_dotenv('.env.mtproxy')

def parse_proxy_url(url: str):
    """ Парсер URL прокси """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    server = qs.get('server', [None])[0]
    port = qs.get('port', [None])[0]
    secret = qs.get('secret', [None])[0]
    if server and port and secret:
        return f"mtproxy://{server}:{port}:{secret}"
    return None

proxies_raw = os.environ.get("MTP_PROXY_URLS", "").split(";")
MTPROXY_STRINGS = [p for p in (parse_proxy_url(u) for u in proxies_raw) if p]



