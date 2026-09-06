# database/tasks.py
from typing import Any, Dict, List, Optional
from logs.set_logger import set_logger
logger = set_logger(name="db")
from database import db



class DBTasks:

    def __init__(self):
        self.db = db


    async def get_tasks(self) -> List[Dict[str, Any]]:
        """ Получение вообще всех тасок (например, для админки) """
        query = "SELECT * FROM scheduled_tasks ORDER BY id ASC;"
        try:
            records = await self.db.fetch(query)
            return [dict(rec) for rec in records] if records else []
        except Exception as e:
            logger.error(f"Error fetching all tasks: {e}")
            return []


    async def get_task_by_id(self, id_task: int) -> Optional[Dict[str, Any]]:
        """ Получить одну задачу по её ID """
        if not id_task:
            return None
        
        query = "SELECT * FROM scheduled_tasks WHERE id = $1;"
        try:
            record = await self.db.fetchrow(query, id_task)
            return dict(record) if record else None
        except Exception as e:
            logger.error(f"Error fetching task by id {id_task}: {e}")
            return None


    async def get_due_tasks(self) -> List[Dict[str, Any]]:
        """ 
        Запрос для фонового демона: выбирает 'pending' задачи, 
        время выполнения которых уже наступило (использует индекс idx_tasks_execution).
        """
        query = """
            SELECT * FROM scheduled_tasks 
            WHERE status = 'pending' AND scheduled_at <= NOW() 
            ORDER BY scheduled_at ASC;
        """
        try:
            records = await self.db.fetch(query)
            return [dict(rec) for rec in records] if records else []
        except Exception as e:
            logger.error(f"Error fetching due tasks: {e}")
            return []


    async def add_task(self, task_data: dict) -> Optional[int]:
        """ 
        Добавить задачу. 
        Возвращает ID созданной задачи (int) при успехе, иначе None.
        """
        if not task_data or not isinstance(task_data, dict):
            logger.error("add_task: task_data is empty or invalid.")
            return None

        keys = list(task_data.keys())
        values = list(task_data.values())
        
        columns = ", ".join(keys)
        placeholders = ", ".join([f"${i+1}" for i in range(len(values))])
        
        query = f"""
            INSERT INTO scheduled_tasks ({columns}) 
            VALUES ({placeholders})
            RETURNING id;
        """
        
        try:
            # Используем fetchval, чтобы сразу забрать возвращенный ID
            task_id = await self.db.fetchval(query, *values)
            return task_id
        except Exception as e:
            logger.error(f"Error adding task: {e}")
            return None


    async def del_task(self, id_task: int) -> bool:
        """ Удаление таски по ID """
        if not id_task:
            logger.error("del_task: id_task was not provided.")
            return False

        query = "DELETE FROM scheduled_tasks WHERE id = $1;"
        try:
            await self.db.execute(query, id_task)
            return True
        except Exception as e:
            logger.error(f"Error deleting task {id_task}: {e}")
            return False


    async def db_update_task(self, task_data: dict) -> bool:
        """ Динамическое обновление полей задачи по ее ID """
        if not task_data or not isinstance(task_data, dict):
            logger.error("db_update_task: Empty or invalid task_data provided.")
            return False

        # 1. Делаем копию, чтобы pop() не изменял передаваемый словарь
        data = task_data.copy()

        # 2. Вынимаем идентификатор
        id_task = data.pop('id', None)

        if id_task is None:
            logger.error("db_update_task: No 'id' field provided in task_data.")
            return False

        if not data:
            logger.warning(f"db_update_task: No fields to update for task {id_task}.")
            return False

        # 3. Собираем динамический SET
        set_parts = []
        values = []
        
        for key, val in data.items():
            param_idx = len(values) + 1
            set_parts.append(f"{key} = ${param_idx}")
            values.append(val)

        # Автоматически обновляем updated_at
        set_parts.append("updated_at = NOW()")

        # 4. Условие WHERE
        where_idx = len(values) + 1
        where_clause = f"id = ${where_idx}"
        values.append(id_task)

        query = f"""
            UPDATE scheduled_tasks 
            SET {', '.join(set_parts)}
            WHERE {where_clause}
        """

        try:
            result = await self.db.execute(query, *values)
            
            if result and "UPDATE 0" in result:
                logger.warning(f"db_update_task: Task ID {id_task} not found in DB.")
                return False
                
            return True

        except Exception as e:
            logger.error(f"Error updating task {id_task}: {e}")
            return False
