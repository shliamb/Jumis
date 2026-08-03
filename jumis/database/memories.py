# database/memories.py
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

    async def add_fact(self, fact_data: dict) -> bool:
        """Добавить факт"""
        fact_dict = fact_data.copy()
        
        # Если передали список чисел в embedding, переводим в формат строки pgvector
        if 'embedding' in fact_dict and isinstance(fact_dict['embedding'], list):
            fact_dict['embedding'] = str(fact_dict['embedding'])
            
        keys = list(fact_dict.keys())
        values = list(fact_dict.values())
        
        columns = ", ".join(keys)
        placeholders = ", ".join([f"${i+1}" for i in range(len(values))])
        
        query = f"INSERT INTO memories ({columns}) VALUES ({placeholders})"
        
        try:
            await self.db.execute(query, *values)
            return True
        except Exception as e:
            logger.error(f"Error adding memories: {e}")
            return False


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



    # async def search_vectors(
    #         self, 
    #         embedding: list, 
    #         user_id: int = None, 
    #         category: str = None, 
    #         limit: int = 10
    #     ) -> list[dict]:
    #         """Универсальный векторный поиск с опциональной фильтрацией по user_id и/или category"""
    #         emb_str = str(embedding)
    #         conditions = []
    #         params = [emb_str]  # $1 — это всегда вектор
            
    #         param_idx = 2
            
    #         if user_id is not None:
    #             conditions.append(f"user_id = ${param_idx}")
    #             params.append(user_id)
    #             param_idx += 1
                
    #         if category and category.strip():
    #             conditions.append(f"category = ${param_idx}")
    #             params.append(category.strip())
    #             param_idx += 1
                
    #         params.append(limit)
    #         limit_param_idx = param_idx

    #         # Собираем WHERE только если есть фильтры. Если их нет — ищет ВООБЩЕ ПО ВСЕЙ БАЗЕ
    #         where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    #         query = f"""
    #             SELECT *, (1 - (embedding <=> $1::vector)) AS similarity
    #             FROM memories 
    #             {where_clause}
    #             ORDER BY embedding <=> $1::vector 
    #             LIMIT ${limit_param_idx}
    #         """
            
    #         records = await self.db.fetch(query, *params)
    #         return [dict(rec) for rec in records] if records else []



    # async def get_vector_by_user_id(self, user_id: int, embedding: list, n: int = 5) -> list:
    #     """Забрать факты пользователя, близкие по вектору (первые n штук)"""
    #     emb_str = str(embedding)
        
    #     # 1 - (embedding <=> $2::vector) дает значение сходства (Similarity) от 0 до 1
    #     query = """
    #         SELECT *, (1 - (embedding <=> $2::vector)) AS similarity
    #         FROM memories 
    #         WHERE user_id = $1 
    #         ORDER BY embedding <=> $2::vector 
    #         LIMIT $3
    #     """
    #     records = await self.db.fetch(query, user_id, emb_str, n)
    #     return [dict(rec) for rec in records] if records else []


    # async def get_vector_by_category(self, category: str, embedding: list, n: int = 10) -> list:
    #     """Забрать факты категории, близкие по вектору (первые n штук)"""
    #     emb_str = str(embedding)
        
    #     query = """
    #         SELECT *, (1 - (embedding <=> $2::vector)) AS similarity
    #         FROM memories 
    #         WHERE category = $1 
    #         ORDER BY embedding <=> $2::vector 
    #         LIMIT $3
    #     """
    #     records = await self.db.fetch(query, category, emb_str, n)
    #     return [dict(rec) for rec in records] if records else []


    # async def get_vector_by_jumis(self, embedding: list, n: int = 10) -> list:
    #     """Забрать глобальные факты Jumis (user_id IS NULL), близкие по вектору"""
    #     emb_str = str(embedding)
        
    #     query = """
    #         SELECT *, (1 - (embedding <=> $1::vector)) AS similarity
    #         FROM memories 
    #         WHERE user_id IS NULL 
    #         ORDER BY embedding <=> $1::vector 
    #         LIMIT $2
    #     """
    #     records = await self.db.fetch(query, emb_str, n)
    #     return [dict(rec) for rec in records] if records else []


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
