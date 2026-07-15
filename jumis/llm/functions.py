# from database.tasks import add_task, delete_task_db, get_tasks_by_statuses, edit_task_db, get_task_by_id
# from database.workers import get_workers, edit_worker_by_id
# from database.devices import get_devices_by_worker_id, edit_device_id
# from database.memories import add_memory, del_memory
from utils.validate_task import clean_task, clean_worker, clean_device
from datetime import datetime
import json


# async def save_task(task) -> str:
#     try:
#         print("\n\nraw_data_task:", task, "\n")
#         task_data = task.get("task", task)  
#         print("\n\ntask_data:", task_data, "\n")
        
#         cleaned = clean_task(task_data)
#         print("\n\ncleaned:", cleaned, "\n")
        
#         # --- СТРАХОВКА ДЛЯ ЗАПИСИ В TEXT ПОЛЯ БД ---
#         # Если модель передала execution_log, result или error как объекты (list/dict),
#         # сериализуем их в JSON-строку, чтобы база скушала их как TEXT
#         for field in ["execution_log", "result", "error"]:
#             if field in cleaned and isinstance(cleaned[field], (list, dict)):
#                 cleaned[field] = json.dumps(cleaned[field], ensure_ascii=False)
#         # --------------------------------------------

#         task_id = await add_task(cleaned)
#         print("\n\ntask_id:", task_id, "\n") 
        
#         if isinstance(task_id, int) and task_id > 0:
#             return f"Таска успешно добавлена, id: {task_id}"
#         else:
#             return f"Ошибка при сохранении: {task_id}"
#     except Exception as e:
#         print(f"Исключение в save_task: {e}")
#         return f"Ошибка: {e}, таска не сохранена"
    


# async def get_date():
#     """ Получение даты """
#     from datetime import datetime
#     return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
 



# async def get_task(id: int) -> str:
#     """Получение данных одной задачи в текстовом формате, оптимизированном для контекста ИИ-агента."""
#     try:
#         # Получаем сырые данные из БД
#         task_data = await get_task_by_id(id)
#         if not task_data:
#             return f"Таска с id={id} не найдена."
        
#         parts = []
#         # Исключаем служебные таймстампы и гигантские логи
#         skip_keys = {'created_at', 'started_at', 'completed_at', 'execution_log'}
        
#         for key, value in task_data.items():
#             # Особое условие автора: если run_at пустой, всё равно показываем его агенту
#             if key == 'run_at' and not value:
#                 parts.append(f"{key}: {value}")
#                 continue
            
#             # Пропускаем ненужные ключи, пустые строки и None
#             if key in skip_keys or value is None or value == '':
#                 continue
            
#             # Красивое сжатие сложных структур данных (словари и списки) в JSON
#             if isinstance(value, (list, dict)):
#                 json_str = json.dumps(value, ensure_ascii=False)
#                 # Если это результат выполнения, делаем его покороче (50 симв), для остального — 100 симв.
#                 limit = 50 if key == 'result' else 100
#                 value = json_str[:limit] + "..." if len(json_str) > limit else json_str
            
#             # Приведение datetime к читаемому ИИ формату (без микросекунд)
#             elif isinstance(value, datetime):
#                 value = value.strftime('%Y-%m-%d %H:%M')
                
#             # Формируем строку с пробелом после двоеточия для идеальной токенизации
#             parts.append(f"{key}: {value}")
            
#         # Возвращаем плоскую строку для одной конкретной задачи
#         return " | ".join(parts)
        
#     except Exception as e:
#         print(f"[AGENT TOOL ERROR] Ошибка в функции get_task: {e}")
#         return f"Не удалось получить данные задачи из-за внутренней ошибки: {e}"
    
        


# async def get_tasks(statuses: list) -> str:
#     """Получение всех задач в плоском текстовом формате, оптимизированном для контекста ИИ-агента."""
#     try:
#         # Вызываем нашу оптимизированную функцию работы с БД
#         rows = await get_tasks_by_statuses(statuses)
#         if not rows:
#             return "Задачи с указанными статусами не найдены."
        
#         result = []
#         # Исключаем служебные таймстампы и гигантские логи, чтобы экономить контекстное окно модели
#         skip_keys = {'created_at', 'started_at', 'completed_at', 'execution_log'}
        
#         for row in rows:
#             parts = []
#             for key, value in row.items():
#                 # Особое условие автора: если run_at пустой, всё равно показываем его агенту
#                 if key == 'run_at' and not value:
#                     parts.append(f"{key}: {value}")
#                     continue
                
#                 # Пропускаем ненужные ключи, пустые строки и None
#                 if key in skip_keys or value is None or value == '':
#                     continue
                
#                 # Красивое сжатие сложных структур данных (словари и списки) в JSON
#                 if isinstance(value, (list, dict)):
#                     json_str = json.dumps(value, ensure_ascii=False)
#                     # Если это результат выполнения, делаем его покороче (50 симв), для остального — 100 симв.
#                     limit = 50 if key == 'result' else 100
#                     value = json_str[:limit] + "..." if len(json_str) > limit else json_str
                
#                 # Приведение datetime к читаемому ИИ формату (без микросекунд)
#                 elif isinstance(value, datetime):
#                     value = value.strftime('%Y-%m-%d %H:%M')
                    
#                 # Формируем строку с пробелом после двоеточия для идеальной токенизации
#                 parts.append(f"{key}: {value}")
                
#             result.append(" | ".join(parts))
            
#         return "\n".join(result)
        
#     except Exception as e:
#         print(f"[AGENT TOOL ERROR] Ошибка в функции get_tasks: {e}")
#         return f"Не удалось получить список задач из-за внутренней ошибки: {e}"



# async def delete_task(task_id: int) -> str:
#     """ Удаление таски """
#     try:
#         conf = await delete_task_db(task_id)
#         return conf
#     except Exception as e:
#         return f"Ошибка удаления таски: {e}"



# async def update_task(task_data: dict) -> str:
#     """ Обновить задачу (вызывается агентом). Ожидает словарь с ключами 'id' и любыми другими полями для изменения."""
#     try:
#         # Проверим, что id передан
#         if 'id' not in task_data:
#             return "Ошибка: не указан id задачи"
        
#         cleaned = clean_task(task_data)
        
#         # --- СТРАХОВКА ДЛЯ UPDATE В TEXT ПОЛЯ БД ---
#         # Если агент пытается обновить логи или результаты сложными структурами,
#         # переводим их в JSON-строку, чтобы Postgres сожрал их как TEXT
#         for field in ["execution_log", "result", "error"]:
#             if field in cleaned and isinstance(cleaned[field], (list, dict)):
#                 cleaned[field] = json.dumps(cleaned[field], ensure_ascii=False)
#         # --------------------------------------------

#         success = await edit_task_db(cleaned)
#         if success:
#             return f"Задача {task_data['id']} обновлена"
#         else:
#             return f"Не удалось обновить задачу {task_data['id']}"
#     except Exception as e:
#         print(f"Исключение в update_task: {e}") # Чтобы в консоли сервера тоже было видно, если что-то пойдет не так
#         return f"Ошибка обновления: {e}"


# async def write_mem(category: str, key: str, text: str) -> str:
#     """ Сохранить/Обновить важную информацию в долговременную память."""
#     return await add_memory(category, key, text)


# async def del_mem(category: str, key: str) -> str:
#     """ Удалить восспоминание из долгосрочной памяти. """
#     return await del_memory(category, key)


# async def get_data_workers() -> str:
#     """ Получить список всех воркеров """
#     try:
#         rows: list[dict] = await get_workers()
#         if not rows:
#             return "Нет Worker"
#         result = []
#         # Поля, которые не нужны агенту (слишком технические или большие)
#         skip_keys = {'crypto_key'}
#         for row in rows:
#             parts = []
#             for key, value in row.items():
#                 if key in skip_keys or value is None or value == '':
#                     continue
#                 # Для datetime - без микросекунд
#                 elif isinstance(value, datetime):
#                     value = value.strftime('%Y-%m-%d %H:%M')
#                 parts.append(f"{key}:{value}")
#             result.append(" | ".join(parts))
#         # print("\n".join(result))
#         return "\n".join(result)
#     except Exception as e:
#         print(f"Ошибка получения workers: {e}")
#         return f"Ошибка получения workers: {e}"



# async def update_worker(worker_data: dict) -> str:
#     """ Обновление воркера """
#     try:
#         # Проверим, что id передан
#         if 'id' not in worker_data:
#             return "Ошибка: не указан id воркера"
        
#         cleaned = clean_worker(worker_data)
#         success = await edit_worker_by_id(cleaned)
#         if success:
#             return f"Воркер {worker_data['id']} обновлен"
#         else:
#             return f"Не удалось обновить воркера {worker_data['id']}"
#     except Exception as e:
#         return f"Ошибка обновления воркера: {e}"



# async def get_devices_worker(worker_id: int) -> str:
#     """ Получить данные всех устройств воркера по его id """
#     try:
#         rows: list[dict] = await get_devices_by_worker_id(worker_id) # worker_id - telegram_id
#         if not rows:
#             return f"Нет устройств у воркера {worker_id}"
#         result = []
#         # Поля, которые не нужны агенту (слишком технические или большие)
#         skip_keys = {'connected', 'hidden'}
#         for row in rows:
#             parts = []
#             for key, value in row.items():
#                 if key in skip_keys or value is None or value == '':
#                     continue
#                 # Для datetime - без микросекунд
#                 elif isinstance(value, datetime):
#                     value = value.strftime('%Y-%m-%d %H:%M')
#                 parts.append(f"{key}:{value}")
#             result.append(" | ".join(parts))
#         # print("\n".join(result))
#         return "\n".join(result)
#     except Exception as e:
#         print(f"Ошибка получения устройств воркера {worker_id}: {e}")
#         return f"Ошибка получения устройств воркера {worker_id}: {e}"


# async def update_device(device_data: dict) -> str:
#     """ Обнавление данных устройства по его id """
#     try:
#         # Проверим, что id передан
#         if 'id' not in device_data:
#             return "Ошибка: не указан id устройства"
        
#         cleaned = clean_device(device_data)
#         success = await edit_device_id(cleaned)
#         if success:
#             return f"Данные устройства {device_data['id']} обновлены"
#         else:
#             return f"Не удалось обновить данные устройства {device_data['id']}"
#     except Exception as e:
#         return f"Ошибка обновления данных устройства: {e}"




FUNCTIONS = {
    # "save_task": {
    #     "description": "Сохранить задачу в базу данных. Вызывай для планирования следующего циклического шага.",
    #     "function": save_task,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "task": {
    #                 "type": "object",
    #                 "description": "Данные задачи в соответствии с таблицей tasks",
    #                 "properties": {
    #                     "target_type": {"type": "string", "description": "any, worker, device"},
    #                     "target_id": {"type": ["integer", "null"], "description": "Если выбран target_type = worker, то указываем workers.telegram_id"},
    #                     "target_device_sn": {"type": ["string", "null"], "description": "Если выбран target_type = device, то указываем devices.sn"},
    #                     "schedule_type": {"type": "string", "description": "once, cyclic"},
    #                     "run_at": {"type": ["string", "null"], "description": "Выставить время выполнения в TIMESTAMP (пример '2026-06-21 14:52')"},
    #                     "priority": {"type": "integer", "description": "чем больше, тем важнее"},
    #                     "comment": {"type": "string", "description": "Основное описание задачи таски"},
    #                     "status": {"type": "string", "description": "Статус таски, по умолчанию pending"},
    #                     "max_retries": {"type": "integer", "description": "Максимальное количество попыток выполнить таску, по умолчанию 3"},
    #                     "notify_admin": {"type": "boolean", "description": "Включение уведомления админу, по умолчанию False"},
    #                     "debug_thinking": {"type": "boolean", "description": "Активация вывода хода мыслей агента, по умолчанию False"},
    #                     "llm_model": {"type": ["string", "null"], "description": "Модель для управления телефоном, по умолчанию deepseek-chat"},
    #                     "max_agent_steps": {"type": ["integer", "null"], "description": "Максимальное количество шагов агента на телефоне"},

    #                     "root_id": {"type": ["integer", "null"], "description": "ID самой первой таски-прародителя цепочки. Переноси из инструкции."},
    #                     "iteration_number": {"type": "integer", "description": "Порядковый номер текущего повтора (шаг цепочки)."},
    #                     "cyclic_instruction": {"type": ["string", "null"], "description": "Инструкция суб агенту для колонирования новой таски cyclic"}
    #                 },
    #                 "required": ["target_type", "schedule_type", "comment", "run_at", "root_id", "iteration_number"] # Сделали root и iteration обязательными для субагента
    #             }
    #         },
    #         "required": ["task"]
    #     }
    # },

    # "get_date": {
    #     "description": "Получить текущую дату и время. Используй, когда нужно узнать текущее время.",
    #     "function": get_date,
    #     "schema": {
    #         "type": "object",
    #         "properties": {},
    #         "required": []
    #     }
    # },

    # "get_tasks": {
    #     "description": "Получить список задач по выбранным статусам. Используй всегда, когда нужно проверить текущие таски по статусу.",
    #     "function": get_tasks,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "statuses": {
    #                 "type": "array",
    #                 "items": {
    #                     "type": "string",
    #                     "enum": ["pending", "assigned", "running", "completed", "failed", "cancelled"]
    #                 },
    #                 "description": "Список статусов задач, которые необходимо выгрузить из базы данных."
    #             }
    #         },
    #         "required": ["statuses"]
    #     }
    # },

    # "get_task": {
    #     "description": "Получить данные одной таски по id. Используй всегда, когда нужно получить данные таски.",
    #     "function": get_task,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "id": {
    #                 "type": "integer",
    #                 "description": "ID задачи"
    #             }
    #         },
    #         "required": ["id"]
    #     }
    # },

    # "delete_task": {
    #     "description": "Удалить задачу по id. Используй, когда пользователь просит удалить задачу.",
    #     "function": delete_task,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "task_id": {
    #                 "type": "integer",
    #                 "description": "ID задачи"
    #             }
    #         },
    #         "required": ["task_id"]
    #     }
    # },

    # "update_task": {
    #     "description": "Обновить существующую задачу. Передай словарь с id и полями для изменения. Вызывай после подтверждения пользователя.",
    #     "function": update_task,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "task_data": {
    #                 "type": "object",
    #                 "description": "Словарь с id задачи и полями для обновления",
    #                 "properties": {
    #                     "id": {"type": "integer"},
    #                     "target_type": {"type": "string", "description": "any, worker, device"},
    #                     "target_id": {"type": ["integer", "null"], "description": "Если выбран target_type = worker, то указываем workers.telegram_id"},
    #                     "target_device_sn": {"type": ["string", "null"], "description": "Если выбран target_type = device, то то указываем - devices.sn"},
    #                     "schedule_type": {"type": "string", "description": "once, cyclic"},
    #                     "run_at": {"type": ["string", "null"],  "description": "Время выполнения в TIMESTAMP (пример '2026-05-08 14:52')"},
    #                     "cyclic_instruction": {"type": ["string", "null"],  "description": "Инструкция суб агенту для колонирования новой таски cyclic"},
    #                     "priority": {"type": "integer", "description": "чем больше, тем важнее"},
    #                     "comment": {"type": "string", "description": "Основное описание задачи таски"},
    #                     "status": {"type": "string", "description": "Статус таски, по умолчанию pending, возможны - assigned, running, completed, failed, cancelled"},
    #                     "max_retries": {"type": "integer", "description": "Максимальное колличество попыток выполнить таску, по умолчанию 3"},
    #                     "notify_admin": {"type": "boolean", "description": "Включение личное уведомление админу о статусе этой задачи, по умолчанию False"},
    #                     "debug_thinking": {"type": "boolean", "description": "Активация вывода в боте воркера ход мыслей агента таски, по умолчанию False"},
    #                     "llm_model": {"type": ["string", "null"], "description": "Языковая модель которая будет использоваться для управления агентом телефона, по умолчанию - deepseek-chat"},
    #                     "max_agent_steps": {"type": ["integer", "null"], "description": "Колличество максимальных итераций агента, по умолчанию - 30"}
    #                 },
    #                 "required": ["id"]
    #             }
    #         },
    #         "required": ["task_data"]
    #     }
    # },

    # "write_memory": {
    #     "description": "Сохранить или ОБНОВИТЬ важную информацию в память. Если категория (category) и ключ (key) уже существуют, старый текст автоматически заменится новым в один шаг (удалять старое перед вызовом НЕ НАДО). Сжимай данные до сути.",
    #     "function": write_mem,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "category": {"type": "string", "description": "Категория (н-р: 'user', 'project', 'infrastructure')"},
    #             "key": {"type": "string", "description": "Уникальный slug-ключ (н-р: 'family_structure', 'huawei_state')"},
    #             "text": {"type": "string", "description": "Сухой факт без воды"}
    #         },
    #         "required": ["category", "key", "text"]
    #     }
    # },

    # "delete_memory": {
    #     "description": "Удалить конкретное воспоминание, если оно стало неактуальным.",
    #     "function": del_mem,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "category": {"type": "string"},
    #             "key": {"type": "string"}
    #         },
    #         "required": ["category", "key"]
    #     }
    # },

    # "get_workers": {
    #     "description": "Получить список всех воркеров",
    #     "function": get_data_workers,
    #     "schema": {
    #         "type": "object",
    #         "properties": {},
    #         "required": []
    #     }
    # },

    # "update_worker": {
    #     "description": "Обновить данные воркера. Передай словарь с id воркера и полями для изменения. Вызывай после подтверждения пользователя.",
    #     "function": update_worker,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "worker_data": {
    #                 "type": "object",
    #                 "description": "Словарь с id воркера и полями для обновления",
    #                 "properties": {
    #                     "id": {"type": "integer", "description": "id воркера "},
    #                     "confirmed": {"type": "boolean", "description": "Подтверждён ли воркер (true/false)"},
    #                     "name": {"type": ["string", "null"], "description": "Имя воркера"},
    #                     "location": {"type": ["string", "null"], "description": "Локация воркера (например, 'Moscow')"},
    #                     "comment": {"type": ["string", "null"], "description": "Комментарий"},
    #                     "balance": {"type": "number"}
    #                 },
    #                 "required": ["id"]
    #             }
    #         },
    #         "required": ["worker_data"]
    #     }
    # },

    # "get_devices_worker": {
    #     "description": "Получить данные всех устройств воркера по воркер telegram_id",
    #     "function": get_devices_worker,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "worker_id": {
    #                 "type": "integer",
    #                 "description": "telegram_id воркера "
    #             }
    #         },
    #         "required": ["worker_id"]
    #     }
    # },

    # "update_device": {
    #     "description": "Обновить данные устройства. Передай словарь с идентификатором устройства id и полями для изменения. Вызывай после подтверждения пользователя.",
    #     "function": update_device,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "device_data": {
    #                 "type": "object",
    #                 "description": "Словарь с id устройства и полями для обновления",
    #                 "properties": {
    #                     "id": {"type": "integer", "description": "id устройства (первичный ключ)"},
    #                     "status": {
    #                         "type": "string",
    #                         "enum": ["available", "busy", "disabled", "maintenance"],
    #                         "description": "Новый статус устройства"
    #                     },
    #                     "location": {"type": ["string", "null"], "description": "Локация устройства"},
    #                     "comment": {"type": ["string", "null"], "description": "Комментарий"},
    #                     "hidden": {"type": "boolean", "description": "Скрыт или нет для работы (true/false)"},
    #                     "device_persona": {"type": ["string", "null"], "description": "Профиль личности устройства"}
    #                 },
    #                 "required": ["id"]
    #             }
    #         },
    #         "required": ["device_data"]
    #     }
    # }

}