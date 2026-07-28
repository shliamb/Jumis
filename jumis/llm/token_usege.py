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



# import time
# import litellm
# from litellm import completion_cost

# async def log_llm_usage(
#     db_pool, 
#     response, 
#     user_id: int = None, 
#     call_type: str = "chat", 
#     execution_time_ms: int = 0
# ):
#     """
#     Автоматически извлекает метрики из ответа LiteLLM,
#     вычисляет стоимость и сохраняет лог в таблицу llm_logs.
#     """
#     try:
#         # Извлекаем модель и объект usage
#         model = getattr(response, "model", "unknown")
#         usage = getattr(response, "usage", None)
        
#         prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
#         completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
#         total_tokens = getattr(usage, "total_tokens", 0) if usage else 0
        
#         # Извлекаем кэшированные токены (у Gemini/DeepSeek они лежат в prompt_tokens_details)
#         cached_tokens = 0
#         if usage and hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
#             cached_tokens = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
        
#         # Автоматический расчет стоимости запроса в $ через справочник LiteLLM
#         try:
#             cost = completion_cost(completion_response=response)
#         except Exception:
#             cost = 0.0

#         query = """
#         INSERT INTO llm_logs 
#         (user_id, model, prompt_tokens, completion_tokens, cached_tokens, total_tokens, cost_usd, execution_time_ms, call_type)
#         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9);
#         """
        
#         async with db_pool.acquire() as conn:
#             await conn.execute(
#                 query,
#                 user_id,
#                 model,
#                 prompt_tokens,
#                 completion_tokens,
#                 cached_tokens,
#                 total_tokens,
#                 cost,
#                 execution_time_ms,
#                 call_type
#             )
#     except Exception as e:
#         # Логирование ошибки не должно ломать основной ответ пользователю
#         print(f"⚠️ Ошибка при записи лога LLM: {e}")
