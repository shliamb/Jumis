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



def sanitize_human_text(text: str) -> str:
    """
    Приводит текст к обычному 'человеческому' виду:
    - Заменяет длинные тире ('—', '–') на короткий дефис ('-')
    - Полностью удаляет эмодзи и иконки
    - Снимает Markdown-разметку (#, **, *, _)
    """
    if not text:
        return ""

    # 1. Замена длинных и средних тире на стандартный дефис
    text = text.replace("—", "-").replace("–", "-")

    # 2. Удаление ИИ-заголовков (символы # в начале строк)
    text = re.sub(r'^\s*#+\s*', '', text, flags=re.MULTILINE)

    # 3. Удаление всех типов Unicode-эмодзи
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\u2600-\u26FF"
        "\u2700-\u27BF"
        "]+", 
        flags=re.UNICODE
    )
    text = emoji_pattern.sub("", text)

    # 4. Удаление Markdown-тегов (#, **, *, _)
    text = re.sub(r'^\s*#+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,2}(.*?)\*{1,2}', r'\1', text)
    text = re.sub(r'_{1,2}(.*?)_{1,2}', r'\1', text)
    text = re.sub(r'`{1,3}(.*?)(`{1,3}|$)', r'\1', text)

    # 5. Нормализация пробелов
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


