# database/memories.py
from logs.set_logger import set_logger
logger = set_logger(name="db")
from database import db




async def add_fact(fact_data: dict) -> bool:
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
        await db.execute(query, *values)
        return True
    except Exception as e:
        logger.error(f"Error adding memories: {e}")
        return False


async def get_facts_by_category(category: str) -> list:
    """Забрать факты категории"""
    query = "SELECT * FROM memories WHERE category = $1 ORDER BY id DESC"
    records = await db.fetch(query, category)
    return [dict(rec) for rec in records] if records else []


async def get_facts_by_user_id(user_id: int) -> list:
    """Забрать факты пользователя"""
    query = "SELECT * FROM memories WHERE user_id = $1 ORDER BY id DESC"
    records = await db.fetch(query, user_id)
    return [dict(rec) for rec in records] if records else []


async def get_vector_by_user_id(user_id: int, embedding: list, n: int = 5) -> list:
    """Забрать факты пользователя, близкие по вектору (первые n штук)"""
    emb_str = str(embedding)
    
    # 1 - (embedding <=> $2::vector) дает значение сходства (Similarity) от 0 до 1
    query = """
        SELECT *, (1 - (embedding <=> $2::vector)) AS similarity
        FROM memories 
        WHERE user_id = $1 
        ORDER BY embedding <=> $2::vector 
        LIMIT $3
    """
    records = await db.fetch(query, user_id, emb_str, n)
    return [dict(rec) for rec in records] if records else []


async def get_vector_by_category(category: str, embedding: list, n: int = 5) -> list:
    """Забрать факты категории, близкие по вектору (первые n штук)"""
    emb_str = str(embedding)
    
    query = """
        SELECT *, (1 - (embedding <=> $2::vector)) AS similarity
        FROM memories 
        WHERE category = $1 
        ORDER BY embedding <=> $2::vector 
        LIMIT $3
    """
    records = await db.fetch(query, category, emb_str, n)
    return [dict(rec) for rec in records] if records else []


async def get_vector_by_jumis(embedding: list, n: int = 5) -> list:
    """Забрать глобальные факты Jumis (user_id IS NULL), близкие по вектору"""
    emb_str = str(embedding)
    
    query = """
        SELECT *, (1 - (embedding <=> $1::vector)) AS similarity
        FROM memories 
        WHERE user_id IS NULL 
        ORDER BY embedding <=> $1::vector 
        LIMIT $2
    """
    records = await db.fetch(query, emb_str, n)
    return [dict(rec) for rec in records] if records else []


async def edit_fact(fact_data: dict) -> bool:
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
        await db.execute(query, *values)
        return True
    except Exception as e:
        logger.error(f"Error updating fact {fact_id}: {e}")
        return False
