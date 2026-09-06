# jumis/jsonbackup/json_backup.py
import json



class JsonBackup():

    def init(
        self,
        db_messages,
        db_memory,
        db_users,
        db_tasks
    ):
        self.db_messages = db_messages
        self.db_memory = db_memory
        self.db_users = db_users
        self.db_tasks = db_tasks

    """ Бекап таблиц ввиде Json
    
        Сначало заливаются пользователи, потом категории
        пользователей, фактов, а только потом остальное."""


    async def get_users_cat(self):
        data: list[dict] = await self.db_users._refresh_categories()


    async def get_facts_cat(self):
        data: list[dict] = await self.db_messages._refresh_categories()


    async def get_users(self):
        data: list[dict] = await self.db_users.get_users()


    async def get_messages(self):
        data: list[dict] = await self.db_messages.get_all_messages()


    async def get_memories(self):
        data: list[dict] = await self.db_memory.get_all_facts()


    async def get_tasks(self):
        data: list[dict] = await self.db_tasks.get_tasks()
