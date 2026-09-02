# database/users.py
from typing import Any, Dict, List, Optional
from logs.set_logger import set_logger
logger = set_logger(name="db")
from database import db




class DBUsers():
    
    def __init__(self):
        self.db = db
        self.users_categories: list[dict] = []


    #######################
    ###### CATEGORY #######
    #######################


    async def init(self):
        """Вызывается один раз при старте приложения"""
        await self._refresh_categories()
        return self


    async def _refresh_categories(self) -> list[dict]:
        """Вытягивает актуальные категории из базы в self.users_categories"""
        query = "SELECT * FROM users_categories ORDER BY id ASC;"
        records = await self.db.fetch(query)
        self.users_categories = [dict(rec) for rec in records] if records else []


    async def get_users(self) -> list[dict]:
        """ Все пользователи """
        query = "SELECT * FROM user ORDER BY id ASC;"
        records = await self.db.fetch(query)
        self.users_categories = [dict(rec) for rec in records] if records else []



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
            INSERT INTO users_categories ({columns})
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
        """Удаляет категорию. Users с этой категорией автоматически сбросятся в DEFAULT ('not_defined')."""
        clean_name = name.strip().lower()
        query = "DELETE FROM users_categories WHERE name = $1;"
        try:
            await self.db.execute(query, clean_name)
            # Сразу обновляем локальный кэш категорий
            await self._refresh_categories()
            logger.info("Категория '%s' удалена.", clean_name)
            return True
        except Exception as e:
            logger.error("Ошибка удаления категории '%s': %s", clean_name, e)
            return False



    #####################
    ####### USERS #######
    #####################


    async def chek_tg_id(self, tg_id: int):
        """ ... """
        query = "INSERT INTO users (tg_id) VALUES ($1) ON CONFLICT (tg_id) DO NOTHING"
        return await self.db.execute(query, tg_id) or False


    async def add_user(self, user_data: dict) -> bool:
        """Добавить пользователя (игнорирует, если tg_id уже существует)."""

        keys = list(user_data.keys())
        values = list(user_data.values())
        
        columns = ", ".join(keys)
        placeholders = ", ".join([f"${i+1}" for i in range(len(values))])
        
        # Добавили ON CONFLICT (tg_id) DO NOTHING
        query = f"""
            INSERT INTO users ({columns}) 
            VALUES ({placeholders}) 
            ON CONFLICT (tg_id) DO NOTHING; -- если такой tg_id в базе есть, то ничего не делает
        """
        
        try:
            await self.db.execute(query, *values)
            return True
        except Exception as e:
            logger.error(f"Error adding user: {e}")
            return False


    async def db_update_user(self, user_data: dict) -> bool:
        """
        Универсальное обновление данных пользователя.
        
        Принимает словарь с ключами БД, например:
        - {'id': 42, 'category': 'client', 'comment': 'Новая заметка'}
        - {'tg_id': 987654321, 'is_blocked': True}
        - {'username': 'alex_dev', 'phone': '+79991112233'}
        """
        if not user_data or not isinstance(user_data, dict):
            logger.error("db_update_user: Empty or invalid user_data provided.")
            return False

        # 1. Делаем копию, чтобы .pop() не портил оригинальный словарь
        data = user_data.copy()

        # 2. Вынимаем идентификаторы (в порядке приоритета: id -> tg_id -> username)
        user_id = data.pop('id', None)
        tg_id = data.pop('tg_id', None)
        username = data.pop('username', None)

        if not any([user_id, tg_id, username]):
            logger.error("db_update_user: No identifier (id, tg_id, or username) provided.")
            return False

        if not data:
            logger.warning("db_update_user: No fields left to update.")
            return False

        # 3. Собираем динамический SET с проверкой векторов и алиасов
        set_parts = []
        values = []
        
        for key, val in data.items():
            param_idx = len(values) + 1

            # Проверка и подготовка эмбеддинга для pgvector
            if key == 'aliases_vector':
                if val is None:
                    set_parts.append(f"aliases_vector = ${param_idx}")
                    values.append(None)
                else:
                    # Приводим list/tuple к строке '[0.1, 0.2, ...]' и кастим в ::vector
                    vec_str = str(list(val)) if isinstance(val, (list, tuple)) else str(val)
                    set_parts.append(f"aliases_vector = ${param_idx}::vector")
                    values.append(vec_str)

            # Проверка и очистка текста алиасов
            elif key == 'aliases':
                clean_aliases = str(val).strip() if val is not None else None
                set_parts.append(f"aliases = ${param_idx}")
                values.append(clean_aliases)

            else:
                set_parts.append(f"{key} = ${param_idx}")
                values.append(val)

        # Автоматически обновляем штамп времени изменения
        set_parts.append("updated_at = NOW()")

        # 4. Определяем плейсхолдер для условия WHERE
        where_idx = len(values) + 1
        if user_id:
            where_clause = f"id = ${where_idx}"
            values.append(user_id)
        elif tg_id:
            where_clause = f"tg_id = ${where_idx}"
            values.append(tg_id)
        else:
            where_clause = f"username = ${where_idx}"
            values.append(str(username).strip().lstrip("@"))

        query = f"""
            UPDATE users 
            SET {', '.join(set_parts)}
            WHERE {where_clause}
        """

        try:
            result = await self.db.execute(query, *values)
            
            if result and "UPDATE 0" in result:
                target = user_id or tg_id or username
                logger.warning("db_update_user: User not found in DB (target: %s).", target)
                return False
                
            return True

        except Exception as e:
            target = user_id or tg_id or username
            logger.error("Error updating user (target: %s): %s", target, e)
            return False


    async def search_users(
        self,
        user_id: Optional[int] = None,
        tg_id: Optional[int] = None,
        category: Optional[str] = None,
        query: Optional[str] = None,
        vector: Optional[list] = None,
        limit: int = 5,
        min_similarity: float = 0.75,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Универсальный поиск пользователей с каскадной логикой приоритетов.
        """
        # Поля выборки без векторного столбца
        fields = """
            id, tg_id, username, full_name, phone, category, 
            comment, summary, aliases, is_admin, is_blocked, 
            is_whitelisted, is_bot, lang_code, model_default, 
            model_cheap, model_smart, created_at, updated_at
        """

        # -----------------------------------------------------------------
        # ШАГ 1. ТОЧНЫЙ ПОИСК ПО ИДЕНТИФИКАТОРАМ (Самый быстрый путь)
        # -----------------------------------------------------------------
        if user_id is not None or tg_id is not None:
            conditions = []
            params = []
            idx = 1

            if user_id is not None:
                conditions.append(f"id = ${idx}")
                params.append(user_id)
                idx += 1
            elif tg_id is not None:
                conditions.append(f"tg_id = ${idx}")
                params.append(tg_id)
                idx += 1

            if category and category != "not_defined":
                conditions.append(f"category = ${idx}")
                params.append(category)

            sql = f"SELECT {fields} FROM users WHERE {' AND '.join(conditions)} LIMIT 1;"
            
            try:
                records = await self.db.fetch(sql, *params)
                return [dict(r) for r in records] if records else []
            except Exception as e:
                logger.error(f"[DBUsers] Ошибка точечного поиска пользователей: {e}")
                return []

        # -----------------------------------------------------------------
        # ШАГ 2. ВЕКТОРНЫЙ ПОИСК (Если передан вектор эмбеддинга)
        # -----------------------------------------------------------------
        if vector is not None:
            emb_str = str(vector)
            conditions = [
                "aliases_vector IS NOT NULL",
                "(1 - (aliases_vector <=> $1::vector)) >= $2"
            ]
            params = [emb_str, min_similarity]
            idx = 3

            if category and category != "not_defined":
                conditions.append(f"category = ${idx}")
                params.append(category)
                idx += 1

            params.append(limit)
            limit_idx = idx

            sql = f"""
                SELECT {fields}, (1 - (aliases_vector <=> $1::vector)) AS similarity
                FROM users
                WHERE {' AND '.join(conditions)}
                ORDER BY aliases_vector <=> $1::vector ASC
                LIMIT ${limit_idx};
            """
            
            try:
                records = await self.db.fetch(sql, *params)
                if records:
                    return [dict(r) for r in records]
            except Exception as e:
                logger.error(f"[DBUsers] Ошибка векторного поиска пользователей: {e}")

        # -----------------------------------------------------------------
        # ШАГ 3. ТЕКСТОВЫЙ ПОИСК ПО ПОЛЯМ (Фоллбэк для query без вектора)
        # -----------------------------------------------------------------
        conditions = []
        params = []
        idx = 1

        if query and str(query).strip():
            clean_q = f"%{str(query).strip().lstrip('@')}%"
            conditions.append(
                f"(username ILIKE ${idx} OR full_name ILIKE ${idx} OR aliases ILIKE ${idx} OR comment ILIKE ${idx})"
            )
            params.append(clean_q)
            idx += 1

        if category and category != "not_defined":
            conditions.append(f"category = ${idx}")
            params.append(category)
            idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        
        params.append(limit)
        limit_idx = idx

        sql = f"""
            SELECT {fields}
            FROM users
            {where_clause}
            ORDER BY id DESC
            LIMIT ${limit_idx};
        """

        try:
            records = await self.db.fetch(sql, *params)
            return [dict(r) for r in records] if records else []
        except Exception as e:
            logger.error(f"[DBUsers] Ошибка текстового/общего поиска пользователей: {e}")
            return []


