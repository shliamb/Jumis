#! master/handlers/get_message.py
import asyncio
import json
import html
from html.parser import HTMLParser
import traceback
import markdown
import re
import time
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram import Router, types
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError, TelegramBadRequest
from aiogram.methods import SendRichMessage
from aiogram.types import InputRichMessage
from config import ADMIN_ID
from logs.set_logger import set_logger
from stt_sense import stt
from handlers.common import rights_verification
logger = set_logger(name="handlers")

router = Router()



# Флаг переключения режимов (по умолчанию новый)
USE_RICH_MESSAGES = True





def markdown_to_rich_html(text: str) -> str:
    """
    Конвертер Markdown -> Telegram HTML.
    Поддерживает: H1-H3, таблицы, код, зачеркнутый текст ~~, спойлеры ||, списки и раскрывающиеся цитаты.
    """
    if not text or not text.strip():
        return ""

    # 1. Зачеркнутый текст: ~~текст~~ -> <s>текст</s>
    text = re.sub(r'~~(.*?)~~', r'<s>\1</s>', text)

    # 2. Телеграм-спойлеры: ||текст|| -> <tg-spoiler>текст</tg-spoiler>
    text = re.sub(r'\|\|(.*?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', text)

    # 3. Конвертация Markdown в HTML через встроенные расширения
    rich_html = markdown.markdown(
        text, 
        extensions=[
            'tables',        # Поддержка Markdown-таблиц (| col | col |)
            'fenced_code',    # Блоки кода ```python ... ```
            'sane_lists',     # Умные списки
            'nl2br'           # Сохранение одиночных переносов строк
        ]
    )

    # =========================================================================
    # ГЛАВНЫЙ ФИКС: Вычищаем вражеские теги <p> и </p>, которые ломают Telegram
    # =========================================================================
    rich_html = re.sub(r'</p>\s*<p>', '<br/><br/>', rich_html)  # Заменяем стыки абзацев на двойной перенос
    rich_html = re.sub(r'</?p>', '', rich_html)            # Удаляем оставшиеся <p> и </p>

    # 4. Делаем ВСЕ цитаты раскрывающимися
    rich_html = rich_html.replace('<blockquote>', '<blockquote expandable>')

    # Очищаем лишние переносы
    #rich_html = re.sub(r'\n{3,}', '\n\n', rich_html).strip()

    # print("\n=== ИТОГОВЫЙ RICH HTML ДЛЯ ТЕЛЕГРАМ ===\n", rich_html, "\n=======================================\n")

    return rich_html



async def send_jumis_response(
    bot: Bot, 
    chat_id: int, 
    message_id: int, 
    llm_response: str, 
    use_rich: bool = USE_RICH_MESSAGES
):
    """
    Универсальный диспетчер отправки.
    Rich Messages отправляются одним куском (до 10k символов).
    Фолбэк режет текст на части по 4000 символов.
    """
    if not llm_response or not llm_response.strip():
        logger.warning("Попытка отправить пустое сообщение от LLM")
        return

    # =========================================================================
    # 1. РЕЖИМ RICH MESSAGES (без принудительного разбиения)
    # =========================================================================
    if use_rich:
        print("\nRICH MESSAGES\n")
        try:
            rich_html = markdown_to_rich_html(llm_response)

            # Если сообщение укладывается в лимит Rich (до 10 000 символов) — отправляем целиком
            if len(rich_html) <= 10000:
                await bot(SendRichMessage(
                    chat_id=chat_id,
                    rich_message=InputRichMessage(html=rich_html)
                ))
            else:
                # Если ответ гигантский (>10k символов) — нарезаем с запасом по 9000
                rich_chunks = split_html_text(rich_html, max_length=9000)
                for r_chunk in rich_chunks:
                    await bot(SendRichMessage(
                        chat_id=chat_id,
                        rich_message=InputRichMessage(html=r_chunk)
                    ))

            # Удаляем заглушку "Юмис печатает..." после успешной отправки
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass

            return  # Успешно отправлено!

        except Exception as e:
            logger.warning(f"Rich Messages не сработали ({e}). Откатываемся на классический рендер...")

    # =========================================================================
    # 2. КЛАССИЧЕСКИЙ РЕНДЕР (HTML Fallback с разбиением по 4000 символов)
    # =========================================================================
    final_html = markdown_to_telegram_html(llm_response)
    chunks = split_html_text(final_html, max_length=4000)

    print("\nHTML Fallback\n")

    try:
        if chunks:
            # Редактируем заглушку первым куском
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=chunks[0],
                parse_mode="HTML",
                disable_web_page_preview=True
            )

            # Досылаем остаток отдельными сообщениями
            for chunk in chunks[1:]:
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
            return

    except TelegramBadRequest as e:
        logger.error(f"Ошибка классического парсера: {e}. Применяем чистый текст.")

    # =========================================================================
    # 3. АВАРИЙНЫЙ РЕЖИМ (Чистый текст без разметки)
    # =========================================================================
    clean_text = re.sub(r'<[^>]+>', '', final_html).strip() or llm_response.strip()
    
    plain_chunks = [clean_text[i:i+4000] for i in range(0, len(clean_text), 4000)] if clean_text else [llm_response[:4000]]

    print("\nEMERGENCY MODE\n")

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=plain_chunks[0],
        parse_mode=None
    )
    for p_chunk in plain_chunks[1:]:
        await bot.send_message(
            chat_id=chat_id,
            text=p_chunk,
            parse_mode=None
        )










def split_html_text(text: str, max_length: int = 4000) -> list[str]:
    """
    Разбивает длинный HTML-текст на куски не более max_length символов,
    не разрывая закрывающие и открывающие HTML-теги.
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Ищем безопасное место для разреза (перенос строки или пробел)
        split_at = text.rfind('\n', 0, max_length)
        if split_at == -1 or split_at < max_length // 2:
            split_at = text.rfind(' ', 0, max_length)

        if split_at == -1:
            split_at = max_length

        chunk = text[:split_at]
        text = text[split_at:].lstrip('\n')

        # Закрываем открытые теги в куске, чтобы Telegram не ругался
        # С помощью нашего же cleaner
        cleaner = TelegramHTMLCleaner()
        valid_chunk = cleaner.clean(chunk)
        chunks.append(valid_chunk)

    return chunks




class TelegramHTMLCleaner(HTMLParser):
    ALLOWED_TAGS = {
        'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 
        'tg-spoiler', 'a', 'code', 'pre', 'blockquote', 'ph',
        'table', 'thead', 'tbody', 'tr', 'th', 'td', 
        'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'p', 'br' 
    }

    def __init__(self):
        super().__init__()
        self.result = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == 'strong': tag = 'b'
        elif tag == 'em': tag = 'i'
        elif tag == 'ins': tag = 'u'
        elif tag in ('strike', 'del'): tag = 's'

        if tag in self.ALLOWED_TAGS:
            if tag == 'ph':
                attr_str = "".join([f' {k}="{html.escape(v)}"' for k, v in attrs])
                self.result.append(f'<ph{attr_str}/>')
                return

            attr_str = ""
            if tag == 'a':
                for k, v in attrs:
                    if k.lower() == 'href':
                        attr_str += f' href="{html.escape(v)}"'
            elif tag == 'blockquote':
                for k, v in attrs:
                    if k.lower() == 'expandable':
                        attr_str += ' expandable'

            self.result.append(f'<{tag}{attr_str}>')
            self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'ph':
            return
        if tag == 'strong': tag = 'b'
        elif tag == 'em': tag = 'i'
        elif tag == 'ins': tag = 'u'
        elif tag in ('strike', 'del'): tag = 's'

        if tag in self.stack:
            while self.stack:
                top = self.stack.pop()
                self.result.append(f'</{top}>')
                if top == tag:
                    break

    def handle_data(self, data):
        self.result.append(html.escape(data))

    def handle_entityref(self, name):
        try:
            char = html.unescape(f'&{name};')
            self.result.append(html.escape(char))
        except Exception:
            self.result.append(f'&{name};')

    def handle_charref(self, name):
        try:
            char = html.unescape(f'&#{name};')
            self.result.append(html.escape(char))
        except Exception:
            self.result.append(f'&#{name};')

    def clean(self, html_content: str) -> str:
        self.result = []
        self.stack = []
        self.feed(html_content)
        while self.stack:
            top = self.stack.pop()
            self.result.append(f'</{top}>')
        return "".join(self.result)


def markdown_to_telegram_html(text: str) -> str:
    if not text:
        return ""

    placeholders = {}
    ph_counter = 0

    def save_placeholder(content: str) -> str:
        nonlocal ph_counter
        key = f'<ph id="{ph_counter}"/>'
        placeholders[key] = content
        ph_counter += 1
        return key

    # 1. Сначала собираем Таблицы (ДО того как инлайн-код вставит свои теги ph)
    lines = text.split('\n')
    new_lines = []
    in_table = False
    table_lines = []

    for line in lines:
        stripped = line.strip()
        # Проверяем строки таблицы
        if '|' in line and (stripped.startswith('|') or stripped.endswith('|')):
            # Пропускаем разделительную черту типа |---|---|
            if re.match(r'^[\|\s\:\-]+$', stripped):
                continue
            in_table = True
            table_lines.append(line)
        else:
            if in_table:
                t_text = '\n'.join(table_lines)
                # Оборачиваем в <pre>, чтобы Telegram включил моноширинный режим и скролл
                new_lines.append(save_placeholder(f'<pre>{html.escape(t_text)}</pre>'))
                table_lines = []
                in_table = False
            new_lines.append(line)

    if in_table:
        t_text = '\n'.join(table_lines)
        new_lines.append(save_placeholder(f'<pre>{html.escape(t_text)}</pre>'))

    text = '\n'.join(new_lines)

    # 2. Изолируем блоки кода ```lang ... ```
    def code_block_sub(match):
        lang = match.group(1) or ""
        code = match.group(2) or ""
        code_escaped = html.escape(code.strip())
        if lang:
            tag = f'<pre><code class="language-{html.escape(lang)}">{code_escaped}</code></pre>'
        else:
            tag = f'<pre>{code_escaped}</pre>'
        return save_placeholder(tag)

    text = re.sub(r'```(\w+)?\n?(.*?)```', code_block_sub, text, flags=re.DOTALL)

    # 3. Изолируем инлайн-код `code`
    def inline_code_sub(match):
        code = match.group(1) or ""
        tag = f'<code>{html.escape(code)}</code>'
        return save_placeholder(tag)

    text = re.sub(r'`([^`\n]+)`', inline_code_sub, text)

    # 4. Заголовки # H1 -> <b>
    text = re.sub(r'^[ \t]*#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # 5. Разделители --- -> линия
    text = re.sub(r'^[ \t]*[-*_]{3,}[ \t]*$', '───────────────────', text, flags=re.MULTILINE)

    # 6. Основная разметка
    text = re.sub(r'\*\*\*(.*?)\*\*\*', r'<b><i>\1</i></b>', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\w)\*(.*?)\*(?!\w)', r'<i>\1</i>', text)
    text = re.sub(r'~~(.*?)~~', r'<s>\1</s>', text)
    text = re.sub(r'\|\|(.*?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)', r'<a href="\2">\1</a>', text)

    # 7. Раскрывающиеся цитаты >
    quote_lines = text.split('\n')
    res_lines = []
    q_block = []

    for line in quote_lines:
        if line.startswith('> ') or line == '>':
            q_block.append(line[2:] if line.startswith('> ') else '')
        else:
            if q_block:
                res_lines.append(f'<blockquote expandable>{"\n".join(q_block)}</blockquote>')
                q_block = []
            res_lines.append(line)
    if q_block:
        res_lines.append(f'<blockquote expandable>{"\n".join(q_block)}</blockquote>')

    text = '\n'.join(res_lines)

    # 8. Санитайзер и восстановление стека
    cleaner = TelegramHTMLCleaner()
    cleaned_text = cleaner.clean(text)

    # 9. Подставляем изолированные блоки обратно (в 2 прохода на случай вложенности)
    for _ in range(2):
        for key, val in placeholders.items():
            cleaned_text = cleaned_text.replace(key, val)

    return cleaned_text





@router.message()
async def handle_message(message: types.Message, bot: Bot, llm):
    user_id = message.from_user.id
    if not await rights_verification(user_id, message.from_user.language_code, message):
        return
    
    message_text = None

    if message.content_type == "voice":
        # Вызываем абстрактный метод класса:
        message_text = await stt.transcribe_telegram_voice(bot, message.voice.file_id)

        if not message_text:
            await message.answer(
                "❌ Проблемы со связью или ошибками скачивания. Попробуй ещё раз."
            )
            return

        # Готово! В message_text лежит распознанный текст
        # print(f"Расшифровка: {message_text}")

        MAX_LEN = 4096
        prefix = "🎤 Распознано:\n"
        max_text_len = MAX_LEN - len(prefix) - 3  
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



    # =========================================================================
    # БЛОК РАБОТЫ С LLM И ИНТЕРАКТИВОМ
    # =========================================================================
    msg = None
    try:
        system, tools = await llm.get_tools("jumis_agent")
        msg = await message.answer("...")

        # Добавляем сообщение пользователя в историю диалога
        await llm.add_user_message(message_text)

        full_text = ""          
        buffer = ""
        last_update_time = 0    

        while True:
            tool_calls_info = []      
            has_tool_calls = False
            stream_text = ""

            # 1. СТРИМИНГ ОТВЕТА ОТ LLM
            async for chunk in llm.refine_stream_tools(question=None, system=system, tools=tools):
                if chunk['type'] == 'text':
                    stream_text += chunk['content']
                    buffer += chunk['content']
                    
                    current_time = time.time()
                    current_text = full_text + stream_text
                    if len(buffer.strip()) > 5 and (current_time - last_update_time) >= 1.5:

                        if len(current_text) > 4000:
                            buffer = ""
                            continue

                        try:
                            await bot.edit_message_text(
                                chat_id=message.chat.id,
                                message_id=msg.message_id,
                                text=current_text,
                                parse_mode=None
                            )
                            last_update_time = current_time
                            buffer = ""
                        except TelegramRetryAfter as e:
                            logger.warning(f"Словили флуд внутри стрима. Спим {e.retry_after} сек.")
                            await asyncio.sleep(e.retry_after)
                            last_update_time = time.time()
                        except TelegramBadRequest as e:
                            if "can't parse entities" not in str(e):
                                logger.error(f"Ошибка ТГ при стриминге: {e}")
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

            # 2. ВЫЗОВ ФУНКЦИЙ (ТУЛЗОВ)
            if has_tool_calls:
                # Тут пустой stream_text — это НОРМАЛЬНО, модель молча вызвала функцию
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
                
                try:
                    await bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=msg.message_id,
                        text=full_text + "🔧 Запускаю функцию..."
                    )
                    last_update_time = time.time()
                except Exception:
                    pass

                for tc in tool_calls_info:
                    args = json.loads(tc['arguments']) if isinstance(tc['arguments'], str) else tc['arguments']
                    result = await llm.call_function(tc['name'], args)
                    result_str = json.dumps(result, ensure_ascii=False) if result is not None else "ok"
                    await llm.add_tool_response(tc['id'], result_str)

                llm._safe_trim()
                continue

            # 3. ФИНАЛЬНЫЙ ОТВЕТ
            else:
                # 🔥 ПЕРВАЯ ЛОВУШКА: Защита от пустого ответа без тулзов
                if not stream_text.strip():
                    logger.warning("LLM вернула пустой ответ. Применяем инъекцию в память.")
                    
                    # 1. Записываем в контекст пояснение для модели на английском (чтобы она не сошла с ума)
                    fallback_context = "[System Note: The LLM API returned an empty string. The user was notified about the network/API glitch. Be ready to answer their previous question if they ask again.]"
                    await llm.add_assistant_message(content=fallback_context)
                    
                    # 2. Уведомляем пользователя (Не шлем пустой ответ в Телеграм!)
                    user_alert = "⚠️ Нейросеть задумалась и не смогла выдать ответ из-за сбоя API. Я уже записал это в память, просто повтори вопрос или напиши 'продолжи'."
                    # Возможно позже стоит перевести на Eng или даже мульти лангв
                    try:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=msg.message_id,
                            text=user_alert
                        )
                    except Exception:
                        await message.answer(user_alert)
                    
                    break # Выходим из цикла, всё спасено

                # ✅ Если всё ок, стандартная логика
                await llm.add_assistant_message(content=stream_text)
                full_text += stream_text
                
                await send_jumis_response(
                    bot=bot,
                    chat_id=message.chat.id,
                    message_id=msg.message_id,
                    llm_response=full_text
                )
                break

    # =========================================================================
    # ГЛОБАЛЬНЫЙ СПАСАТЕЛЬНЫЙ КРУГ ДЛЯ ОШИБОК LLM / API / ФУНКЦИЙ
    # =========================================================================
    except Exception as e:
        logger.error(f"ФАТАЛЬНАЯ ОШИБКА в логике LLM: {e}")
        logger.error(traceback.format_exc())

        # 🔥 ВТОРАЯ ЛОВУШКА: Если API вообще крашнулось, закрываем вопрос пользователя заглушкой
        try:
            crash_note = f"[System Note: API connection crashed with error '{type(e).__name__}'. No response was generated.]"
            await llm.add_assistant_message(content=crash_note)
        except Exception:
            pass # Если и тут крашнется, ну и ладно

        error_msg = (
            "⚠️ **Произошла ошибка при генерации ответа.**\n\n"
            f"**Тип:** `{type(e).__name__}`\n"
            f"**Детали:** `{str(e)}`"
        )

        if msg:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=msg.message_id,
                    text=error_msg,
                    parse_mode="Markdown"
                )
            except Exception as edit_err:
                logger.error(f"Не удалось отредактировать заглушку ошибкой: {edit_err}")
                await message.answer(error_msg, parse_mode="Markdown")
        else:
            await message.answer(error_msg, parse_mode="Markdown")




















    # # =========================================================================
    # # БЛОК РАБОТЫ С LLM И ИНТЕРАКТИВОМ
    # # =========================================================================
    # msg = None
    # try:
    #     system, tools = await llm.get_tools("jumis_agent")
    #     msg = await message.answer("...")

    #     # Добавляем сообщение пользователя в историю диалога
    #     await llm.add_user_message(message_text)

    #     full_text = ""          
    #     buffer = ""
    #     last_update_time = 0    

    #     while True:
    #         tool_calls_info = []      
    #         has_tool_calls = False
    #         stream_text = ""

    #         # 1. СТРИМИНГ ОТВЕТА ОТ LLM
    #         async for chunk in llm.refine_stream_tools(question=None, system=system, tools=tools):
    #             if chunk['type'] == 'text':
    #                 stream_text += chunk['content']
    #                 buffer += chunk['content']
                    
    #                 current_time = time.time()
    #                 current_text = full_text + stream_text
    #                 if len(buffer.strip()) > 5 and (current_time - last_update_time) >= 1.5:

    #                     if len(current_text) > 4000:
    #                         buffer = ""
    #                         continue

    #                     # Внутренние "мягкие" ошибки Telegram при стриме ловим локально
    #                     try:
    #                         await bot.edit_message_text(
    #                             chat_id=message.chat.id,
    #                             message_id=msg.message_id,
    #                             text=current_text,
    #                             parse_mode=None
    #                         )
    #                         last_update_time = current_time
    #                         buffer = ""
    #                     except TelegramRetryAfter as e:
    #                         logger.warning(f"Словили флуд внутри стрима. Спим {e.retry_after} сек.")
    #                         await asyncio.sleep(e.retry_after)
    #                         last_update_time = time.time()
    #                     except TelegramBadRequest as e:
    #                         if "can't parse entities" in str(e):
    #                             pass
    #                         else:
    #                             logger.error(f"Ошибка ТГ при стриминге: {e}")
    #                     except TelegramAPIError as e:
    #                         logger.error(f"Общая ошибка ТГ при стриминге: {e}")

    #             elif chunk['type'] == 'tool':
    #                 for tool_id, tool in chunk['data'].items():
    #                     tool_calls_info.append({
    #                         'id': tool_id,
    #                         'name': tool['name'],
    #                         'arguments': tool['arguments']
    #                     })
    #                     has_tool_calls = True

    #         # 2. ВЫЗОВ ФУНКЦИЙ (ТУЛЗОВ)
    #         if has_tool_calls:
    #             tool_calls_for_msg = []
    #             for tc in tool_calls_info:
    #                 tool_calls_for_msg.append({
    #                     "id": tc['id'],
    #                     "type": "function",
    #                     "function": {
    #                         "name": tc['name'],
    #                         "arguments": tc['arguments']
    #                     }
    #                 })
    #             await llm.add_assistant_message(content=stream_text, tool_calls=tool_calls_for_msg)
    #             full_text += stream_text + "\n"
                
    #             try:
    #                 await bot.edit_message_text(
    #                     chat_id=message.chat.id,
    #                     message_id=msg.message_id,
    #                     text=full_text + "🔧 Запускаю функцию..."
    #                 )
    #                 last_update_time = time.time()
    #             except Exception:
    #                 pass

    #             for tc in tool_calls_info:
    #                 args = json.loads(tc['arguments']) if isinstance(tc['arguments'], str) else tc['arguments']
    #                 result = await llm.call_function(tc['name'], args)
    #                 result_str = json.dumps(result, ensure_ascii=False) if result is not None else "ok"
    #                 await llm.add_tool_response(tc['id'], result_str)

    #             llm._safe_trim()
    #             continue

    #         # 3. ФИНАЛЬНЫЙ ОТВЕТ
    #         else:
    #             await llm.add_assistant_message(content=stream_text)
    #             full_text += stream_text
                
    #             await send_jumis_response(
    #                 bot=bot,
    #                 chat_id=message.chat.id,
    #                 message_id=msg.message_id,
    #                 llm_response=full_text
    #             )
    #             break

    # # =========================================================================
    # # ГЛОБАЛЬНЫЙ СПАСАТЕЛЬНЫЙ КРУГ ДЛЯ ОШИБОК LLM / API / ФУНКЦИЙ
    # # =========================================================================
    # except Exception as e:
    #     logger.error(f"ФАТАЛЬНАЯ ОШИБКА в логике LLM: {e}")
    #     logger.error(traceback.format_exc())

    #     error_msg = (
    #         "⚠️ **Произошла ошибка при генерации ответа.**\n\n"
    #         f"**Тип:** `{type(e).__name__}`\n"
    #         f"**Детали:** `{str(e)}`"
    #     )

    #     # Если сообщение "..." уже создано — меняем его текст на ошибку
    #     if msg:
    #         try:
    #             await bot.edit_message_text(
    #                 chat_id=message.chat.id,
    #                 message_id=msg.message_id,
    #                 text=error_msg,
    #                 parse_mode="Markdown"
    #             )
    #         except Exception as edit_err:
    #             logger.error(f"Не удалось отредактировать заглушку ошибкой: {edit_err}")
    #             await message.answer(error_msg, parse_mode="Markdown")
    #     else:
    #         # Если упало еще до создания заглушки
    #         await message.answer(error_msg, parse_mode="Markdown")


