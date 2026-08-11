# database/messages.py
from typing import List, Dict, Any, Optional
from logs.set_logger import set_logger
logger = set_logger(name="db")
from database import db


class DBMessages():
    def __init__(self):
        self.db = db


    async def add_message(self, data: dict) -> Optional[int] | None:
        """
        Сохраняет сообщение в базу.
        В data обязательно должен быть 'tg_id' (ID чата/пользователя).
        """
        if "tg_id" not in data or data["tg_id"] is None:
            logger.error("[DBMessages] Ошибка: tg_id отсутствует в data.")
            return None

        data_dict = data.copy()

        # Форматируем вектор под каст $N::vector для pgvector
        has_embedding = False
        if "embedding" in data_dict and isinstance(data_dict["embedding"], list):
            data_dict["embedding"] = str(data_dict["embedding"])
            has_embedding = True

        keys = list(data_dict.keys())
        values = list(data_dict.values())

        placeholders = []
        for i, key in enumerate(keys):
            idx = i + 1
            if key == "embedding" and has_embedding:
                placeholders.append(f"${idx}::vector")
            else:
                placeholders.append(f"${idx}")

        columns_str = ", ".join(keys)
        placeholders_str = ", ".join(placeholders)

        query = f"""
            INSERT INTO messages ({columns_str}) 
            VALUES ({placeholders_str}) 
            RETURNING id
        """

        try:
            message_id = await self.db.fetchval(query, *values)
            return message_id
        except Exception as e:
            logger.error(f"[DBMessages] Ошибка добавления сообщения: {e}", exc_info=True)
            return None


    async def get_messages_context(
        self, 
        tg_id: int, 
        start_id: int = None, 
        end_id: int = None, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Возвращает контекст диалога для агента.
        Если переданы start_id и end_id, возвращает хронологический промежуток сообщений.
        Если границы не переданы, работает как фоллбэк: отдает последние 'limit' сообщений.
        """
        if start_id is not None and end_id is not None:
            # Запрос по конкретному диапазону ID
            query = """
                SELECT id, tg_msg_id, role, content, msg_type, created_at
                FROM messages
                WHERE tg_id = $1 AND id >= $2 AND id <= $3
                ORDER BY id ASC;
            """
            args = (tg_id, start_id, end_id)
        else:
            # Классический запрос последних N сообщений
            query = """
                SELECT id, tg_msg_id, role, content, msg_type, created_at
                FROM (
                    SELECT id, tg_msg_id, role, content, msg_type, created_at
                    FROM messages
                    WHERE tg_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                ) AS sub
                ORDER BY created_at ASC;
            """
            args = (tg_id, limit)

        try:
            records = await self.db.fetch(query, *args)
            return [dict(r) for r in records]
        except Exception as e:
            logger.error(f"[DBMessages] Ошибка получения контекста для tg_id={tg_id}: {e}")
            return []


    async def search_similar_messages(
            self, 
            embedding: list, 
            tg_id: int = None, 
            limit: int = 5,
            min_similarity: float = 0.75 # <-- ПОРОГ КАЧЕСТВА (0.0 - 1.0)
        ) -> list[dict]:
            """
            Векторный поиск по сообщениям диалога с порогом отсечения шума (стиль Builder).
            """
            emb_str = str(embedding)
            
            # 1. Формируем базовые условия фильтрации
            # $1 — вектор, $2 — минимальная схожесть
            conditions = [
                "is_embedded = TRUE",
                "(1 - (embedding <=> $1::vector)) >= $2"
            ]
            params = [emb_str, min_similarity]
            
            param_idx = 3
            
            # Динамически добавляем фильтр по пользователю, если передан
            if tg_id is not None:
                conditions.append(f"tg_id = ${param_idx}")
                params.append(tg_id)
                param_idx += 1
                
            params.append(limit)
            limit_param_idx = param_idx

            where_clause = f"WHERE {' AND '.join(conditions)}"

            # 2. Формируем SQL запрос
            query = f"""
                SELECT *, (1 - (embedding <=> $1::vector)) AS similarity
                FROM messages
                {where_clause}
                ORDER BY embedding <=> $1::vector
                LIMIT ${limit_param_idx}
            """
            
            try:
                records = await self.db.fetch(query, *params)
                return [dict(rec) for rec in records] if records else []
            except Exception as e:
                logger.error(f"[DBMessages] Ошибка векторного поиска (tg_id={tg_id}): {e}")
                return []


    # async def update_content(self, message_id: int, content: str) -> bool:
    #     # Хер знает на кой он нужен, позже удалить нахер..
    #     """Обновление текста сообщения (для воркера транскрибации)."""
    #     query = "UPDATE messages SET content = $1, is_embedded = FALSE WHERE id = $2;"
    #     try:
    #         await self.db.execute(query, content, message_id)
    #         return True
    #     except Exception as e:
    #         logger.error(f"[DBMessages] Ошибка обновления текста id={message_id}: {e}")
    #         return False


    async def update_embedding(self, message_id: int, embedding: List[float]) -> bool:
        """Обновление вектора сообщения."""
        query = "UPDATE messages SET embedding = $1::vector, is_embedded = TRUE WHERE id = $2;"
        try:
            await self.db.execute(query, str(embedding), message_id)
            return True
        except Exception as e:
            logger.error(f"[DBMessages] Ошибка обновления вектора id={message_id}: {e}")
            return False