from urllib.parse import urlparse





def parse_proxy(proxy: str) -> tuple:
    """Получаю отдельно элементы PROXY"""
    if not proxy: return
    parsed = urlparse(proxy)
    return parsed.hostname, int(parsed.port), parsed.username, parsed.password