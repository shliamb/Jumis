# database/users.py
from logs.set_logger import set_logger
logger = set_logger(name="db")
from database import db
# import uuid
# import asyncpg




async def add_user(user_data: dict) -> bool:
    """Добавить пользователя"""
    keys = list(user_data.keys())
    values = list(user_data.values())
    
    columns = ", ".join(keys)
    placeholders = ", ".join([f"${i+1}" for i in range(len(values))])
    
    query = f"INSERT INTO users ({columns}) VALUES ({placeholders})"
    
    try:
        await db.execute(query, *values)
        return True
    except Exception as e:
        logger.error(f"Error adding user: {e}")
        return False


# async def db_get_user(
#     user_id: int | None = None, 
#     tg_id: int | None = None, 
#     username: str | None = None
# ) -> dict:
#     """Единый поиск пользователя в базе по id, tg_id или username."""
#     if user_id:
#         query = "SELECT * FROM users WHERE id = $1"
#         param = user_id
#     elif tg_id:
#         query = "SELECT * FROM users WHERE tg_id = $1"
#         param = tg_id
#     elif username:
#         clean_username = username.strip().lstrip("@")
#         query = "SELECT * FROM users WHERE username = $1"
#         param = clean_username
#     else:
#         return {}

#     record = await db.fetchrow(query, param)
#     return dict(record) if record else {}



async def db_get_user(
    user_id: int | str | None = None, 
    tg_id: int | str | None = None, 
    username: str | None = None
) -> dict:
    """Единый поиск пользователя в базе по id, tg_id или username."""
    query = None
    param = None

    try:
        # 1. Поиск по ID в базе (проверяем именно на None, а не на truthy)
        if user_id is not None and str(user_id).strip():
            query = "SELECT * FROM users WHERE id = $1"
            param = int(user_id)

        # 2. Поиск по Telegram ID
        elif tg_id is not None and str(tg_id).strip():
            query = "SELECT * FROM users WHERE tg_id = $1"
            param = int(tg_id)

        # 3. Поиск по username
        elif username and str(username).strip():
            query = "SELECT * FROM users WHERE username = $1"
            param = str(username).strip().lstrip("@")

        # Если ни один параметр не передан — выходим
        if not query:
            logger.warning("db_get_user called with no valid parameters.")
            return {}

        record = await db.fetchrow(query, param)
        return dict(record) if record else {}

    except ValueError as e:
        logger.error("db_get_user type conversion error: %s", e)
        return {}
    except Exception as e:
        logger.error("db_get_user database error: %s", e)
        return {}




async def get_all_users() -> list[dict]:
    """Забрать данные всех пользователей."""
    query = "SELECT * FROM users ORDER BY id DESC"
    records = await db.fetch(query)
    return [dict(rec) for rec in records]


async def get_users_count() -> int:
    """Получить общее количество пользователей"""
    query = "SELECT COUNT(*) FROM users"
    return await db.fetchval(query)



async def db_update_user(user_data: dict) -> bool:
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

    # 1. Делаем копию, чтобы .pop() не портил оригинальный словарь в месте вызова
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

    # 3. Собираем динамический SET через items() для гарантии порядка ключей и значений
    set_parts = []
    values = []
    
    for idx, (key, val) in enumerate(data.items(), start=1):
        set_parts.append(f"{key} = ${idx}")
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
        # Зачищаем @ если забыли срезать ранее
        where_clause = f"username = ${where_idx}"
        values.append(str(username).strip().lstrip("@"))

    query = f"""
        UPDATE users 
        SET {', '.join(set_parts)}
        WHERE {where_clause}
    """

    try:
        result = await db.execute(query, *values)
        
        # asyncpg возвращает строку вида "UPDATE 1" или "UPDATE 0"
        if result and "UPDATE 0" in result:
            target = user_id or tg_id or username
            logger.warning("db_update_user: User not found in DB (target: %s).", target)
            return False
            
        return True

    except Exception as e:
        target = user_id or tg_id or username
        logger.error("Error updating user (target: %s): %s", target, e)
        return False


# async def edit_user_tg_id(user_data: dict) -> bool:
#     """Обновить данные user (tg_id обязателен)"""
    
#     if 'tg_id' not in user_data:
#         logger.error("No tg_id in user_data")
#         return False
    
#     tg_id = user_data.pop('tg_id')  # вынимаем tg_id
#     if not user_data:  # если кроме tg_id ничего нет
#         return False
    
#     # Формируем SET
#     set_parts = [f"{key} = ${i+1}" for i, key in enumerate(user_data.keys())]
#     values = list(user_data.values())
#     values.append(tg_id)  # tg_id для WHERE в конце
    
#     query = f"""
#         UPDATE users 
#         SET {', '.join(set_parts)}
#         WHERE tg_id = ${len(values)}
#     """
    
#     try:
#         await db.execute(query, *values)
#         return True
#     except Exception as e:
#         logger.error(f"Error updating user {tg_id}: {e}")
#         return False


# async def edit_user_id(user_data: dict) -> bool:
#     """Обновить данные user (id обязателен)"""
    
#     if 'id' not in user_data:
#         logger.error("No id in user_data")
#         return False
    
#     id = user_data.pop('id')  # вынимаем id
#     if not user_data:  # если кроме id ничего нет
#         return False
    
#     # Формируем SET
#     set_parts = [f"{key} = ${i+1}" for i, key in enumerate(user_data.keys())]
#     values = list(user_data.values())
#     values.append(id)  # id для WHERE в конце
    
#     query = f"""
#         UPDATE users 
#         SET {', '.join(set_parts)}
#         WHERE id = ${len(values)}
#     """
    
#     try:
#         await db.execute(query, *values)
#         return True
#     except Exception as e:
#         logger.error(f"Error updating user {id}: {e}")
#         return False


async def get_user_by_phone(phone: str) -> dict:
    """Найти пользователя по телефону"""
    query = "SELECT * FROM users WHERE phone = $1"
    record = await db.fetchrow(query, phone)
    if record:
        return dict(record)
    return {}



async def get_user_by_telegram_name(telegram_name: str) -> dict:
    """Найти пользователя по telegram name"""
    query = "SELECT * FROM users WHERE username = $1"
    record = await db.fetchrow(query, telegram_name)
    if record:
        return dict(record)
    return {}

