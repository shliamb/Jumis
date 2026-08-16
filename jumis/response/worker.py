# response/worker.py
import asyncio
import time
from config import ADMIN_ID, BUFFER_IDLE_SEC
from logs.set_logger import set_logger

logger = set_logger(name="response")




class ResponseWorker:
    def __init__(self, bot, queue_response: asyncio.Queue, telethon_client, llm):
        self.bot = bot
        self.llm = llm
        self.queue = queue_response
        self.telethon_client = telethon_client
        self.admin_id = ADMIN_ID
        self.buffer_idle_sec = BUFFER_IDLE_SEC
        self._is_running = False
        self.active_chats = {}  # {tg_id: {"quantity": int, "last_time": float}}
        self._cycle_task = None  # Ссылка на фоновую таску таймера


    async def run(self):
        self._is_running = True
        logger.info("[ResponseWorker] Воркер обработки ответов запущен.")

        # Запускаем фоновый цикл проверки таймаутов
        self._cycle_task = asyncio.create_task(self._message_cycle())

        while self._is_running:
            try:
                # Ждем следующую задачу из очереди
                task = await self.queue.get()
                await self._add_mess_circle(task)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ResponseWorker] Ошибка обработки задачи: {e}", exc_info=True)
            finally:
                self.queue.task_done()

        # Отменяем фоновый таймер при остановке воркера
        if self._cycle_task and not self._cycle_task.done():
            self._cycle_task.cancel()


    async def _add_mess_circle(self, task: dict):
        """Добавляет или обновляет время и счетчик сообщений для пользователя."""
        tg_id = task.get("tg_id")
        is_favourites = task.get("is_favourites")
        msg_type = task.get("msg_type")
        content = task.get("content")

        if not tg_id:
            return

        # 1. Мгновенная обработка Избранного (без попадения в 30-сек очередь)
        if is_favourites:
            if msg_type == "voice":
                preview = f"{content[:30]}..." if content and len(content) > 30 else content
                logger.info(f"[ResponseWorker] Голосовое из Избранного: {preview}")
                # Отправляем расшифрованный текст обратно в Избранное
                await self.telethon_client.send_message(message_text=content, telegram_id=task["chat_id"], username=None)
            return  # В 30-секундный таймер Избранное не пускаем

        # 2. Пропускаем мои личные сообщения, на мои сообщения отвечать не нужно)
        if tg_id == self.admin_id:
            return

        current_time = time.time()  # Фиксируем локальный timestamp (float)

        # Проверяем наличие ключа безопасно
        if tg_id not in self.active_chats:
            self.active_chats[tg_id] = {"quantity": 1, "last_time": current_time}
        else:
            self.active_chats[tg_id]["quantity"] += 1
            self.active_chats[tg_id]["last_time"] = current_time


    async def _message_cycle(self):
        """Фоновый бесконечный цикл: проверяет таймауты молчания каждые 2 секунды."""
        while self._is_running:
            try:
                now = time.time()

                # Итерируемся по КОПИИ списка items(),
                # чтобы безопасно удалять ключи из словаря прямо в цикле
                for tg_id, info in list(self.active_chats.items()):
                    # Если пользователь молчит больше 30 секунд
                    if (now - info["last_time"]) > self.buffer_idle_sec:
                        logger.info(f"[ResponseWorker] Таймаут {self.buffer_idle_sec}с вышел для tg_id={tg_id}. Передаем субагенту.")

                        # Отправляем субагенту
                        await self._send_mess_to_subagent(tg_id=tg_id, info=info)

                        # Правильное удаление элемента из словаря
                        del self.active_chats[tg_id]

            except Exception as e:
                logger.error(f"[ResponseWorker] Ошибка в фоновом цикле таймера: {e}", exc_info=True)

            # Проверяем чаты раз в 2 секунды
            await asyncio.sleep(2)



    async def _send_mess_to_subagent(self, tg_id: int, info: dict):
        """Передача задачи Субагенту (заглушка)"""
        logger.info(f"[ResponseWorker] Вызов субагента для tg_id={tg_id} (сообщений: {info['quantity']})")

        system, tools = await self.llm.get_tools("tg_inbound_agent")
        # !!!!!!

        














    # async def _run_agent_loop(self):
    #     pass

    # async def _execute_agent_loop(self, messages: list, depth: int = 0) -> str:
    #     if depth > 5:  # Защита от бесконечной рекурсии
    #         return "Превышен лимит вызовов инструментов."

    #     # Вызов LLM через твой модуль llm
    #     response = await llm_client.completion(messages=messages, tools=AVAILABLE_TOOLS)
        
    #     # Если LLM хочет вызвать функцию (tool_call)
    #     if response.tool_calls:
    #         tool_results = await self._call_tools(response.tool_calls)
    #         messages.append(response.message) # Сохраняем вызов
    #         messages.extend(tool_results)    # Добавляем результаты работы функций
            
    #         # Рекурсивный запуск на следующий круг
    #         return await self._execute_agent_loop(messages, depth + 1)
            
    #     return response.content



# async def _send_mess_to_subagent(self, tg_id: int, info: dict):
#     # 1. Достаем историю сообщений из БД для этого tg_id
#     messages = await self.db.get_history(tg_id)
    
#     # 2. Запускаем рекурсивный / итеративный цикл вызова функций
#     final_text = await self._execute_agent_loop(messages)
    
#     # 3. Отправляем ответ пользователю
#     await self.bot.send_message(tg_id, final_text)












# from aiogram.methods import SendRichMessage
# from aiogram.types import InputRichMessage
# from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton



    # async def _collecting_messages(self, task: dict):
    #         """ ... """
    #         user_tg_id = task.get("tg_id")
    #         msg_db_id = task.get("msg_db_id")
    #         tg_msg_id = task.get("tg_msg_id")
    #         username = task.get("username")
    #         content = task.get("content")
    #         created_at = task.get("created_at")

    #         if user_tg_id == self.admin_id:
    #             return



    #         await asyncio.sleep(30)



            # # Создаем Inline-кнопку "Ответить" с tg_id юзера в callback_data
            # kb = InlineKeyboardMarkup(inline_keyboard=[[
            #     InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_to:{user_tg_id}")
            # ]])

            # text = f"📩 <b>Новое сообщение!</b><br/>От: @{username}<br/>Время: {created_at}<br/>---<br/>Текст:<br/><blockquote expandable>{content}</blockquote>"
            
            # # # Шлем напрямую админу через Aiogram Bot
            # # await self.bot.send_message(
            # #     chat_id=self.admin_id, 
            # #     text=text, 
            # #     parse_mode="Markdown",
            # #     reply_markup=kb
            # # )

            # await self.bot(SendRichMessage(
            #     chat_id=self.admin_id, 
            #     rich_message=InputRichMessage(html=text),
            #     reply_markup=kb
            # ))


    # async def _handle_response_task(self, task: dict):
    #     tg_id = task["tg_id"]
    #     content = task["content"]

    #     print(f"[ResponseWorker] Обработка запроса от tg_id={tg_id}: '{content[:30]}...'")
    #     logger.info(f"[ResponseWorker] Обработка запроса от tg_id={tg_id}: '{content[:30]}...'")

    #     # Твоя бизнес-логика ответов:
    #     # 1. Сгенерировать ответ через ИИ / отправить уведомление в бота
    #     # 2. Отправить ответ пользователю через Telethon
