# jumis/jsonbackup/json_backup.py
import json



class JsonBackup():

    def init(
        self,
        db_messages,
        db_memory,
        db_users,
        tasks
    ):
        self.db_messages = db_messages
        self.db_memory = db_memory
        self.db_users = db_users
        self.tasks = tasks

    """ Бекап таблиц ввиде Json
    
        Сначало заливаются пользователи, потом категории
        пользователей, фактов, а только потом остальное."""


    async def get_users_cat(self):
        data: list[dict] = await self.db_users._refresh_categories()


    async def get_facts_cat(self):
        pass


    async def get_users(self):
        data: list[dict] = await self.db_users.get_users()


    async def get_messages(self):
        data: list[dict] = await self.db_messages.get_all_messages()


    async def get_facts(self): # or name is memories
        data: list[dict] = await self.db_memory._refresh_categories()


    async def get_memories(self):
        data: list[dict] = await self.db_memory.get_all_facts()


    async def get_tasks(self):
        data: list[dict] = await self.tasks.get_all_tasks()