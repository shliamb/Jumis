# database/memories.py
from typing import Optional
from logs.set_logger import set_logger
logger = set_logger(name="db")
from database import db


class DBMemories():
    def __init__(self):
        self.db = db
        self.fact_categories: list[dict] = []



    ###### CATEGORY #######

    async def init(self):
        """Вызывается один раз при старте приложения"""
        await self._refresh_categories()
        return self


    async def _refresh_categories(self) -> list[dict]:
        """Вытягивает актуальные категории из базы в self.fact_categories"""
        query = "SELECT * FROM facts_categories ORDER BY id ASC;"
        records = await self.db.fetch(query)
        self.fact_categories = [dict(rec) for rec in records] if records else []


    async def add_category(self, category_data: dict) -> bool:
        """Добавление или обновление категории из словаря"""

        if not category_data.get("name"):
            logger.error("Category data must contain a 'name' field.")
            return False

        category_dict = category_data.copy()

        # Очищаем имя категории до чистого snake_case
        if isinstance(category_dict.get("name"), str):
            category_dict["name"] = category_dict["name"].strip().lower().replace(" ", "_")

        keys = list(category_dict.keys())
        values = list(category_dict.values())
        
        columns = ", ".join(keys)
        placeholders = ", ".join([f"${i+1}" for i in range(len(values))])

        query = f"""
            INSERT INTO facts_categories ({columns})
            VALUES ({placeholders})
            ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description;
        """
        try:
            await self.db.execute(query, *values)
            # Сразу обновляем локальный кэш категорий
            await self._refresh_categories()
            return True  # Подтверждаем успешную запись
            
        except Exception as e:
            print(f"Error adding category to DB: {e}")
            logger.error(f"Error adding category to DB: {e}")
            return False


    async def delete_category(self, name: str) -> bool:
        ### Не использую её пока ни где, не удаляю и не обновляю категорию, иначе все связи умрут..
        """Удаляет категорию. Факты с этой категорией автоматически сбросятся в DEFAULT ('fact')."""
        clean_name = name.strip().lower()
        query = "DELETE FROM facts_categories WHERE name = $1;"
        try:
            await self.db.execute(query, clean_name)
            # Сразу обновляем локальный кэш категорий
            await self._refresh_categories()
            logger.info("Категория '%s' удалена.", clean_name)
            return True
        except Exception as e:
            logger.error("Ошибка удаления категории '%s': %s", clean_name, e)
            return False



    ######## FACTS ##########


    async def add_fact(self, fact_data: dict) -> Optional[int]:
        """Добавить факт и вернуть его id (или None в случае ошибки)."""
        fact_dict = fact_data.copy()

        # Если передали список чисел в embedding, переводим в формат строки pgvector
        if 'embedding' in fact_dict and isinstance(fact_dict['embedding'], list):
            fact_dict['embedding'] = str(fact_dict['embedding'])

        keys = list(fact_dict.keys())
        values = list(fact_dict.values())

        columns = ", ".join(keys)
        placeholders = ", ".join([f"${i+1}" for i in range(len(values))])

        query = f"INSERT INTO memories ({columns}) VALUES ({placeholders}) RETURNING id"

        try:
            fact_id = await self.db.fetchval(query, *values)
            return fact_id
        except Exception as e:
            logger.error(f"Error adding memories: {e}")
            return None


    async def get_facts_by_category(self, category: str) -> list:
        """Забрать факты категории"""
        query = "SELECT * FROM memories WHERE category = $1 ORDER BY id DESC"
        records = await self.db.fetch(query, category)
        return [dict(rec) for rec in records] if records else []


    async def get_facts_by_user_id(self, user_id: int) -> list:
        """Забрать факты пользователя"""
        query = "SELECT * FROM memories WHERE user_id = $1 ORDER BY id DESC"
        records = await self.db.fetch(query, user_id)
        return [dict(rec) for rec in records] if records else []



    async def search_vectors(
            self, 
            embedding: list, 
            user_id: int = None, 
            category: str = None, 
            limit: int = 10,
            min_similarity: float = 0.75  # <-- ПОРОГ КАЧЕСТВА (0.0 - 1.0)
        ) -> list[dict]:
            """Универсальный векторный поиск с порогом отсечения шума"""
            emb_str = str(embedding)
            
            # 1. Формируем условия фильтрации
            conditions = [f"(1 - (embedding <=> $1::vector)) >= ${2}"]  # $2 — min_similarity
            params = [emb_str, min_similarity]
            
            param_idx = 3
            
            if user_id is not None:
                conditions.append(f"user_id = ${param_idx}")
                params.append(user_id)
                param_idx += 1
                
            if category and category.strip():
                conditions.append(f"category = ${param_idx}")
                params.append(category.strip())
                param_idx += 1
                
            params.append(limit)
            limit_param_idx = param_idx

            where_clause = f"WHERE {' AND '.join(conditions)}"

            query = f"""
                SELECT *, (1 - (embedding <=> $1::vector)) AS similarity
                FROM memories 
                {where_clause}
                ORDER BY embedding <=> $1::vector 
                LIMIT ${limit_param_idx}
            """
            
            records = await self.db.fetch(query, *params)
            return [dict(rec) for rec in records] if records else []



    async def edit_fact(self, fact_data: dict) -> bool:
        """Обновить данные факта"""
        fact_dict = fact_data.copy()
        
        if 'id' not in fact_dict:
            logger.error("No id in fact_data")
            return False
        
        fact_id = fact_dict.pop('id')
        if not fact_dict:
            return False
            
        if 'embedding' in fact_dict and isinstance(fact_dict['embedding'], list):
            fact_dict['embedding'] = str(fact_dict['embedding'])
        
        # Формируем SET и принудительно обновляем updated_at
        set_parts = [f"{key} = ${i+1}" for i, key in enumerate(fact_dict.keys())]
        set_parts.append("updated_at = NOW()")
        
        values = list(fact_dict.values())
        values.append(fact_id)
        
        query = f"""
            UPDATE memories 
            SET {', '.join(set_parts)}
            WHERE id = ${len(values)}
        """
        
        try:
            await self.db.execute(query, *values)
            return True
        except Exception as e:
            logger.error(f"Error updating fact {fact_id}: {e}")
            return False



    async def db_del_fact(self, fact_id: int) -> bool:
        """Удаление факта из памяти по ID. Возвращает True, если факт реально удален."""
        if not fact_id or fact_id <= 0:
            logger.warning("db_del_fact: Invalid fact_id provided: %s", fact_id)
            return False

        query = "DELETE FROM memories WHERE id = $1"
        
        try:
            result = await self.db.execute(query, fact_id)
            
            # Защита: если запись не найдена, asyncpg возвращает "DELETE 0"
            if result and "DELETE 0" in result:
                logger.warning("db_del_fact: Fact #%s not found in database.", fact_id)
                return False
                
            return True
            
        except Exception as e:
            logger.error("Error deleting fact #%s from database: %s", fact_id, e)
            return False
