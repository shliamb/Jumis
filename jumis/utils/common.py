#! master/utils/common.py
import re

def extract_id_from_message(text: str):
    """ Извлечение bot id из формата: 
        [8660011450]: Сообщение боту! """
    match = re.match(r'\[(\d+)\]:', text)
    if match:
        return int(match.group(1))
    return None


def extract_text_after_id(text: str):
    """ Разделяем по ': ' и берём вторую часть """
    parts = text.split(']: ', 1)
    if len(parts) > 1:
        return parts[1]
    return text  # если нет ID, возвращаем как есть