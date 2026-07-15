# database/common.py
from config import HOST, PORT, USER_DB, PASSWORD_DB, DB_NAME, TIME_ZONE
from logs.set_logger import set_logger
logger = set_logger(name="db")
import asyncpg





class Database:
    """ Work to data in DB """
    def __init__(self):
        self.pool = None
    

    async def connect(self):
        """ Создаем пул при запуске бота """
        if self.pool is None:
            self.pool = await asyncpg.create_pool(
                host=HOST,
                port=PORT,
                database=DB_NAME,
                user=USER_DB,
                password=PASSWORD_DB,
                min_size=5, # 5 соединений всегда готовы
                max_size=50, # максимум 50 одновременных
                max_queries=50000, # после 50к запросов - пересоздать соединение
                timeout=30, # ждать свободное соединение 30 секунд
                max_inactive_connection_lifetime=300,  # закрывать неиспользуемые через 5 мин
                server_settings={'timezone': TIME_ZONE}
            )
            logger.info("Database pool created")
            print(await self.get_pool_stats())

        return self.pool


    async def close(self):
        """ Закрываем пул при остановке """
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Database pool closed")
            stat = await self.get_pool_stats()
            print(f"Pool is closed {stat}")


    async def _ensure_connected(self):
        """Внутренний метод: убедиться что подключены"""
        if self.pool is None:
            await self.connect()


    async def get_pool_stats(self):
        """Получить статистику пула"""
        if not self.pool:
            return "Пул не создан"
        
        total = self.pool.get_size()      # всего соединений
        idle = self.pool.get_idle_size()  # свободных сейчас
        used = total - idle               # занятых
        #print(f"Total: {total}, Idle: {idle}, Used: {used}")
        return {
            "total": total,
            "used": used,
            "idle": idle,
            "percent_used": (used / total * 100) if total > 0 else 0
        }
        

    async def fetch(self, query, *args) -> list[dict]:
        """Выполнить SELECT и получить все строки"""
        # для: SELECT * FROM ...
        await self._ensure_connected()
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)
    

    async def fetchrow(self, query, *args) -> dict:
        """Выполнить запрос и получить первую строку"""
        # для: SELECT ... LIMIT 1 или INSERT ... RETURNING
        await self._ensure_connected()
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)
    

    async def execute(self, query, *args):
        """Выполнить запрос без возврата данных"""
        # для: INSERT/UPDATE/DELETE без RETURNING
        await self._ensure_connected()
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)


    async def fetchval(self, query, *args) -> str:
        """ Выполнить запрос и получить одно значение (например, RETURNING id)"""
        # для: SELECT id FROM ... или INSERT ... RETURNING id
        await self._ensure_connected()
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)
        

