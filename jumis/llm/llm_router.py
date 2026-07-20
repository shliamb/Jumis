#! jumis/llm/deepseek.py
import os
import asyncio
import litellm
from litellm import acompletion
from dotenv import load_dotenv
#import httpx
import inspect
from typing import AsyncGenerator, AsyncIterator
from llm.agents import AGENTS
from llm.functions import FUNCTIONS
from config import HISTORY_LIMIT, USE_MEM
from jumis.llm.token_usege import DBTokenLogger
# from database.memories import load_memories
import json
load_dotenv('.env.llm')







class LLMWorker:
    def __init__(self):
        ''' Экземпляр работы с LLM через litellm'''
        litellm.cache = litellm.Cache(type="local") # Включаем локальный в памяти (In-Memory) кэш одной строчкой
        litellm.callbacks = [DBTokenLogger()] # Регистрируем класс в глобальный список коллбеков

        os.environ["DEEPSEEK_API_KEY"]
        os.environ["OPENAI_API_KEY"]
        os.environ["ANTHROPIC_API_KEY"]
        os.environ["GEMINI_API_KEY"]
        os.environ["XAI_API_KEY"]

        self.dialog = []
        self.memories = []
        self.use_mem = USE_MEM
        # self.model = MODEL_DS
        # self.timeout = TIMEOUT
        self.history_limit = HISTORY_LIMIT
        self.functions = FUNCTIONS
        self.agents = AGENTS
        self.tool_choice = "auto"
        # self.max_tokens = 1000
        # self.temperature = 0.7


    @staticmethod
    async def _error_net(text_err):
        """Заглушка для ошибок сети. Возвращает объект в формате ответа API, 
        чтобы основной код не сломался. Внутри будет текст ошибки вместо ответа модели."""

        from types import SimpleNamespace
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=text_err
                    )
                )
            ]
        )


    # async def load_memories(self):
    #     """ Выгрузка воспоминаний из базы """
    #     try:
    #         memories_list = await load_memories()
    #         if memories_list:
    #             return "\n".join(memories_list)
            
    #     except Exception as e:
    #         print(f"Ошибка загрузки памяти: {e}")
    #         self.memories = []
        


    async def get_tools_for_agent(self, function_names: list) -> list:
        """ Формирует tools из FUNCTIONS в functions.py"""
        tools = []
        for func_name in function_names:
            if func_name in self.functions:
                func_info = self.functions[func_name]
                tools.append({
                    "type": "function",
                    "function": {
                        "name": func_name,
                        "description": func_info["description"],
                        "parameters": func_info["schema"]
                    }
                })
        return tools
    

    async def call_function(self, func_name, arguments):
        """Вызывает зарегистрированную функцию с переданными аргументами.
        Автоматически передаёт llm, если функция их ожидает.
        Поддерживает два формата регистрации:
            - прямая функция (callable)
            - словарь с ключом 'function' (для сложных случаев с описанием)
        """
        entry = self.functions.get(func_name)
        if entry is None:
            return {"error": f"Function '{func_name}' not found"}

        # Определяем вызываемый объект
        if callable(entry):
            func = entry
        elif isinstance(entry, dict) and 'function' in entry:
            func = entry['function']
        else:
            return {"error": f"Invalid registration for function '{func_name}': {type(entry)}"}

        try:
            sig = inspect.signature(func)
            call_kwargs = dict(arguments)  # копируем аргументы

            # Автоматически добавляем llm, если функция его принимает
            if 'llm' in sig.parameters:
                call_kwargs['llm'] = self

            # Вызов в зависимости от типа функции
            if inspect.iscoroutinefunction(func):
                return await func(**call_kwargs)
            else:
                return func(**call_kwargs)

        except Exception as e:
            return {"error": f"Function '{func_name}' failed: {str(e)}"}



    async def get_tools(self, agent: str = "general_agent") -> tuple:
            """ Получает системный промпт и tools для указанного агента. """
            try:
                # Берем локальную копию промпта, чтобы случайно не испортить оригинал
                system = self.agents[agent]["system"]
                function_names = self.agents[agent]["tools"]
                tools = await self.get_tools_for_agent(function_names)
                #print(json.dumps(tools, indent=2, ensure_ascii=False))
                
                if not self.use_mem:
                    return system, tools
                
                # # Намертво изолируем воспоминания только для главного агента
                # if agent == "general_agent":
                #     memories = await self.load_memories()
                #     if memories:
                #         system = f"{system.rstrip()}\n\n**Важные воспоминания агента:**\n{memories}"
                        
                return system, tools
            
            except KeyError as e:
                raise KeyError(f"Агент '{agent}' не найден. Доступные агенты: {list(self.agents.keys())}") from e



    async def get_agent_info(self, agent: str) -> dict:
        """Полная информация об агенте (опционально)"""
        agent_config = self.agents[agent].copy()  # Копируем, чтобы не менять оригинал
        agent_config["tools_for_api"] = await self.get_tools_for_agent(agent_config["function"])
        return agent_config




    async def _call_request(
            self,
            system: str,
            question: str = None,
            stream: bool = True,
            tools: dict = None,
            dialog: list = None,
            response_format: dict = None
        ):
        """ Чистый вызов LLM """

        messages = [{"role": "system", "content": system}]
        if dialog is not None:
            messages.extend(dialog)
        elif self.dialog:
            messages.extend(self.dialog)

        if question:
            messages.append({"role": "user", "content": question})

        print("\ndialog:", json.dumps(messages, indent=2, ensure_ascii=False, default=str))


        return await acompletion(
            model="deepseek/deepseek-v4-flash",
            # model="gemini/gemini-3.1-flash-lite-preview",
            # model="anthropic/claude-sonnet-4-5-20250929",
            # model="gpt-5",
            # model="xai/grok-3-latest",
            messages=messages,
            tools=tools,
            #response_format=response_format,
            stream=stream,
            tool_choice=self.tool_choice
        )




    # async def _call_request(
    #         self,
    #         system: str,
    #         question: str = None,
    #         stream: bool = True,
    #         tools: dict = None,
    #         dialog: list = None,
    #         response_format: dict = None
    #     ):
    #     """
    #     Чистый вызов API DeepSeek с получением ключа из общей очереди.
    #     Возвращает ответ API или пробрасывает исключение при ошибке.
    #     """
    #     free_key_ds = None
    #     try:
    #         # Формируем messages
    #         messages = [{"role": "system", "content": system}]

    #         if dialog is not None:
    #             messages.extend(dialog)          # передали явно, даже если пустой список
    #         elif self.dialog:
    #             messages.extend(self.dialog)     # не передали — берём self.dialog
            
    #         if question:
    #             messages.append({"role": "user", "content": question}) # текущий вопрос user


    #         print("\ndialog:", json.dumps(messages, indent=2, ensure_ascii=False, default=str))
    #         print("Waiting for free DeepSeek API key...")

    #         free_key_ds = await self.key_queue.get()
    #         print(f"Got key ending with ...{free_key_ds[-4:]}")


    #         client = AsyncOpenAI(
    #             api_key=free_key_ds,
    #             base_url="https://api.deepseek.com",
    #             http_client=self.http_client
    #         )

    #         response = await client.chat.completions.create(
    #             model=self.model,
    #             messages=messages,
    #             tools=tools,
    #             response_format=response_format,
    #             stream=stream,
    #             timeout=self.timeout,
    #             tool_choice=self.tool_choice
    #         )

    #         # Если это стрим, оборачиваем его в безопасный генератор
    #         if stream:
    #             async def stream_wrapper(api_stream, key):
    #                 try:
    #                     async for chunk in api_stream:
    #                         yield chunk
    #                 finally:
    #                     # Ключ вернётся в очередь СТРОГО после закрытия или прочтения стрима
    #                     self.key_queue.put_nowait(key)
    #                     print(f"Stream finished. Key ...{key[-4:]} returned to queue.")

    #             key_to_release = free_key_ds
    #             free_key_ds = None # Зануляем, чтобы нижний finally не вернул его раньше времени
    #             return stream_wrapper(response, key_to_release)
            
    #         # Если запрос обычный (не стрим) — просто отдаем ответ, finally внизу сам вернет ключ
    #         return response
        
    #     except APITimeoutError:
    #         return await self._error_net("Сервер DeepSeek отвечает слишком долго.")
    #     except APIConnectionError:
    #         return await self._error_net("Проблемы с интернет-соединением.")
    #     except RateLimitError:
    #         return await self._error_net("Превышен лимит запросов к DeepSeek.")
    #     except APIError as e:
    #         print(f"[DEEPSEEK ERROR] {e.__class__.__name__}: {str(e)}")
    #         return await self._error_net(f"Ошибка DeepSeek API: {e.status_code}")
    #     finally:
    #         # Сработает сразу только для обычных запросов (stream=False)
    #         if free_key_ds is not None:
    #             self.key_queue.put_nowait(free_key_ds)
    #             print(f"Simple call finished. Key ...{free_key_ds[-4:]} returned to queue.")




    async def add_tool_response(self, tool_call_id: str, content: str) -> None:
        """ Добавить ответ функции в историю диалога """
        self.dialog.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content
        })
        # БЕЗ обрезки здесь — trim вызывается явно после добавления всего блока


    async def add_assistant_message(self, content: str = "", tool_calls: list = None):
        """ Добавляет сообщение ассистента в диалог. tool_calls — список в формате API """
        msg = {"role": "assistant"}
        if content:
            msg["content"] = content
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.dialog.append(msg)
        # Обрезаем только если это assistant БЕЗ tool_calls (финальный ответ)
        if not tool_calls:
            self._safe_trim()



    async def add_user_message(self, content: str):
        """ Добавление в диалог только сообщение/вопрос пользователя """
        self.dialog.append({"role": "user", "content": content})



    def _safe_trim(self):
        """Обрезает диалог до history_limit, не разрывая пары assistant(tool_calls) ↔ tool."""
        if len(self.dialog) <= self.history_limit:
            print("\ntrim: NO\n")
            return
        # Сдвигаем точку обрезки вправо, пока не окажемся в безопасной позиции
        cut = len(self.dialog) - self.history_limit
        while cut < len(self.dialog):
            msg = self.dialog[cut]
            # Нельзя начинать с tool
            if msg.get('role') == 'tool':
                cut += 1
                continue
            # Нельзя начинать с assistant, у которого есть tool_calls (если за ним не идут все соответствующие tool)
            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                # Проверяем, что все tool-ответы на месте
                expected_ids = {tc['id'] for tc in msg['tool_calls']}
                found_ids = set()
                for m in self.dialog[cut+1:]:
                    if m.get('role') == 'tool':
                        if m.get('tool_call_id') in expected_ids:
                            found_ids.add(m['tool_call_id'])
                    else:
                        break
                if found_ids != expected_ids:
                    cut += 1
                    continue
            break
        print("\ntrim: YES\n")
        self.dialog = self.dialog[cut:]



    async def _clear_dialog(self) -> None:
        """ Очистка диалога """
        self.dialog = []



    async def simple_call(
            self,
            system: str,
            dialog: list = None,
            question: str = None
        ) -> dict:
        """
        Простой запрос к LLM без вызова функций.
        Возвращает словарь с текстом и usage.
        Диалог свой личный!
        """
        
        if dialog is None:
            dialog = []

        if not system:
            raise ValueError("System content is required")

        response = await self._call_request(
            system=system,
            question=question,
            dialog=dialog,
            stream=False
        )

        message = response.choices[0].message
        # usage = response.usage

        result = {
            'type': 'text',
            'content': message.content or '',
        }

        # if usage:
        #     result['usage'] = {
        #         'completion_tokens': usage.completion_tokens,
        #         'prompt_tokens': usage.prompt_tokens,
        #         'total_tokens': usage.total_tokens,
        #         'cached_tokens': getattr(
        #             usage.prompt_tokens_details, 'cached_tokens', 0
        #         ) if usage.prompt_tokens_details else 0
        #     }

        return result



    async def refine_stream(
            self,
            system: str,
            question: str = None,
        ) -> AsyncIterator[dict]:
        """
            Простой стрим ответ от LLM без вызова функций и АВТО СОХРАНЕНИЕ ДИАЛОГА
            Stream ответ от DeepSeek + usage:
            - {'type': 'text', 'content': '...'} - текст для stream вывода
            - {'type': 'usage', 'data': {...}} - статистика (в конце)
        """

        if not system:
            print("Error: where is System content?")
            return

        collected_content = ""
        answer = ""

        stream = await self._call_request(
                system=system,
                question=question,
                stream=True
            )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            
            # 1. Текстовый ответ stream
            if delta.content:
                collected_content += delta.content
                yield {'type': 'text', 'content': delta.content}

        # 2. Usage данные (из последнего chunk)
        if hasattr(chunk, 'usage') and chunk.usage:
            yield {
                'type': 'usage',
                'data': {
                    'completion_tokens': chunk.usage.completion_tokens,
                    'prompt_tokens': chunk.usage.prompt_tokens,
                    'total_tokens': chunk.usage.total_tokens,
                    'cached_tokens': getattr(chunk.usage.prompt_tokens_details, 'cached_tokens', 0)
                }
            }

        ########### Сохранение диалога  #############
        answer += collected_content
        await self._add_dialog(question=question, answer=answer)



    async def refine_stream_tools(
            self,
            system: str,
            question: str = None,
            tools: dict = None,
        ) -> AsyncIterator[dict]:
        """
            Запрос к LLM с фызовом функций и выводом стрима БЕЗ СОХРАНЕНИЯ ДИАЛОГА (в ручную для контроля)
            Stream ответ от DeepSeek + tools + usage:
            - {'type': 'text', 'content': '...'} - текст для stream вывода
            - {'type': 'tool', 'data': {...}} - данные о вызове функции (после завершения)
            - {'type': 'usage', 'data': {...}} - статистика (в конце)
        """

        collected_content = ""
        tool_calls_buffer = {}
        current_tool_id = None

        try:
            stream = await self._call_request(
                system=system,
                question=question,
                tools=tools,
                stream=True
            )
            if not hasattr(stream, '__aiter__'):
                yield {'type': 'error', 'content': f"Ошибка API: неожиданный ответ {type(stream)}"}
                return
        except Exception as e:
            yield {'type': 'error', 'content': f"Ошибка API: {e}"}
            return

        async for chunk in stream:
            delta = chunk.choices[0].delta
            
            # 1. Текстовый ответ stream
            if delta.content:
                collected_content += delta.content
                yield {'type': 'text', 'content': delta.content}

            # 2. Функции - накапливаем, отдаем в конце
            if delta.tool_calls:
                for tool_call in delta.tool_calls:
                    if tool_call.id:
                        # Новый вызов функции
                        current_tool_id = tool_call.id
                        tool_calls_buffer[current_tool_id] = {
                            "name": tool_call.function.name or "",
                            "arguments": tool_call.function.arguments or "",
                            'id': tool_call.id
                        }
                    elif tool_call.function:
                        # Продолжение аргументов
                        if tool_call.function.name:
                            tool_calls_buffer[current_tool_id]["name"] += tool_call.function.name
                        if tool_call.function.arguments:
                            tool_calls_buffer[current_tool_id]["arguments"] += tool_call.function.arguments


        # 3. Usage данные (из последнего chunk)
        if hasattr(chunk, 'usage') and chunk.usage:
            yield {
                'type': 'usage',
                'data': {
                    'completion_tokens': chunk.usage.completion_tokens,
                    'prompt_tokens': chunk.usage.prompt_tokens,
                    'total_tokens': chunk.usage.total_tokens,
                    'cached_tokens': getattr(chunk.usage.prompt_tokens_details, 'cached_tokens', 0)
                }
            }

        # 4. Вызовы функций (отдаем разом после стрима)
        tool_calls_list = []   # инициализируем здесь

        if tool_calls_buffer:
            # Преобразуем буфер в список tool_calls в формате API
            for tool_id, tc in tool_calls_buffer.items():
                tool_calls_list.append({
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"]
                    }
                })

            yield {'type': 'tool', 'data': tool_calls_buffer}




    async def call_with_tools(
            self,
            system: str,
            dialog: list = None,
            question: str = None,
            tools: list = None,
        ) -> dict:
        """
        Простой запрос к LLM с возможным вызовом функций (без стрима).
        Возвращает словарь с результатом:
        - {'type': 'text', 'content': '...', 'usage': {...}}
        - {'type': 'tool_calls', 'tool_calls': [...], 'content': '...', 'usage': {...}}
        - {'type': 'error', 'content': '...'}
        """
        if not system:
            raise ValueError("System content is required")

        try:
            response = await self._call_request(
                system=system,
                question=question,
                dialog=dialog,
                tools=tools,
                stream=False
            )
        except Exception as e:
            return {'type': 'error', 'content': f"API request failed: {e}"}

        if not response.choices:
            return {'type': 'error', 'content': "Empty response from API"}

        message = response.choices[0].message
        usage = response.usage

        result = {}

        # Текстовый ответ
        if message.content:
            result['content'] = message.content
            result['type'] = 'text'
        else:
            result['content'] = ''
            result['type'] = 'text'  # запасной тип, если нет ни текста, ни tool_calls

        # Вызовы инструментов
        if message.tool_calls:
            result['type'] = 'tool_calls'
            result['tool_calls'] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                }
                for tc in message.tool_calls
            ]

        # # Статистика
        # if usage:
        #     result['usage'] = {
        #         'completion_tokens': usage.completion_tokens,
        #         'prompt_tokens': usage.prompt_tokens,
        #         'total_tokens': usage.total_tokens,
        #         'cached_tokens': getattr(
        #             usage.prompt_tokens_details, 'cached_tokens', 0
        #         ) if usage.prompt_tokens_details else 0
        #     }

        return result











