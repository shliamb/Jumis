#! master/database/deleted_tables_db.py
from logs.set_logger import set_logger
logger = set_logger(name="db")
from database import db
import asyncio




# Fast delete ALL Tabs:
async def drop_all_tables_and_reset_schema() -> bool:
    """ Быстрое удаление таблиц базы данных """
    querys = [
        "DROP SCHEMA public CASCADE;",
        "CREATE SCHEMA public;",
        "GRANT ALL ON SCHEMA public TO PUBLIC;"   
    ]

    try:
        for query in querys:
            await db.execute(query)
            await asyncio.sleep(1)
        return True
    
    except Exception as e:
        logger.error(f"Error drop all tables and reset schema: {e}")
        return False