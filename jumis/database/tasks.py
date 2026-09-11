# database/tasks.py
from typing import Any, Dict, List, Optional
from logs.set_logger import set_logger
logger = set_logger(name="db")
from database import db



class DBTasks:

    def __init__(self):
        self.db = db


    async def db_add_task(self, task_data: dict) -> Optional[int]:
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


    async def db_del_task(self, id_task: int) -> bool:
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


    async def db_search_tasks(
        self, 
        task_id: Optional[int] = None, 
        status: Optional[str] = None, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Получение списка задач с фильтрацией по ID задачи или по статусу.
        
        Args:
            task_id (Optional[int]): Уникальный ID конкретной задачи.
            status (Optional[str]): Статус ('pending', 'running', 'completed', 'cancelled').
            limit (int): Максимальное количество записей (по умолчанию 20).
        """
        conditions = []
        params = []

        # 1. Если передан конкретный ID — ищем только его
        if task_id is not None:
            params.append(task_id)
            conditions.append(f"id = ${len(params)}")
        # 2. Если ID не передан, но указан статус
        elif status is not None:
            params.append(status)
            conditions.append(f"status = ${len(params)}")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Добавляем limit последним параметром
        params.append(limit)
        limit_clause = f"LIMIT ${len(params)}"

        query = f"""
            SELECT 
                id, tg_id, task_type, title, agent_instruction, 
                scheduled_at, cron_expression, repeat_interval_minutes, 
                requires_ack, is_ack_received, max_nag_attempts, current_nag_count, 
                status, created_at, updated_at
            FROM scheduled_tasks 
            {where_clause} 
            ORDER BY scheduled_at ASC 
            {limit_clause};
        """

        try:
            records = await self.db.fetch(query, *params)
            return [dict(rec) for rec in records] if records else []
        except Exception as e:
            logger.error(f"[DB Tasks] Ошибка получения задач (id={task_id}, status={status}): {e}", exc_info=True)
            return []


    async def get_pending_task(self) -> Dict[str, Any]:
        """
        Запрашивает из БД самую ближайшую по времени задачу в статусе 'pending'.

        Returns:
            Dict[str, Any]: Словарь с полями задачи или пустой словарь, если задач нет или произошла ошибка.
        """
        query = """
            SELECT 
                id, tg_id, task_type, title, agent_instruction, 
                scheduled_at, cron_expression, repeat_interval_minutes, 
                requires_ack, is_ack_received, max_nag_attempts, current_nag_count
            FROM scheduled_tasks 
            WHERE status = 'pending' 
            ORDER BY scheduled_at ASC 
            LIMIT 1;
        """
        try:
            record = await self.db.fetchrow(query)
            return dict(record) if record else {}
        except Exception as e:
            logger.error(f"[DB Tasks] Ошибка при получении активной задачи: {e}", exc_info=True)
            return {}

