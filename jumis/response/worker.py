# # jumis/response/worker.py
# import asyncio
# import time
# import json
# from config import ADMIN_ID, BUFFER_IDLE_SEC
# from logs.set_logger import set_logger

# logger = set_logger(name="response")




# class ResponseWorker:
    
#     def __init__(
#             self, 
#             bot, 
#             queue_response: asyncio.Queue, 
#             telethon_client, 
#             queue_req_jum: asyncio.Queue, 
#             llm
#         ):
#         self.bot = bot
#         self.llm = llm
#         self.queue_response = queue_response
#         self.queue_req_jum = queue_req_jum
#         self.telethon_client = telethon_client
#         self.admin_id = ADMIN_ID
#         self.buffer_idle_sec = BUFFER_IDLE_SEC
#         self._is_running = False
#         self.active_chats = {}  # {tg_id: {"quantity": int, "last_time": float}}
#         self._cycle_task = None  # Ссылка на фоновую таску таймера
#         self.active_subagent_tasks = {}  # dict[int, asyncio.Task]


    # async def run(self):
    #     self._is_running = True
    #     logger.info("[ResponseWorker] Воркер обработки ответов запущен.")

    #     # # Запускаем фоновый цикл проверки таймаутов
    #     # self._cycle_task = asyncio.create_task(self._message_cycle())

    #     while self._is_running:
    #         try:
    #             # Ждем следующую задачу из очереди
    #             task = await self.queue_response.get()
    #         except asyncio.CancelledError:
    #             break

    #         # Выполняем обработку и гарантируем task_done() только для полученного элемента
    #         try:
    #             await self._add_mess_circle(task)
    #         except Exception as e:
    #             logger.error(f"[ResponseWorker] Ошибка обработки задачи: {e}", exc_info=True)
    #         finally:
    #             self.queue_response.task_done()

    #     # Отменяем фоновый таймер при остановке воркера
    #     if self._cycle_task and not self._cycle_task.done():
    #         self._cycle_task.cancel()


    # async def on_owner_reply(self, peer_id: int):
    #     """Очищает буфер и отменяет работу субагента, если владелец ответил вручную."""
    #     # 1. Если тикал таймер буфера — сбрасываем его
    #     if peer_id in self.active_chats:
    #         del self.active_chats[peer_id]
    #         logger.info(f"🧹 [ResponseWorker] Буфер для peer_id={peer_id} очищен (владелец ответил сам).")

    #     # 2. Если субагент УЖЕ запущен — отменяем таску через safe .pop()
    #     subagent_task = self.active_subagent_tasks.pop(peer_id, None)
    #     if subagent_task and not subagent_task.done():
    #         subagent_task.cancel()
    #         logger.info(f"🛑 [ResponseWorker] Таска субагента для peer_id={peer_id} отменена.")


    # async def _add_mess_circle(self, task: dict):
    #     """ Вылавливаем из очереди queue_response (входящие + исходящие + избранное)"""
    #     chat_id = task.get("chat_id")
    #     sender_id = task.get("sender_id")
    #     recipient_id = task.get("recipient_id")

    #     tg_msg_id = task.get("tg_msg_id")
    #     msg_db_id = task.get("msg_db_id")
    #     is_favourites = task.get("is_favourites")

    #     msg_type = task.get("msg_type")
    #     content = task.get("content")
    #     direction = task.get("direction")

    #     if not tg_id:
    #         return

    #     # 1. Извлекаем ID собеседника (peer)
    #     peer_id = chat_id if role == "out_owner" else tg_id

    #     # 2. VOICE + FAVORITES MESSAGES 
    #     if is_favourites:
    #         if msg_type == "voice":
    #             preview = f"{content[:30]}..." if content and len(content) > 30 else content
    #             logger.info(f"[ResponseWorker] Голосовое из Избранного: {preview}")
    #             # Отправляем расшифрованный текст обратно в Избранное
    #             await self.telethon_client.send_message(message_text=content, telegram_id=task["chat_id"], username=None)
    #         return  # В 30-секундный таймер Избранное не пускаем

    #     # 3. MY OUT MESSAGE
    #     if role == "out_owner":
    #         await self.on_owner_reply(peer_id)
    #         return

    #     # 4. IN MESSAGE
    #     if role == "in_peer":
    #         current_time = time.time()

    #         if peer_id not in self.active_chats:
    #             self.active_chats[peer_id] = {"quantity": 1, "last_time": current_time}
    #         else:
    #             self.active_chats[peer_id]["quantity"] += 1
    #             self.active_chats[peer_id]["last_time"] = current_time



    # async def _message_cycle(self):
    #     """Фоновый бесконечный цикл: проверяет таймауты молчания каждые 2 секунды."""
    #     while self._is_running:
    #         try:
    #             now = time.time()

    #             for peer_id, info in list(self.active_chats.items()):
    #                 # Если пользователь молчит больше buffer_idle_sec секунд
    #                 if (now - info["last_time"]) > self.buffer_idle_sec:
    #                     logger.info(f"[ResponseWorker] Таймаут {self.buffer_idle_sec}с вышел для peer_id={peer_id}. Передаем субагенту.")

    #                     # Запускаем фоновую задачу для субагента
    #                     sub_task = asyncio.create_task(
    #                         self._run_tg_inbound_agent(tg_id=peer_id, info=info)
    #                     )

    #                     # Сохраняем задачу, чтобы иметь возможность отменить
    #                     self.active_subagent_tasks[peer_id] = sub_task

    #                     # Удаляем из буфера
    #                     del self.active_chats[peer_id]

    #         except Exception as e:
    #             logger.error(f"[ResponseWorker] Ошибка в фоновом цикле таймера: {e}", exc_info=True)

    #         # Проверяем чаты раз в 2 секунды
    #         await asyncio.sleep(2)



    # async def _run_tg_inbound_agent(self, tg_id: int, info: dict):
    #     """Передача задачи Субагенту с асинхронным циклом вызова тулов (Agentic Loop)."""
    #     logger.info(f"[ResponseWorker] Вызов субагента для tg_id={tg_id} (сообщений: {info['quantity']})")

    #     system, tools = await self.llm.get_tools("tg_inbound_agent")
    #     dialog = [{
    #         "role": "user", 
    #         "content": f"Пользователь tg_id={tg_id} написал {info['quantity']} новых сообщений владельцу."
    #     }]

    #     max_steps = 30  # Страховка от бесконечного цикла временно
    #     step = 0

    #     try:

    #         while step < max_steps:
    #             step += 1

    #             #print(f"\nDIALOG SUB AGENT: {dialog}\n")

    #             # 1. Запрос к LLM (без стриминга)
    #             answer_llm = await self.llm.call_with_tools(
    #                 system=system, 
    #                 dialog=dialog, 
    #                 question=None, 
    #                 tools=tools
    #             )

    #             # Обработка сбоя сети / API
    #             if answer_llm['type'] == 'error':
    #                 logger.error(f"[Subagent] Ошибка LLM: {answer_llm['content']}")
    #                 return f"⚠️ Ошибка вызова LLM: {answer_llm['content']}"

    #             # 2. ФИНАЛ: Модель вернула только текст (анализ завершен)
    #             if answer_llm['type'] == 'text':
    #                 final_text = answer_llm['content']
    #                 dialog.append({"role": "assistant", "content": final_text})
    #                 logger.info(f"[Subagent] Анализ завершен за {step} шагов.")

    #                 ##### Reply to the Jumis queue #####
    #                 # Вылавливаем тут - jumis/jumis_agent/jumis_agent.py
    #                 """ Consumer: JumisAgent (обработка черновиков из queue_req_jum) """
    #                 await self.queue_req_jum.put({
    #                     "tg_id": tg_id,
    #                     "draft_text": final_text,
    #                     "source": "tg_inbound_agent"
    #                 })

    #                 logger.info(f"[Subagent] Передал в очередь Jumis сообщение.")
    #                 print(f"[Subagent] Передал в очередь Jumis сообщение.")
    #                 return

    #             # 3. ТУЛЫ: Модель просит вызвать инструменты
    #             if answer_llm['type'] == 'tool_calls':
    #                 # Шаг 3А: Фиксируем запрос модели в истории (со сгенерированными tool_calls)
    #                 dialog.append({
    #                     "role": "assistant",
    #                     "content": answer_llm.get('content') or "",
    #                     "tool_calls": answer_llm['tool_calls']
    #                 })

    #                 # Шаг 3Б: Последовательно вызываем каждый тул и пишем результат в dialog
    #                 for tc in answer_llm['tool_calls']:
    #                     call_id = tc['id']
    #                     func_name = tc['function']['name']
    #                     raw_args = tc['function']['arguments']

    #                     # Парсим аргументы
    #                     try:
    #                         args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    #                     except json.JSONDecodeError:
    #                         args = {}

    #                     logger.info(f"🛠️ [Subagent Step {step}] Вызов {func_name}({args})")

    #                     # Выполняем тул
    #                     try:
    #                         result = await self.llm.call_function(func_name, args)
    #                         result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
    #                     except Exception as e:
    #                         logger.error(f"💥 Ошибка тула {func_name}: {e}")
    #                         result_str = json.dumps({"error": str(e)}, ensure_ascii=False)

    #                     # Добавляем результат функции в диалог по стандарту OpenAI/LiteLLM
    #                     dialog.append({
    #                         "role": "tool",
    #                         "tool_call_id": call_id,
    #                         "content": result_str
    #                     })

    #         logger.warning(f"[Subagent] Достигнут лимит шагов ({max_steps})")
    #         return "⚠️ Субагент превысил лимит шагов и не смог завершить анализ."

    #     except asyncio.CancelledError:
    #         logger.info(f"🚫 Задача субагента для tg_id={tg_id} была успешно отменена.")
    #         raise  # Обязательно пробрасываем дальше для корректной остановки task
    #     finally:
    #         # В конце работы чистим за собой словарь задач
    #         self.active_subagent_tasks.pop(tg_id, None)










