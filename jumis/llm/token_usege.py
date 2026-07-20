# jumis/llm/token_usege.py
import json
from litellm.integrations.custom_logger import CustomLogger # Импортируем базовый класс для логирования




class DBTokenLogger(CustomLogger):
    """ Класс-логгер, наследуясь от CustomLogger """
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        model = kwargs.get("model", "")
        usage = getattr(response_obj, "usage", None)
        
        if usage:
            try:
                usage_dict = usage.model_dump()
            except AttributeError:
                usage_dict = getattr(usage, "__dict__", {})

            # 1. Вытаскиваем базовые метрики (с защитой "or 0" на случай None)
            prompt_tokens = usage_dict.get("prompt_tokens") or 0
            completion_tokens = usage_dict.get("completion_tokens") or 0
            total_tokens = usage_dict.get("total_tokens") or 0

            # 2. Вытаскиваем детализацию кэша
            prompt_details = usage_dict.get("prompt_tokens_details") or {}
            
            # Магия здесь: если там None, оператор "or 0" жестко превратит его в 0
            cached_tokens = prompt_details.get("cached_tokens") or 0

            # 3. Выводим красивый структурированный отчет в терминал
            print("\n" + "=" * 60)
            print(f"📊 [АНАЛИТИКА ТРАФИКА ЛЛМ] | Модель: {model}")
            print("-" * 60)
            print(f" 📥 Вход (Prompt):       {prompt_tokens} ток.")
            if cached_tokens > 0:
                print(f"    └─ ⚡ ИЗ НИХ В КЭШЕ:  {cached_tokens} ток. (Скидка! 🎉)")
            else:
                print(f"    └─ ⚡ ИЗ НИХ В КЭШЕ:  0 ток. (Чистый запрос)")
                
            print(f" 📤 Выход (Completion):  {completion_tokens} ток.")
            print(f" 🔄 Всего (Total):       {total_tokens} ток.")
            print("-" * 60)
            
        else:
            print(f"\n[Учет] Не удалось вытащить объект usage для модели {model}")



# class DBTokenLogger(CustomLogger):
#     """ Класс-логгер, наследуясь от CustomLogger """
#     async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
#         model = kwargs.get("model", "")
#         usage = getattr(response_obj, "usage", None)
        
#         if usage:
#             # Переводим Pydantic-модель LiteLLM в обычный питоновский словарь
#             try:
#                 usage_dict = usage.model_dump()
#             except AttributeError:
#                 usage_dict = getattr(usage, "__dict__", {})

#             # 1. Вытаскиваем базовые метрики
#             prompt_tokens = usage_dict.get("prompt_tokens", 0)
#             completion_tokens = usage_dict.get("completion_tokens", 0)
#             total_tokens = usage_dict.get("total_tokens", 0)

#             # 2. Вытаскиваем детализацию кэша (стандарт OpenAI/DeepSeek)
#             prompt_details = usage_dict.get("prompt_tokens_details", {}) or {}
#             cached_tokens = prompt_details.get("cached_tokens", 0)

#             # 3. Выводим красивый структурированный отчет в терминал
#             print("\n" + "=" * 60)
#             print(f"📊 [АНАЛИТИКА ТРАФИКА ЛЛМ] | Модель: {model}")
#             print("-" * 60)
#             print(f" 📥 Вход (Prompt):       {prompt_tokens} ток.")
#             if cached_tokens > 0:
#                 print(f"    └─ ⚡ ИЗ НИХ В КЭШЕ:  {cached_tokens} ток. (Скидка! 🎉)")
#             else:
#                 print(f"    └─ ⚡ ИЗ НИХ В КЭШЕ:  0 ток. (Чистый запрос)")
                
#             print(f" 📤 Выход (Completion):  {completion_tokens} ток.")
#             print(f" 🔄 Всего (Total):       {total_tokens} ток.")
#             print("-" * 60)
#             # print("🔍 [ПОЛНЫЙ ДАМП ОБЪЕКТА USAGE ДЛЯ ТВОЕЙ СТАТИСТИКИ]:")
#             # print(json.dumps(usage_dict, indent=4, ensure_ascii=False))
#             # print("=" * 60 + "\n")
            
#             # Сюда потом вставишь свой: await db.save_metrics(...)
#         else:
#             print(f"\n[Учет] Не удалось вытащить объект usage для модели {model}")
