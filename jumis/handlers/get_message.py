#! master/handlers/get_message.py
import asyncio
import json
import re
import time
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram import Router, types
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError, TelegramBadRequest
from config import ADMIN_ID
from logs.set_logger import set_logger
from stt_sense import stt
logger = set_logger(name="handlers")

router = Router()







def new_fix_streaming_html(text: str) -> str:
    """Очищает и закрывает HTML-теги для безопасного стриминга в Telegram"""
    # 1. Убираем "огрызки" тегов на самом конце строки (например, <, </, </p, <pr)
    # Они всё равно долетят полными в следующих чанках, а сейчас ломают парсер ТГ
    text = re.sub(r'</?[a-zA-Z]*$', '', text)
    
    # 2. Считаем и закрываем открытые теги
    for tag in ['pre', 'code', 'b', 'i', 'blockquote', 'tg-spoiler']:
        opened = text.count(f'<{tag}>')
        closed = text.count(f'</{tag}>')
        if opened > closed:
            text += f'</{tag}>'
            
    return text



# def new_fix_streaming_html(text: str) -> str:
#     """Очищает и закрывает HTML-теги для безопасного стриминга в Telegram"""
#     if not text:
#         return ""

#     # МИКРО-ЩИТ: Срабатывает только если в чанке вообще есть намек на теги
#     if '<' in text:
#         # Убиваем брейки
#         text = text.replace('<br/>', '\n').replace('<br>', '\n').replace('<br />', '\n')
#         # Списочные элементы превращаем в человеческие точки
#         text = text.replace('<li>', '• ').replace('</li>', '\n')
#         # Мусорные теги, которые ТГ не поддерживает, просто стираем в ноль
#         for bad_tag in ['<ul>', '</ul>', '<ol>', '</ol>', '<p>', '</p>', '<div>', '</div>']:
#             if bad_tag in text:
#                 text = text.replace(bad_tag, '')

#     # 1. Твой родной срез огрызков на конце строки
#     text = re.sub(r'</?[a-zA-Z]*$', '', text)
    
#     # 2. Твой родной счетчик открытых тегов
#     for tag in ['pre', 'code', 'b', 'i', 'blockquote', 'tg-spoiler']:
#         opened = text.count(f'<{tag}>')
#         closed = text.count(f'</{tag}>')
#         if opened > closed:
#             text += f'</{tag}>'
            
#     return text



# def new_fix_streaming_html(text: str) -> str:
#     """
#     Очищает, переводит Markdown в HTML, экранирует сырые операторы 
#     и закрывает теги для безопасного стриминга в Telegram.
#     """
#     if not text:
#         return ""

#     # 1. СТРАХОВКА ОТ СЫРЫХ ЗНАКОВ СРАВНЕНИЯ (< и >)
#     # Перечень всех валидных тегов Telegram, которые мы НЕ должны трогать
#     allowed_tags = r'(?:b|i|u|s|a|code|pre|blockquote|tg-spoiler)'
    
#     # Экранируем любой знак "<", если за ним НЕ идет разрешенный тег или его закрытие
#     # Например: "x < y" превратится в "x &lt; y", а "<b>" останется нетронутым
#     text = re.sub(r'<(?!\/?' + allowed_tags + r'\b)', '&lt;', text, flags=re.IGNORECASE)
    
#     # Символ ">" менее критичен, но одиночные операторы типа " > " лучше подстраховать
#     text = re.sub(r'\s>\s', ' &gt; ', text)

#     # 2. ИНТЕРПРЕТАЦИЯ MARKDOWN В HTML (С поддержкой стриминга)
#     # Сначала обрабатываем полные, закрытые пары
#     text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
#     text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
#     text = re.sub(r'__([^_]+)__', r'<u>\1</u>', text)
#     text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
#     text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)

#     # Ловим "зависшие" маркеры Markdown, которые ИИ начал писать, но еще не закрыл в чанке.
#     # Превращаем их в открывающие HTML-теги, а наш финальный цикл их корректно закроет!
#     if text.count('**') % 2 != 0:
#         text = re.sub(r'\*\*(?!.*?\*\*)', '<b>', text)  # Меняем последний непарный **
#     if text.count('*') % 2 != 0:
#         text = re.sub(r'\*(?!.*?\*)', '<i>', text)      # Меняем последний непарный *
#     if text.count('__') % 2 != 0:
#         text = re.sub(r'__(?!.*?__)', '<u>', text)      # Меняем последний непарный __
#     if text.count('_') % 2 != 0:
#         text = re.sub(r'_(?!.*?_)', '<i>', text)        # Меняем последний непарный _
#     if text.count('`') % 2 != 0:
#         text = re.sub(r'`(?!.*?`)', '<code>', text)    # Меняем последний непарный `

#     # 3. ТУПАЯ ЗАМЕНА ЗАПРЕЩЕННЫХ ВЕБ-ТЕГОВ (<ul>, <li>, <p>, <div>)
#     text = re.sub(r'</?(ul|ol)>', '\n', text, flags=re.IGNORECASE)
#     text = re.sub(r'<li[^>]*>', '• ', text, flags=re.IGNORECASE)
#     text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
#     text = re.sub(r'<(p|div)[^>]*>', '', text, flags=re.IGNORECASE)
#     text = re.sub(r'</(p|div)>', '\n', text, flags=re.IGNORECASE)
    
#     # Схлопываем три и более переноса строк в два
#     text = re.sub(r'\n{3,}', '\n\n', text)

#     # 4. СРЕЗАЕМ ОГРЫЗКИ НА САМОМ КОНЦЕ СТРИМА (твоя база: "<b", "</cod")
#     text = re.sub(r'</?[a-zA-Z]*$', '', text)
    
#     # 5. АВТО-ЗАКРЫТИЕ ВСЕХ ОТКРЫТЫХ ТЕГОВ (С учетом добавленных нами тегов)
#     for tag in ['pre', 'code', 'b', 'i', 'u', 's', 'blockquote', 'tg-spoiler']:
#         opened = len(re.findall(r'<' + tag + r'\b[^>]*>', text, flags=re.IGNORECASE))
#         closed = len(re.findall(r'</' + tag + r'>', text, flags=re.IGNORECASE))
#         if opened > closed:
#             text += f'</{tag}>'
            
#     return text




# def new_fix_streaming_html2(text: str) -> str:
#     """Очищает, переводит Markdown в HTML и закрывает теги на Си-скоростях"""
#     if not text:
#         return ""

#     # 1. ЗАМЕНА ВЕБ-ТЕГОВ (Через .replace — максимально быстро)
#     if any(tag in text for tag in ["<ul>", "<ol>", "<li>", "<p>", "<div>", "<br"]):
#         text = text.replace("<ul>", "").replace("</ul>", "\n")
#         text = text.replace("<ol>", "").replace("</ol>", "\n")
#         text = text.replace("<li>", "• ").replace("</li>", "\n")
#         text = text.replace("<p>", "").replace("</p>", "\n")
#         text = text.replace("<div>", "").replace("</div>", "\n")
#         # Выкашиваем любые варианты брейк-тегов в обычный перенос строки
#         text = text.replace("<br/>", "\n").replace("<br />", "\n").replace("<br>", "\n")

#     # 2. СВЕРХБЫСТРЫЙ ПАРСИНГ MARKDOWN ДЛЯ СТРИМА (Через .split)
#     if "**" in text:
#         parts = text.split("**")
#         new_parts = []
#         for i, part in enumerate(parts):
#             if i == len(parts) - 1 and len(parts) % 2 == 0:
#                 new_parts.append("<b>" + part)
#             elif i % 2 == 1:
#                 new_parts.append("<b>" + part + "</b>")
#             else:
#                 new_parts.append(part)
#         text = "".join(new_parts)

#     if "`" in text:
#         parts = text.split("`")
#         new_parts = []
#         for i, part in enumerate(parts):
#             if i == len(parts) - 1 and len(parts) % 2 == 0:
#                 new_parts.append("<code>" + part)
#             elif i % 2 == 1:
#                 new_parts.append("<code>" + part + "</code>")
#             else:
#                 new_parts.append(part)
#         text = "".join(new_parts)

#     # 3. ЭКРАНИРОВАНИЕ ОДИНОЧНЫХ ЗНАКОВ СРАВНЕНИЯ (Чтобы код типа "if x < y:" не рушил ТГ)
#     text = re.sub(r'<(?![a-zA-Z\/])', '&lt;', text)

#     # 4. СРЕЗ ОГРЫЗКОВ НА САМОМ КОНЦЕ СТРИМА
#     text = re.sub(r'</?[a-zA-Z]*$', '', text)
    
#     # 5. СЧЕТЧИК ЗАКРЫТИЯ ТЕГОВ
#     for tag in ['pre', 'code', 'b', 'i', 'u', 's', 'blockquote', 'tg-spoiler']:
#         opened = text.count('<blockquote') if tag == 'blockquote' else text.count(f'<{tag}>')
#         closed = text.count(f'</{tag}>')
        
#         if opened > closed:
#             text += f'</{tag}>'
            
#     return text





# CHECK RIGHTS USER TELEGRAM
async def rights_verification(user_id: int, lang: str, message: types.Message) -> bool:
    """ Проверка прав доступа """
    if message.chat.type != 'private':  # group - чтобы мастер-бот не обрабатывал сообщения из группы
        return False
    if user_id in {ADMIN_ID}:
        return True
    if lang == "ru": await message.answer("🔐 Вы не имеете доступа!")
    else: await message.answer("🔐 You don't have access")
    logger.error(f"This {user_id} shit made an attempt to enter to Admin Panel.")
    return False




@router.message()
async def handle_message(message: types.Message, bot: Bot, llm):
    user_id = message.from_user.id
    if not await rights_verification(user_id, message.from_user.language_code, message):
        return
    
    message_text = None

    # Обработка разных типов сообщений
    if message.content_type == "voice":
        file_id = message.voice.file_id
        
        file = None
        # Пробуем получить файл до 3 раз при сетевых сбоях
        for attempt in range(1, 4):
            try:
                file = await bot.get_file(file_id)
                break  # Если успешно — вылетаем из цикла ретраев
            except Exception as e:
                logger.warning(f"Сетевой сбой при get_file (попытка {attempt}/3): {e}")
                if attempt == 3:
                    await message.answer("❌ Проблемы со связью. Не удалось загрузить голосовое, попробуй еще раз.")
                    return
                await asyncio.sleep(1.5)  # Даем сети «отвиснуть» перед повтором

        # Точно так же страхуем само скачивание байт
        audio_bytes = None
        for attempt in range(1, 4):
            try:
                audio_bytes = await bot.download_file(file.file_path)
                break
            except Exception as e:
                logger.warning(f"Сетевой сбой при download_file (попытка {attempt}/3): {e}")
                if attempt == 3:
                    await message.answer("❌ Ошибка скачивания аудиофайла из-за сетевого сбоя.")
                    return
                await asyncio.sleep(1.5)

        message_text = await stt.transcribe(audio_bytes.read(), ".ogg")

        MAX_LEN = 4096
        prefix = "🎤 Распознано:\n"
        max_text_len = MAX_LEN - len(prefix) - 3  # запас на "..."
        trimmed = message_text[:max_text_len]
        if len(message_text) > max_text_len:
            trimmed += "..."
        await message.answer(f"{prefix}{trimmed}")

    elif message.content_type == "text":
        message_text = message.text

    elif message.content_type == "video_note":
        await message.answer("Видеосообщения пока не поддерживаются.")
        return
    
    elif message.content_type == "photo":
        await message.answer("Фото пока не обрабатываются.")
        return
    
    elif message.content_type == "document":
        await message.answer("Документы пока не принимаются.")
        return
    
    else:
        await message.answer("Этот тип сообщения не поддерживается.")
        return
        

    system, tools = await llm.get_tools("general_agent")
    msg = await message.answer("...")

    # Добавляем сообщение пользователя в историю диалога
    await llm.add_user_message(message_text)

    full_text = ""          # накопленный текст за всё время (для финального сообщения)
    buffer = ""
    last_update_time = 0    # Трекер времени последнего апдейта ТГ

    while True:
        tool_calls_info = []      # [{id, name, arguments}, ...]
        has_tool_calls = False
        stream_text = ""

        async for chunk in llm.refine_stream_tools(question=None, system=system, tools=tools):
            if chunk['type'] == 'text':
                stream_text += chunk['content']
                buffer += chunk['content']
                
                # Стримим в ТГ только если накопился текст И прошло больше 1.5 секунд с прошлого апдейта
                current_time = time.time()
                if len(buffer.strip()) > 5 and (current_time - last_update_time) >= 1.5:
                    
                    # Фиксим HTML перед отправкой в Telegram
                    text_to_send = new_fix_streaming_html(full_text + stream_text)
                    
                    try:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=msg.message_id,
                            text=text_to_send
                        )
                        last_update_time = current_time
                        buffer = ""
                    except TelegramRetryAfter as e:
                        # Если всё-таки превысили лимит — просто спим и пропускаем этот тик, не падая
                        logger.warning(f"Словили флуд внутри стрима. Спим {e.retry_after} сек.")
                        await asyncio.sleep(e.retry_after)
                        last_update_time = time.time()
                    except TelegramBadRequest as e:
                        # Если ТГ всё равно ругается на разметку (например, нейросеть внутри <pre> 
                        # написала что-то странное), мы просто пропускаем кадр.
                        # В консоль это больше СРАТЬ НЕ БУДЕТ.
                        if "can't parse entities" in str(e):
                            pass
                        else:
                            # А вот критические ошибки (например, юзер заблокировал бота) — пишем в лог
                            logger.error(f"Критическая ошибка ТГ при стриминге: {e}")
                    except TelegramAPIError as e:
                        logger.error(f"Общая ошибка ТГ при стриминге: {e}")

            elif chunk['type'] == 'tool':
                for tool_id, tool in chunk['data'].items():
                    tool_calls_info.append({
                        'id': tool_id,
                        'name': tool['name'],
                        'arguments': tool['arguments']
                    })
                    has_tool_calls = True

        # Если были вызовы функций
        if has_tool_calls:
            tool_calls_for_msg = []
            for tc in tool_calls_info:
                tool_calls_for_msg.append({
                    "id": tc['id'],
                    "type": "function",
                    "function": {
                        "name": tc['name'],
                        "arguments": tc['arguments']
                    }
                })
            await llm.add_assistant_message(content=stream_text, tool_calls=tool_calls_for_msg)
            full_text += stream_text + "\n"
            
            # Рендерим промежуточный статус перед вызовом тулзы
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=msg.message_id,
                    text=full_text + "🔧 Запускаю функцию..."
                )
                last_update_time = time.time()
            except Exception:
                pass

            # Выполняем функции
            for tc in tool_calls_info:
                args = json.loads(tc['arguments']) if isinstance(tc['arguments'], str) else tc['arguments']
                result = await llm.call_function(tc['name'], args)
                result_str = json.dumps(result, ensure_ascii=False) if result is not None else "ok"
                await llm.add_tool_response(tc['id'], result_str)

            llm._safe_trim()
            continue

        # Нет вызовов функций — финальный ответ
        else:
            await llm.add_assistant_message(content=stream_text)
            full_text += stream_text + "\n"
            
            # Финальный текст шлем обязательно, забивая на таймер
            try:
                await bot.edit_message_text(
                    full_text,
                    chat_id=message.chat.id,
                    message_id=msg.message_id
                )
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                # Переотправляем после сна
                try:
                    await bot.edit_message_text(full_text, chat_id=message.chat.id, message_id=msg.message_id)
                except Exception:
                    pass
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    raise
            break
