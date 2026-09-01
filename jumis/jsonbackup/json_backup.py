# jumis/jsonbackup/json_backup.py
import json



class JsonBackup():

    def init(
        self,
        db_messages,
        db_memory,
        db_users
    ):
        self.db_messages = db_messages
        self.db_memory = db_memory
        self.db_users = db_users

    """ Бекап таблиц ввиде Json
    
        Сначало заливаются пользователи, потом категории
        пользователей, фактов, а только потом остальное."""


    async def get_users_cat(self):
        pass


    async def get_facts_cat(self):
        pass


    async def get_users(self):
        pass


    async def get_messages(self):
        pass


    async def get_facts(self):
        pass


    async def get_memories(self):
        pass