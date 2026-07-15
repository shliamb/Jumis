# master/llm/queue_keys.py
import asyncio
from llm.ds_keys import DS_KEYS


def create_key_queue() -> asyncio.Queue:
    """ Создаёт и заполняет очередь ключей DeepSeek. Возвращает готовую очередь. """
    key_queue = asyncio.Queue()
    for raw_key in DS_KEYS:
        key = raw_key.strip()
        if key:
            key_queue.put_nowait(key)

    if key_queue.empty():
        raise RuntimeError("Не найдено ни одного ключа DeepSeek")
    
    return key_queue
    

