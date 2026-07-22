#! jumis/llm/llm_router.py (или deepseek.py)
import os
import asyncio
import litellm
from litellm import acompletion
from dotenv import load_dotenv
import inspect
from typing import AsyncGenerator, AsyncIterator, Tuple, Dict, Any, List, Optional
import json

from llm.agents import AGENTS
from llm.functions import FUNCTIONS
from config import HISTORY_LIMIT, USE_MEM
from llm.token_usege import DBTokenLogger
# from database.memories import load_memories

# Подгружаем переменные окружения
load_dotenv('.env.llm')

# Инициализируем глобальные настройки LiteLLM один раз при импорте модуля
litellm.cache = litellm.Cache(type="local")  # Включаем локальный в памяти (In-Memory) кэш
litellm.callbacks = [DBTokenLogger()]       # Регистрируем класс-логгер токенов в глобальный список







class LLMWorker:
    def __init__(self):
        ''' Экземпляр работы с LLM через litellm '''
        
        # Проверяем наличие ключей в окружении (не вызовет KeyError, если какого-то ключа пока нет)
        self._check_env_keys()

        # "deepseek/deepseek-v4-flash"
        # "gemini/gemini-2.5-flash"
        # "anthropic/claude-sonnet-4-5-20250929"
        # "gpt-5"
        # "xai/grok-3-latest"
        self.default_model = "deepseek/deepseek-v4-flash"
        self.dialog: List[Dict[str, Any]] = []
        self.memories: List[str] = []
        self.use_mem = USE_MEM
        self.history_limit = HISTORY_LIMIT
        self.functions = FUNCTIONS
        self.agents = AGENTS
        self.tool_choice = "auto"

    def _check_env_keys(self):
        """ Безопасная проверка загрузки API-ключей из .env """
        required_keys = ["DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY"]
        missing = [key for key in required_keys if not os.getenv(key)]
        if missing:
            print(f"⚠️ [LLMWorker Warning] Следующие ключи не найдены в .env.llm: {', '.join(missing)}")



    @staticmethod
    async def _error_net(text_err: str):
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

    # Позже доделаем память для каждого диалога с пользователем своя + векторизируем..
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
                
                # Поддержка формата, если функция зарегистрирована словарём или через callable
                description = func_info.get("description", "") if isinstance(func_info, dict) else getattr(func_info, "__doc__", "")
                schema = func_info.get("schema", {}) if isinstance(func_info, dict) else {}

                tools.append({
                    "type": "function",
                    "function": {
                        "name": func_name,
                        "description": description,
                        "parameters": schema
                    }
                })
        return tools
    

    async def call_function(self, func_name: str, arguments: dict):
        """ Вызывает зарегистрированную функцию с переданными аргументами.
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

            # Вызов в зависимости от типа функции (async/sync)
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
            function_names = self.agents[agent].get("tools", [])
            tools = await self.get_tools_for_agent(function_names)
            
            if not self.use_mem:
                return system, tools
            
            return system, tools
        
        except KeyError as e:
            raise KeyError(f"Агент '{agent}' не найден. Доступные агенты: {list(self.agents.keys())}") from e

                
                # Позже доделаем память для каждого диалога с пользователем своя + векторизируем..
                # # Намертво изолируем воспоминания только для главного агента
                # if agent == "general_agent":
                #     memories = await self.load_memories()
                #     if memories:
                #         system = f"{system.rstrip()}\n\n**Важные воспоминания агента:**\n{memories}"
                        



    async def get_agent_info(self, agent: str) -> dict:
        """Полная информация об агенте (опционально)"""
        agent_config = self.agents[agent].copy()  # Копируем, чтобы не менять оригинал
        agent_config["tools_for_api"] = await self.get_tools_for_agent(agent_config.get("tools", []))
        return agent_config





    async def _call_request(
            self,
            system: str,
            question: str = None,
            stream: bool = True,
            tools: list = None,
            dialog: list = None
        ):
        """ Чистый вызов LLM через LiteLLM"""

        messages = [{"role": "system", "content": system}]
        if dialog is not None:
            messages.extend(dialog)
        elif self.dialog:
            messages.extend(self.dialog)

        if question:
            messages.append({"role": "user", "content": question})

        print("\ndialog:", json.dumps(messages, indent=2, ensure_ascii=False, default=str))

        # Важно: передаем tools только если они реально есть (не пустой список)
        actual_tools = tools if tools else None

        return await acompletion(
            model=self.default_model,
            messages=messages,
            tools=actual_tools,
            stream=stream,
            tool_choice=self.tool_choice if actual_tools else None
        )




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
        """ Добавление в диалог только сообщения/вопроса пользователя """
        self.dialog.append({"role": "user", "content": content})

    async def _add_dialog(self, question: str = None, answer: str = None):
        """ Добавляет пару вопрос-ответ в общую историю диалога """
        if question:
            await self.add_user_message(question)
        if answer:
            await self.add_assistant_message(content=answer)



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
        if not system:
            raise ValueError("System content is required")

        response = await self._call_request(
            system=system,
            question=question,
            dialog=dialog,
            stream=False
        )

        message = response.choices[0].message
        result = {
            'type': 'text',
            'content': message.content or '',
        }

        return result



    async def refine_stream(
            self,
            system: str,
            question: str = None,
        ) -> AsyncIterator[dict]:
        """ Простой стрим ответ от LLM без вызова функций + сохранение диалога"""

        if not system:
            print("Error: where is System content?")
            return

        collected_content = ""

        stream = await self._call_request(
            system=system,
            question=question,
            stream=True
        )
        
        async for chunk in stream:
            delta = chunk.choices[0].delta
            
            if delta.content:
                collected_content += delta.content
                yield {'type': 'text', 'content': delta.content}

        ########### Сохранение диалога #############
        await self._add_dialog(question=question, answer=collected_content)




    async def refine_stream_tools(
            self,
            system: str,
            question: str = None,
            tools: list = None,
        ) -> AsyncIterator[dict]:
        """ Запрос к LLM с вызовом функций и выводом стрима БЕЗ СОХРАНЕНИЯ ДИАЛОГА (вручную для контроля) """

        collected_content = ""
        tool_calls_buffer = {}  # Сборщик вызовов по index чанка

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

            # 2. Функции - надежное накопление по index чанка
            if delta.tool_calls:
                for tool_call in delta.tool_calls:
                    idx = tool_call.index
                    if idx not in tool_calls_buffer:
                        tool_calls_buffer[idx] = {
                            "id": tool_call.id or "",
                            "name": "",
                            "arguments": ""
                        }
                    
                    if tool_call.id:
                        tool_calls_buffer[idx]["id"] = tool_call.id
                    if tool_call.function:
                        if tool_call.function.name:
                            tool_calls_buffer[idx]["name"] += tool_call.function.name
                        if tool_call.function.arguments:
                            tool_calls_buffer[idx]["arguments"] += tool_call.function.arguments

        # 3. Отдаем накопленные вызовы функций разом после завершения стрима
        if tool_calls_buffer:
            # Преобразуем буфер в обычный словарь для отправки наружу
            formatted_tools = {
                tc["id"]: {"name": tc["name"], "arguments": tc["arguments"], "id": tc["id"]}
                for tc in tool_calls_buffer.values()
            }
            yield {'type': 'tool', 'data': formatted_tools}




    async def call_with_tools(
            self,
            system: str,
            dialog: list = None,
            question: str = None,
            tools: list = None,
        ) -> dict:
        """ Простой запрос к LLM с возможным вызовом функций (без стрима) """

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
        result = {}

        # Текстовый ответ
        if message.content:
            result['content'] = message.content
            result['type'] = 'text'
        else:
            result['content'] = ''
            result['type'] = 'text'

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

        return result



    # =========




    # import litellm

    # # 1. Проверить, знает ли LiteLLM эту модель
    # is_valid = litellm.check_valid_model(model="gemini/gemini-2.5-flash") # True / False

    # # 2. Получить список ВСЕХ поддерживаемых моделей (их там сотни)
    # all_models = list(litellm.model_cost.keys())

    # # 3. Найти модели конкретного провайдера (например, deepseek или gemini)
    # deepseek_models = [m for m in litellm.model_cost if "deepseek" in m]




    # from litellm import completion_cost

    # # Передаешь ответ от acompletion — получаешь точную стоимость запроса в $
    # cost = completion_cost(completion_response=response)
    # print(f"Запрос стоил: ${cost:.6f}")



    async def set_active_model(self, model_name: str) -> str:
        """ Проверяет наличие модели в LiteLLM и делает её активной """

        SYSTEM_CONFIG = ''
        
        # Проверяем, знает ли LiteLLM такую модель
        if not litellm.check_valid_model(model_name):
            return (
                f"❌ Модель '{model_name}' не найдена в текущей базе LiteLLM.\n"
                f"Возможно, она выжила недавно. Попробуй сначала вызвать инструмент upgrade_litellm_system."
            )
        
        # Сохраняем в системное состояние / оперативку / легкий JSON
        SYSTEM_CONFIG["active_model"] = model_name
        
        # Вытаскиваем сразу цены из LiteLLM для справки
        info = litellm.model_cost.get(model_name, {})
        input_price = info.get("input_cost_per_token", 0) * 1_000_000
        output_price = info.get("output_cost_per_token", 0) * 1_000_000
        
        return f"✅ Активная модель успешно изменена на <code>{model_name}</code>!\nТариф: ${input_price:.2f} / ${output_price:.2f} за 1M токенов."





    async def upgrade_litellm_system() -> str:
        """ Обновляет библиотеку LiteLLM до последней версии с актуальными моделями и ценами """

        import subprocess
        import sys

        try:
            # Запускаем pip install --upgrade litellm прямо из кода
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "litellm"],
                capture_output=True, text=True, check=True
            )
            
            # Перезагружаем модуль в памяти Python, чтобы подтянулся свежий model_cost.json
            import importlib
            importlib.reload(litellm)
            
            return "✅ LiteLLM успешно обновлен до последней версии! Справочник моделей и цен актуализирован."
        except Exception as e:
            return f"❌ Ошибка при обновлении: {str(e)}"



