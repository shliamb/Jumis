# jumis/jsonbackup/json_backup.py
import json
import os
import datetime
from uuid import UUID
from decimal import Decimal
from aiogram import types
from aiogram.types import FSInputFile
from logs.set_logger import set_logger
logger = set_logger(name="backup_json")
from config import PATH_JSON
from config import ADMIN_ID




class JsonBackup():
    """ Бекап таблиц ввиде Json """

    def __init__(
        self,
        bot,
        db_messages,
        db_memory,
        db_users,
        db_tasks
    ):
        self.path_json = PATH_JSON
        self.bot = bot
        self.admin_id = ADMIN_ID
        self.db_messages = db_messages
        self.db_memory = db_memory
        self.db_users = db_users
        self.db_tasks = db_tasks


    def _json_serializer(self, obj):
        """Сериализация специфичных типов (datetime, UUID, Decimal), которые стандартный JSON не понимает"""
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        if isinstance(obj, datetime.date):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, Decimal):
            return float(obj)
        raise TypeError(f"Type {type(obj)} is not JSON serializable")


    async def save_json_file(self, data: list[dict], name_action: str) -> str | bool:
        """Сохраняем данные в JSON файл"""
        try:
            # Проверяем и создаём папку
            os.makedirs(self.path_json, exist_ok=True)

            # Создаем имя файла с текущей датой-временем
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

            # Формируем корректный путь. 
            filename = f"{name_action}_{timestamp}.json"
            filepath = os.path.join(os.getcwd(), self.path_json, filename)
            
            # Записываем в файл.
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(
                    data, 
                    f, 
                    ensure_ascii=False, 
                    indent=4,
                    default=self._json_serializer
                )
            return filepath

        except Exception as e:
            logger.error(f"Error save file to JSON ({name_action}): {e}")
            return False


    async def _push_file(self, name_action: str, filepath: str) -> bool:
        """ Передает файл JSON админу в Aiogram"""
        try:
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:

                # Файлы отправляются через send_document, а не send_message
                await self.bot.send_document(
                    chat_id=self.admin_id,
                    document=FSInputFile(filepath),
                    caption=f"📁 JSON Backup: {name_action}"
                )
                return True
            else:
                logger.warning(f"File {filepath} is empty or not found.")
                return False
        except Exception as e:
            logger.error(f"Error pushing file {filepath} to admin: {e}")
            return False


    async def _process_table_backup(self, data: list[dict], name_action: str):
        """Вспомогательный метод (DRY), чтобы не дублировать логику сохранения и отправки"""
        if not data:
            logger.warning(f"No data for {name_action}, skipping.")
            return
        
        filepath = await self.save_json_file(data, name_action)
        if filepath:
            await self._push_file(name_action, filepath)



    # ==========================================
    # Методы получения данных (с номерами для FK)
    # ==========================================


    async def get_users_cat(self):
        data = self.db_users.users_categories
        await self._process_table_backup(data, "01_user_categories")


    async def get_facts_cat(self):
        # Обычно категории фактов лежат в db_memory
        data = self.db_memory.fact_categories
        await self._process_table_backup(data, "02_facts_categories")


    async def get_users(self):
        data = await self.db_users.get_users()
        await self._process_table_backup(data, "03_users")


    async def get_memories(self):
        data = await self.db_memory.get_all_facts()
        await self._process_table_backup(data, "04_facts")


    async def get_messages(self):
        data = await self.db_messages.get_all_messages()
        await self._process_table_backup(data, "05_messages")


    async def get_tasks(self):
        data = await self.db_tasks.get_tasks()
        await self._process_table_backup(data, "06_tasks")


    async def db_to_json(self):
        """Запускает сохранение в JSON всех таблиц DB по очереди"""
        logger.info("Starting JSON Backup process...")
        
        # Строгий порядок для соблюдения зависимостей (Foreign Keys)
        await self.get_users_cat()
        await self.get_facts_cat()
        await self.get_users()
        await self.get_memories()
        await self.get_messages()
        await self.get_tasks()
        
        logger.info("JSON Backup process completed.")
        
        # Финальное оповещение
        try:
            await self.bot.send_message(
                chat_id=self.admin_id, 
                text="✅ Бэкап БД (JSON) успешно сформирован и отправлен."
            )
        except Exception:
            pass




















    # async def save_json_file(self, data: list[dict], name_action: str) -> str | bool:
    #     """Сохраняем данные в JSON файл"""
    #     json_list = []
    #     try:

    #         # Проверяем и создаём папку
    #         os.makedirs(self.path_json, exist_ok=True)

    #         # Создаем имя файла с текущей датой-временем
    #         timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    #         # Безопасно создаём файл
    #         filename = os.path.join(self.path_json, f"{name_action}_{timestamp}.json")
            
    #         # Полный путь к файлу в рабочей директории
    #         filepath = os.path.join(os.getcwd(), filename)

    #         list_data: list[dict] = data.copy() # ?????!!!!! 
    #         keys_need_serialized = ["created_at", "updated_at", "scheduled_at"]

    #         for rec in list_data:
    #             json_dict = {}
    #             for key, value in rec.items():
    #                 if key in keys_need_serialized:
    #                     json_dict[key] = await self._json_serializer(value)
    #                 json_dict[key] = value

    #             json_list.append(json_dict)

            
    #         # Сохраняем с обработкой специальных типов

    #         try:
    #             """ предвижу возможные ошибки, потому лучше try"""
    #             with open(filename, 'w', encoding='utf-8') as f:
    #                 json.dump(
    #                     json_list, 
    #                     f, 
    #                     ensure_ascii=False, 
    #                     indent=4,
    #                     #default=self._json_serializer
    #                 )
    #             return filepath
    #         except:
    #             print("")
    #             logger.error("")
    #             return False

    #     except Exception as e:
    #         logger.error(f"Error save file to JSON: {e}")
    #         return False
