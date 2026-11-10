# database/memories.py
from typing import Optional
from logs.set_logger import logger
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


    async def get_prompt_sections(self) -> list[dict]:
        """Разделы, которые рендерятся в системный промпт (правила, стили, паспорт)."""
        query = """
            SELECT name, description, prompt_title, prompt_order
            FROM facts_categories
            WHERE in_prompt = TRUE
            ORDER BY prompt_order ASC, id ASC;
        """
        records = await self.db.fetch(query)
        return [dict(rec) for rec in records] if records else []


    async def get_section_facts(self, category: str) -> list[dict]:
        """Живые факты одного раздела — в порядке добавления (новые в конце)."""
        query = """
            SELECT id, content, updated_at
            FROM memories
            WHERE category = $1 AND memory_status = 'live'
            ORDER BY id ASC;
        """
        records = await self.db.fetch(query, category)
        return [dict(rec) for rec in records] if records else []


    async def get_map_rows(self) -> list[dict]:
        """Полки архива: категории без рендера в промпт + число живых фактов."""
        query = """
            SELECT c.name,
                   c.description,
                   count(m.id) FILTER (WHERE m.memory_status = 'live') AS facts
            FROM facts_categories c
            LEFT JOIN memories m ON m.category = c.name
            WHERE c.in_prompt = FALSE
            GROUP BY c.name, c.description
            ORDER BY facts DESC, c.name ASC;
        """
        records = await self.db.fetch(query)
        return [dict(rec) for rec in records] if records else []


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


    async def get_all_facts(self) -> list[dict]:
        """ Все факты/воспоминания """
        query = "SELECT * FROM memories ORDER BY id ASC;"
        records = await self.db.fetch(query)
        return [dict(rec) for rec in records] if records else []



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


    async def get_facts_by_user_id(self, tg_id: int) -> list:
        """Забрать факты пользователя"""
        query = "SELECT * FROM memories WHERE tg_id = $1 ORDER BY id DESC"
        records = await self.db.fetch(query, tg_id)
        return [dict(rec) for rec in records] if records else []



    async def search_vectors(
            self, 
            embedding: list, 
            tg_id: int = None, 
            category: str = None, 
            limit: int = 10,
            min_similarity: float = 0.75,  # <-- ПОРОГ КАЧЕСТВА (0.0 - 1.0)
            include_sections: bool = True
        ) -> list[dict]:
            """Универсальный векторный поиск с порогом отсечения шума.

            Устаревшие факты (`memory_status <> 'live'`) не возвращаются никогда:
            они остаются в базе как история, но не мешают текущим ответам.
            include_sections=False — не искать в разделах, которые и так в промпте.
            """
            emb_str = str(embedding)
            
            # 1. Формируем условия фильтрации
            conditions = [
                "(1 - (embedding <=> $1::vector)) >= $2",  # $2 — min_similarity
                "memory_status = 'live'",
            ]
            params = [emb_str, min_similarity]
            
            param_idx = 3

            if not include_sections:
                conditions.append(
                    "category NOT IN (SELECT name FROM facts_categories WHERE in_prompt = TRUE)"
                )
            
            if tg_id is not None:
                conditions.append(f"tg_id = ${param_idx}")
                params.append(tg_id)
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


    async def get_facts_by_ids(self, fact_ids: list[int]) -> list[dict]:
        """Факты по списку id (для уборки: что именно склеиваем)."""
        if not fact_ids:
            return []
        query = """
            SELECT id, content, category, tg_id, created_at
            FROM memories
            WHERE id = ANY($1::bigint[])
            ORDER BY id ASC;
        """
        records = await self.db.fetch(query, list(fact_ids))
        return [dict(rec) for rec in records] if records else []


    async def fetch_relative_dated(self, days: int) -> list[dict]:
        """Живые факты старше N дней — кандидаты в архив (гниющие «сегодня/завтра»)."""
        query = """
            SELECT id, content, category, created_at
            FROM memories
            WHERE memory_status = 'live'
              AND created_at < NOW() - make_interval(days => $1)
            ORDER BY id ASC;
        """
        records = await self.db.fetch(query, int(days))
        return [dict(rec) for rec in records] if records else []


    async def find_similar_pairs(self, min_similarity: float = 0.92) -> list[dict]:
        """Пары живых фактов, которые почти одно и то же (для уборки памяти).

        Полный self-join по векторам: при 300–500 фактах это дешёвый запрос,
        а HNSW для поиска пар «a < b» не помогает.
        """
        query = """
            SELECT a.id AS left_id,
                   b.id AS right_id,
                   (1 - (a.embedding <=> b.embedding)) AS similarity
            FROM memories a
            JOIN memories b ON a.id < b.id
            WHERE a.memory_status = 'live'
              AND b.memory_status = 'live'
              AND a.embedding IS NOT NULL
              AND b.embedding IS NOT NULL
              AND (1 - (a.embedding <=> b.embedding)) >= $1
            ORDER BY similarity DESC;
        """
        records = await self.db.fetch(query, min_similarity)
        return [dict(rec) for rec in records] if records else []


    async def mark_superseded(self, fact_ids: list[int], target_id: int | None, status: str = "superseded") -> int:
        """Помечает факты устаревшими. Ничего не удаляет — данные остаются в базе."""
        if not fact_ids:
            return 0

        query = """
            UPDATE memories
            SET memory_status = $2,
                superseded_by = $3,
                updated_at = NOW()
            WHERE id = ANY($1::bigint[])
              AND memory_status <> $2
        """
        result = await self.db.execute(query, list(fact_ids), status, target_id)
        # asyncpg: "UPDATE 12"
        try:
            return int(result.split()[1]) if result and result.startswith("UPDATE") else 0
        except (IndexError, ValueError):
            return 0


    async def live_stats(self) -> dict:
        """Сколько памяти живёт сейчас и сколько отложено в историю."""
        query = """
            SELECT count(*) FILTER (WHERE memory_status = 'live') AS live,
                   count(*) FILTER (WHERE memory_status <> 'live') AS archived,
                   count(*) AS total
            FROM memories;
        """
        row = await self.db.fetchrow(query)
        return dict(row) if row else {"live": 0, "archived": 0, "total": 0}



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
