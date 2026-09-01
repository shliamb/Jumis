# jumis/jumis_agent/jumis_agent.py
import asyncio
import time
import json
import re
import html
import traceback
import markdown
from aiogram.enums import ParseMode
from html.parser import HTMLParser
from stt_sense import stt
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError, TelegramBadRequest, ClientDecodeError
from pydantic import ValidationError
from aiogram.methods import SendRichMessage
from aiogram.types import InputRichMessage
from config import ADMIN_ID, USE_RICH_MESSAGES
from logs.set_logger import set_logger
logger = set_logger(name="jumis_agent")



class TelegramHTMLCleaner(HTMLParser):
    """"""
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
















###############################
######### JUMIS AGENT #########
###############################

class JumisAgent:

    def __init__(self, bot, llm, queue_new_mess: asyncio.Queue):
        self.llm = llm
        self.bot = bot
        self.stt = stt
        self.queue_new_mess = queue_new_mess
        self.admin_id = ADMIN_ID
        self.use_rich_message = USE_RICH_MESSAGES

        # Словарь вида: {sender_id: {"username": str, "count": int, "last_text": str}}
        self.pending_peers = {}
        # ID сообщения-уведомления, которое мы будем редактировать в чате с Владельцем
        self.notif_msg_id = None


    @staticmethod
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




    @staticmethod
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



    @staticmethod
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
        self,
        chat_id: int, 
        message_id: int, 
        llm_response: str
    ):
        """
        Универсальный диспетчер отправки.
        Rich Messages отправляются одним куском (до 10k символов).
        Фолбэк режет текст на части по 4000 символов.
        """

        use_rich: bool = self.use_rich_message

        if not llm_response or not llm_response.strip():
            logger.warning("Попытка отправить пустое сообщение от LLM")
            return

        # =========================================================================
        # 1. РЕЖИМ RICH MESSAGES (без принудительного разбиения)
        # =========================================================================
        if use_rich:
            print("\nRICH MESSAGES\n")
            try:
                rich_html = self.markdown_to_rich_html(llm_response)

                # 1. Если сообщение укладывается в лимит Rich (до 10 000 символов)
                if len(rich_html) <= 10000:
                    try:
                        await self.bot(SendRichMessage(
                            chat_id=chat_id,
                            rich_message=InputRichMessage(html=rich_html)
                        ))
                    except (ValidationError, ClientDecodeError) as e:
                        # Сообщение физически доставлено пользователю.
                        # Pydantic упал только на валидации структуры JSON-ответа сервера.
                        logger.warning(f"RichMessage доставлен, но Pydantic споткнулся о response сервера: {e}")

                # 2. Если ответ длинный (>10k символов) — режем на куски
                else:
                    rich_chunks = self.split_html_text(rich_html, max_length=9000)
                    for r_chunk in rich_chunks:
                        try:
                            await self.bot(SendRichMessage(
                                chat_id=chat_id,
                                rich_message=InputRichMessage(html=r_chunk)
                            ))
                        except (ValidationError, ClientDecodeError) as e:
                            # То же самое для каждого отдельного куска
                            logger.warning(f"Rich-кусок доставлен, пропущена ошибка схемы Pydantic: {e}")

                # Удаляем заглушку "Юмис печатает..." после успешной отправки
                try:
                    await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception:
                    pass

                # ВАЖНО: Выходим из функции/метода, чтобы execution не ушел ниже в обычный fallback
                return

            except Exception as e:
                # Сюда попадем ТОЛЬКО при реальных сетевых сбоях, ошибках API Telegram или падении парсера
                logger.error(f"Реальная ошибка Rich Messages (fallback на обычный HTML): {e}", exc_info=True)

        # =========================================================================
        # 2. КЛАССИЧЕСКИЙ РЕНДЕР (HTML Fallback с разбиением по 4000 символов)
        # =========================================================================
        final_html = self.markdown_to_telegram_html(llm_response)
        chunks = self.split_html_text(final_html, max_length=4000)

        print("\nHTML Fallback\n")

        try:
            if chunks:
                # Редактируем заглушку первым куском
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=chunks[0],
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )

                # Досылаем остаток отдельными сообщениями
                for chunk in chunks[1:]:
                    await self.bot.send_message(
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

        await self.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=plain_chunks[0],
            parse_mode=None
        )
        for p_chunk in plain_chunks[1:]:
            await self.bot.send_message(
                chat_id=chat_id,
                text=p_chunk,
                parse_mode=None
            )


    # async def chec_notif_mess(self):
    #     """
    #     Формирует итоговый массив сообщений для LLM,
    #     внедряя фейковый вызов функции get_pending_queue, если есть неотвеченные.
    #     """
    #     messages = list(history)  # Копия существующей истории

    #     if self.pending_peers:
    #         # 1. Формируем структуру незакрытых чатов
    #         unread_info = []
    #         for peer_id, data in self.pending_peers.items():
    #             u_name = f"@{data['username']}" if data.get('username') and data['username'] != "Без юзернейма" else "без username"
    #             unread_info.append({
    #                 "peer_id": peer_id,
    #                 "username": u_name,
    #                 "unread_count": data.get("count", 1),
    #                 "last_text": data.get("last_text", "")
    #             })

    #         # 2. Имитация вызова функции моделью
    #         mock_call_id = "call_auto_pending_check"
    #         messages.append({
    #             "role": "assistant",
    #             "content": None,
    #             "tool_calls": [{
    #                 "id": mock_call_id,
    #                 "type": "function",
    #                 "function": {
    #                     "name": "get_pending_queue",
    #                     "arguments": "{}"
    #                 }
    #             }]
    #         })

    #         # 3. Ответ функции с флагом побуждения к напоминанию
    #         messages.append({
    #             "role": "tool",
    #             "tool_call_id": mock_call_id,
    #             "content": json.dumps({
    #                 "status": "success",
    #                 "unread_peers_count": len(self.pending_peers),
    #                 "unread_queue": unread_info,
    #                 "system_instruction": "Внимание! Есть неотвеченные диалоги. Кратко напомни Владельцу о них, если это уместно в контексте ответа."
    #             }, ensure_ascii=False)
    #         })



    async def check_notif_mess(self):
            """
            Проверяет очередь pending_peers и инжектит контекст неотвеченных сообщений 
            напрямую в self.llm.dialog через его стандартные методы.
            """
            if not self.pending_peers:
                return

            # 1. Собираем компактный список неотвеченных
            unread_info = []
            for peer_id, data in self.pending_peers.items():
                u_name = f"@{data['username']}" if data.get("username") and data["username"] != "Без юзернейма" else "без username"
                unread_info.append({
                    "peer_id": peer_id,
                    "username": u_name,
                    "unread_count": data.get("count", 1),
                    "last_text": data.get("last_text", "")
                })

            # 2. Уникальный ID для mock-вызова (чтобы API не ругался на дубли каллов)
            mock_call_id = f"call_auto_pending_{int(time.time())}"

            tool_calls_payload = [{
                "id": mock_call_id,
                "type": "function",
                "function": {
                    "name": "get_pending_queue",
                    "arguments": "{}"
                }
            }]

            tool_response_payload = json.dumps({
                "status": "success",
                "context_type": "OWNER_INBOX_NOTIFICATIONS",
                "unread_peers_count": len(self.pending_peers),
                "unread_queue": unread_info,
                "system_instruction": (
                    f"Talking ONLY to Owner (tg_id: {self.admin_id}). 'unread_queue' contains 3rd-party messages. "
                    "Briefly notify Owner if relevant. Do NOT run search tools or 'clear_inbox_notifs' without direct command."
                )
            }, ensure_ascii=False)

            # 3. Записываем в историю llm через твои родные методы
            await self.llm.add_assistant_message(content=None, tool_calls=tool_calls_payload)
            await self.llm.add_tool_response(tool_call_id=mock_call_id, content=tool_response_payload)



    async def process_agent_request(self, chat_id: int, prompt_text: str):
            """ УНИВЕРСАЛЬНОЕ ядро запуска Агента Jumis (из хэндлера или очереди) """
            msg = None
            try:
                system, tools = await self.llm.get_tools("jumis_agent")
                
                # Отправляем начальную плашку в чат
                msg = await self.bot.send_message(chat_id, "...")

                # 1. Проверяем и инжектим уведомление о неотвеченных (если они есть)
                await self.check_notif_mess()

                # 2. Теперь добавляем сам запрос пользователя
                await self.llm.add_user_message(prompt_text)

                full_text = ""          
                buffer = ""
                last_update_time = 0    

                while True:
                    tool_calls_info = []      
                    has_tool_calls = False
                    stream_text = ""

                    # 1. СТРИМИНГ ОТВЕТА ОТ LLM
                    async for chunk in self.llm.refine_stream_tools(question=None, system=system, tools=tools):
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
                                    await self.bot.edit_message_text(
                                        chat_id=chat_id,
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
                        await self.llm.add_assistant_message(content=stream_text, tool_calls=tool_calls_for_msg)
                        full_text += stream_text + "\n"
                        
                        try:
                            await self.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=msg.message_id,
                                text=full_text + "🔧 Запускаю функцию..."
                            )
                            last_update_time = time.time()
                        except Exception:
                            pass

                        for tc in tool_calls_info:
                            args = json.loads(tc['arguments']) if isinstance(tc['arguments'], str) else tc['arguments']
                            result = await self.llm.call_function(tc['name'], args)
                            result_str = json.dumps(result, ensure_ascii=False) if result is not None else "ok"
                            await self.llm.add_tool_response(tc['id'], result_str)

                        self.llm._safe_trim()
                        continue

                    # 3. ФИНАЛЬНЫЙ ОТВЕТ
                    else:
                        if not stream_text.strip():
                            logger.warning("LLM вернула пустой ответ. Применяем инъекцию в память.")
                            
                            fallback_context = "[System Note: The LLM API returned an empty string. The user was notified about the network/API glitch. Be ready to answer their previous question if they ask again.]"
                            await self.llm.add_assistant_message(content=fallback_context)
                            
                            user_alert = "⚠️ Нейросеть задумалась и не смогла выдать ответ из-за сбоя API. Я уже записал это в память, просто повтори вопрос или напиши 'продолжи'."
                            try:
                                await self.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=msg.message_id,
                                    text=user_alert
                                )
                            except Exception:
                                await self.bot.send_message(chat_id, user_alert)
                            
                            break

                        # Успешный ответ
                        await self.llm.add_assistant_message(content=stream_text)
                        full_text += stream_text
                        
                        await self.send_jumis_response(
                            chat_id=chat_id,
                            message_id=msg.message_id,
                            llm_response=full_text
                        )
                        break

            except Exception as e:
                logger.error(f"ФАТАЛЬНАЯ ОШИБКА в логике LLM: {e}", exc_info=True)

                try:
                    crash_note = f"[System Note: API connection crashed with error '{type(e).__name__}'. No response was generated.]"
                    await self.llm.add_assistant_message(content=crash_note)
                except Exception:
                    pass

                error_msg = (
                    "⚠️ **Произошла ошибка при генерации ответа.**\n\n"
                    f"**Тип:** `{type(e).__name__}`\n"
                    f"**Детали:** `{str(e)}`"
                )

                if msg:
                    try:
                        await self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg.message_id,
                            text=error_msg,
                            parse_mode="Markdown"
                        )
                    except Exception:
                        await self.bot.send_message(chat_id, error_msg, parse_mode="Markdown")
                else:
                    await self.bot.send_message(chat_id, error_msg, parse_mode="Markdown")


    async def send_jumis_mess_handler(self, message):
            """ Запуск Агента Jumis по входящему сообщению в телеграм бот """
            message_text = None

            if message.content_type == "voice":
                message_text = await self.stt.transcribe_telegram_voice(self.bot, message.voice.file_id)
                if not message_text:
                    await message.answer("❌ Проблемы со связью или ошибками скачивания. Попробуй ещё раз.")
                    return

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

            # Передаем управление в ядро
            await self.process_agent_request(
                chat_id=message.chat.id, 
                prompt_text=message_text
            )


    async def message_processing(self, task_data: dict):
        """ Логика работы виджета уведомлений (Плавающий дашборд):

            1. Новый собеседник: 
            Если пишет КТО-ТО НОВЫЙ — сносим старую плашку и отправляем новую в самый низ чата СО ЗВУКОМ (чтобы привлечь внимание).

            2. Обновление диалога: 
            Если приходят новые сообщения от ТЕХ ЖЕ людей или мы ИМ ОТВЕЧАЕМ — просто обновляем текст существующего сообщения на месте (без звука и без засорения чата).

            3. Все отвечены: 
            Как только очередь пустеет — полностью удаляем плашку из чата."""

        
        # 1. Приведение ID к единому типу int
        raw_sender = task_data.get("sender_id")
        raw_recipient = task_data.get("recipient_id")
        sender_id = int(raw_sender) if raw_sender is not None else None
        recipient_id = int(raw_recipient) if raw_recipient is not None else None

        direction = task_data.get("direction")
        username = task_data.get("username") or "Без юзернейма"
        content = task_data.get("content") or ""

        # Флаг: появился ли человек, которого ДО ЭТОГО не было в очереди
        is_new_peer = False

        # 2. Обновление локального словаря pending_peers
        if direction == "inbound_peer" and sender_id:
            if sender_id not in self.pending_peers:
                is_new_peer = True  # Это новый собеседник (например, "В")
            
            prev_count = self.pending_peers.get(sender_id, {}).get("count", 0)
            self.pending_peers[sender_id] = {
                "username": username,
                "count": prev_count + 1,
                "last_text": content
            }

        elif direction == "outbound_owner" and recipient_id:
            if recipient_id in self.pending_peers:
                del self.pending_peers[recipient_id]

        # -------------------------------------------------------------------
        # СЦЕНАРИЙ 4: 0 сообщений (всем ответил) -> delete_message
        # -------------------------------------------------------------------
        if not self.pending_peers:
            if self.notif_msg_id:
                try:
                    await self.bot.delete_message(chat_id=self.admin_id, message_id=self.notif_msg_id)
                except Exception as e:
                    logger.error(f"[JumisAgent] Ошибка удаления виджета: {e}")
                finally:
                    self.notif_msg_id = None
            return

        # 3. Верстка HTML-текста
        total_peers = len(self.pending_peers)
        total_messages = sum(peer["count"] for peer in self.pending_peers.values())

        lines = [f"📥 <b>Inbox:</b> <code>{total_peers}</code> • 💬 <code>{total_messages}</code>\n"]

        for peer_id, data in self.pending_peers.items():
            u_name = data.get("username", "Unknown")
            u_name_safe = html.escape(f"@{u_name}" if u_name != "Без юзернейма" else "Unknown User")
            count = data.get("count", 1)
            raw_text = data.get("last_text") or ""
            safe_text = html.escape(raw_text.strip()) if raw_text.strip() else "<i>(media / empty)</i>"
            
            if len(safe_text) > 180:
                safe_text = safe_text[:177] + "..."

            peer_card = (
                f"<a href=\"tg://user?id={peer_id}\"><b>{u_name_safe}</b></a> • 💬 <b>{count}</b>\n"
                f"<blockquote expandable>{safe_text}</blockquote>"
            )
            lines.append(peer_card)

        text_message = "\n\n".join(lines)

        # -------------------------------------------------------------------
        # СЦЕНАРИЙ 3: + 1 новое от В -> delete_message(id1) и отправка вниз
        # -------------------------------------------------------------------
        if is_new_peer and self.notif_msg_id:
            try:
                await self.bot.delete_message(chat_id=self.admin_id, message_id=self.notif_msg_id)
            except Exception as e:
                logger.info(f"[JumisAgent] Старый виджет не найден при сносе: {e}")
            finally:
                self.notif_msg_id = None

        # -------------------------------------------------------------------
        # СЦЕНАРИЙ 2: +/- 1 от А -> edit_message_text(id1)
        # -------------------------------------------------------------------
        if self.notif_msg_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=self.admin_id,
                    message_id=self.notif_msg_id,
                    text=text_message,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                return  # Успешно отредактировали старое сообщение, выходим
            except TelegramBadRequest as e:
                if "message is not modified" in str(e).lower():
                    return
                # Если сообщение было удалено вручную, сбрасываем ID и идем отправлять новое
                self.notif_msg_id = None
            except Exception as e:
                logger.error(f"[JumisAgent] Ошибка edit_message_text: {e}")
                self.notif_msg_id = None

        # -------------------------------------------------------------------
        # СЦЕНАРИЙ 1 & Пересоздание: send_message()
        # -------------------------------------------------------------------
        try:
            sent_msg = await self.bot.send_message(
                chat_id=self.admin_id,
                text=text_message,
                parse_mode="HTML",
                disable_web_page_preview=True,
                disable_notification=False if is_new_peer else True  # Звук только при создании/появлении нового пира
            )
            self.notif_msg_id = sent_msg.message_id
        except Exception as e:
            logger.error(f"[JumisAgent] Ошибка send_message: {e}", exc_info=True)
            self.notif_msg_id = None
  



    async def run_queue_worker(self):
        """ Внутренний цикл ожидания сообщений входящие + исходящие Телеграмм """
        logger.info("[JumisAgent] Запущен фоновый слушатель очереди сообщений")
        MAX_RETRIES = 3

        while True:
            try:
                # Ждем появления сообщения в очереди
                task_data = await self.queue_new_mess.get()
                msg_db_id = task_data.get("msg_db_id", "unknown")
                tg_msg_id = task_data.get("tg_msg_id")
                msg_type = task_data.get("msg_type", "msg")
                retries = task_data.get("retry_count", 0)

                # Формируем читаемый и уникальный ID для логов
                if msg_db_id:
                    msg_label = f"db:{msg_db_id}"
                elif tg_msg_id:
                    msg_label = f"tg:{tg_msg_id} [{msg_type}]"
                else:
                    msg_label = f"chat:{task_data.get('chat_id', 'unknown')}"
                
                logger.info(f"[JumisAgent Worker] Взял из очереди сообщение: {msg_label} (попытка {retries + 1})")
                                
                # Оборачиваем в таймаут 10 секунд
                await asyncio.wait_for(self.message_processing(task_data), timeout=10.0)

                logger.info(f"[JumisAgent Worker] Успешно обработал сообщение: {msg_label}")

            except asyncio.TimeoutError:
                retries = task_data.get("retry_count", 0) + 1
                task_data["retry_count"] = retries

                if retries < MAX_RETRIES:
                    logger.error(
                        f"⚠️ [JumisAgent Worker] ТАЙМАУТ на сообщении {msg_label}! "
                        f"Повторная попытка {retries}/{MAX_RETRIES} через 2 сек..."
                    )
                    await asyncio.sleep(2)
                    # Возвращаем таск обратно в очередь на повтор
                    await self.queue_new_mess.put(task_data)
                else:
                    logger.error(
                        f"💥 [JumisAgent Worker] ПРЕВЫШЕН ЛИМИТ ПОПЫТОК ({MAX_RETRIES})! "
                        f"Сообщение {msg_label} сброшено. Требуется ручная проверка."
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"💥 [JumisAgent Worker] Критическая ошибка обработки: {e}", exc_info=True)
                await asyncio.sleep(1)
            finally:
                self.queue_new_mess.task_done()



    async def clear_pending_queue(self) -> str:
        """
        Инструмент для Юмис: полностью очищает список неотвеченных
        и удаляет виджет-уведомление из чата Владельца.
        """
        try:
            peers_count = len(self.pending_peers)
            self.pending_peers.clear()

            # Удаляем виджет из Telegram, если он существует
            if self.notif_msg_id:
                try:
                    await self.bot.delete_message(
                        chat_id=self.admin_id,
                        message_id=self.notif_msg_id
                    )
                except Exception as e:
                    logger.warning(f"[JumisAgent] Не удалось удалить виджет при очистке: {e}")
                finally:
                    self.notif_msg_id = None

            if peers_count == 0:
                logger.info("[JumisAgent] Очередь неотвеченных сообщений и так была пуста.")
                return "Inbox queue was already empty. Dashboard widget reset."

            logger.info(f"[JumisAgent] Очередь очищена. Сброшено диалогов: {peers_count}. Уведомление удалено.")
            return f"Inbox queue successfully cleared. Dismissed dialogs count: {peers_count}."

        except Exception as e:
            logger.error(f"[JumisAgent] Ошибка при очистке очереди сообщений: {e}")
            return f"Failed to clear inbox queue due to error: {e}"


        
    async def get_notifs_queue(self) -> str:
        """Возвращает текущее состояние очереди неотвеченных сообщений."""
        if not self.pending_peers:
            return json.dumps({"status": "empty", "unread_peers_count": 0}, ensure_ascii=False)

        unread_info = []
        for peer_id, data in self.pending_peers.items():
            u_name = f"@{data['username']}" if data.get("username") and data["username"] != "Без юзернейма" else "без username"
            unread_info.append({
                "peer_id": peer_id,
                "username": u_name,
                "unread_count": data.get("count", 1),
                "last_text": data.get("last_text", "")
            })

        return json.dumps({
            "status": "success",
            "unread_peers_count": len(self.pending_peers),
            "unread_queue": unread_info
        }, ensure_ascii=False)



















    # async def message_processing(self, task_data: dict):
    #     """ Логика работы виджета уведомлений (Плавающий дашборд):

    #         1. Новый собеседник: 
    #         Если пишет КТО-ТО НОВЫЙ — сносим старую плашку и отправляем новую в самый низ чата СО ЗВУКОМ (чтобы привлечь внимание).

    #         2. Обновление диалога: 
    #         Если приходят новые сообщения от ТЕХ ЖЕ людей или мы ИМ ОТВЕЧАЕМ — просто обновляем текст существующего сообщения на месте (без звука и без засорения чата).

    #         3. Все отвечены: 
    #         Как только очередь пустеет — полностью удаляем плашку из чата."""
        
    #     sender_id = task_data.get("sender_id")
    #     recipient_id = task_data.get("recipient_id")
    #     username = task_data.get("username") or "Без юзернейма"
    #     content = task_data.get("content") or ""
    #     direction = task_data.get("direction")
    #     # chat_id = task_data.get("chat_id")
    #     # msg_type = task_data.get("msg_type")
    #     # created_at = task_data.get("created_at")
    #     # tg_msg_id = task_data.get("tg_msg_id")
    #     # msg_db_id = task_data.get("msg_db_id")


    #     # 1. Логика входящих сообщений
    #     if direction == "inbound_peer":
    #         # Безопасное чтение текущего счетчика
    #         prev_count = self.pending_peers.get(sender_id, {}).get("count", 0)
    #         self.pending_peers[sender_id] = {
    #             "username": username,
    #             "count": prev_count + 1,
    #             "last_text": content
    #         }

    #     # 2. Логика исходящих сообщений от Владельца
    #     elif direction == "outbound_owner":
    #         # Если мы написали человеку, которого НЕТ в списке ожидающих — ничего не делаем!
    #         if recipient_id not in self.pending_peers:
    #             return

    #         # Если он БЫЛ в очереди — удаляем его
    #         del self.pending_peers[recipient_id]

    #         # Если после этого очередь опустела — удаляем сообщение из Telegram и выходим
    #         if not self.pending_peers:
    #             if self.notif_msg_id:
    #                 try:
    #                     await self.bot.delete_message(chat_id=self.admin_id, message_id=self.notif_msg_id)
    #                 except TelegramBadRequest as e:
    #                     if "message to delete not found" not in str(e).lower():
    #                         logger.error(f"[JumisAgent] Ошибка удаления уведомления: {e}")
    #                 except Exception as e:
    #                     logger.error(f"[JumisAgent] Неизвестная ошибка удаления: {e}")
    #                 finally:
    #                     self.notif_msg_id = None
    #             return

    #     # Защитная проверка: если очередь пуста, выходим
    #     if not self.pending_peers:
    #         return

    #     # 3. Минималистичный Rich-текст (English & Ultra-Compact)
    #     total_peers = len(self.pending_peers)
    #     total_messages = sum(peer["count"] for peer in self.pending_peers.values())

    #     # Компактный заголовок
    #     lines = [
    #         f"📥 <b>Inbox:</b> <code>{total_peers}</code> • 💬 <code>{total_messages}</code>\n"
    #     ]

    #     for peer_id, data in self.pending_peers.items():
    #         u_name = data.get("username")
    #         u_name_display = f"@{u_name}" if u_name and u_name != "Без юзернейма" else "Unknown User"
    #         count = data.get("count", 1)
    #         raw_text = data.get("last_text") or ""

    #         # Экранируем спецсимволы (<, >, &), чтобы Telegram HTML не ломал разметку
    #         safe_text = html.escape(raw_text.strip()) if raw_text.strip() else "<i>(media / empty)</i>"
            
    #         # Ограничиваем длину превью, если сообщение слишком длинное
    #         if len(safe_text) > 180:
    #             safe_text = safe_text[:177] + "..."

    #         # Лаконичная карточка: кликабельный юзернейм + счетчик + цитата
    #         peer_card = (
    #             f"<a href=\"tg://user?id={peer_id}\"><b>{u_name_display}</b></a> • 💬 <b>{count}</b>\n"
    #             f"<blockquote expandable>{safe_text}</blockquote>"
    #         )
    #         lines.append(peer_card)

    #     text_message = "\n\n".join(lines)

    #     # 4. Отправляем новое или редактируем существующее
    #     try:
    #         if not self.notif_msg_id:
    #             # Создаем новое сообщение и запоминаем его ID
    #             sent_msg = await self.bot.send_message(
    #                 chat_id=self.admin_id,
    #                 text=text_message,
    #                 parse_mode="HTML",
    #                 disable_web_page_preview=True
    #             )
    #             self.notif_msg_id = sent_msg.message_id
    #         else:
    #             try:
    #                 await self.bot.edit_message_text(
    #                     chat_id=self.admin_id,
    #                     message_id=self.notif_msg_id,
    #                     text=text_message,
    #                     parse_mode="HTML",
    #                     disable_web_page_preview=True
    #                 )
    #             except TelegramBadRequest as e:
    #                 # ИГНОРИРУЕМ ОШИБКУ если текст точно такой же (например, пришло 2 одинаковых фото)
    #                 if "message is not modified" in str(e).lower():
    #                     pass
    #                 else:
    #                     raise e # Прокидываем в общий except

    #     except Exception as e:
    #         logger.error(f"[JumisAgent] Ошибка обновления дашборда сообщений: {e}")
    #         # Сбрасываем ID только при реальной ошибке, чтобы в следующий раз создать заново
    #         self.notif_msg_id = None