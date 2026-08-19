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
        В data обязательно должен быть 'sender_id' (ID чата/пользователя).
        """
        if "sender_id" not in data or data["sender_id"] is None:
            logger.error("[DBMessages] Ошибка: sender_id отсутствует в data.")
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
            start_id: int, 
            end_id: int
        ) -> List[Dict[str, Any]]:
            """
            Возвращает хронологический срез сообщений по диапазону ID (включительно).
            """
            if start_id > end_id:
                start_id, end_id = end_id, start_id

            # Исключаем поле embedding, чтобы не гонять тяжелые векторы в память
            query = """
                SELECT id, chat_id, sender_id, recipient_id, tg_msg_id, 
                    direction, content, msg_type, created_at
                FROM messages
                WHERE id >= $1 AND id <= $2
                ORDER BY id ASC;
            """
            try:
                records = await self.db.fetch(query, start_id, end_id)
                return [dict(r) for r in records]
            except Exception as e:
                logger.error(f"[DBMessages] Ошибка получения диапазона [{start_id}..{end_id}]: {e}")
                return []


    async def search_similar_messages(
            self, 
            embedding: list, 
            chat_id: int = None, 
            limit: int = 5,
            min_similarity: float = 0.75
        ) -> list[dict]:
            """
            Векторный поиск по сообщениям диалога с порогом отсечения шума.
            """
            emb_str = str(embedding)
            
            # 1. Базовые условия фильтрации ($1 — вектор, $2 — минимальная схожесть)
            conditions = [
                "is_embedded = TRUE",
                "(1 - (embedding <=> $1::vector)) >= $2"
            ]
            params = [emb_str, min_similarity]
            param_idx = 3
            
            # Динамический фильтр по чату
            if chat_id is not None:
                conditions.append(f"chat_id = ${param_idx}")
                params.append(chat_id)
                param_idx += 1
                
            params.append(limit)
            limit_param_idx = param_idx

            where_clause = f"WHERE {' AND '.join(conditions)}"

            # 2. Формируем SQL запрос (выбираем всё кроме самого столбца embedding)
            query = f"""
                SELECT id, chat_id, sender_id, recipient_id, tg_msg_id, 
                    direction, content, msg_type, created_at,
                    (1 - (embedding <=> $1::vector)) AS similarity
                FROM messages
                {where_clause}
                ORDER BY embedding <=> $1::vector ASC
                LIMIT ${limit_param_idx}
            """
            
            try:
                records = await self.db.fetch(query, *params)
                return [dict(rec) for rec in records] if records else []
            except Exception as e:
                logger.error(f"[DBMessages] Ошибка векторного поиска (chat_id={chat_id}): {e}")
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