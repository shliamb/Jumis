# jumis/jsonbackup/json_backup.py
import asyncio
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

    # Маппинг иконок под каждую таблицу
    TABLE_ICONS = {
        "01_user_categories": "🏷️",
        "02_facts_categories": "📂",
        "03_users": "👤",
        "04_facts": "🧠",
        "05_messages": "💬",
        "06_tasks": "📋",
    }


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


    async def save_json_files(
        self, 
        data: list[dict], 
        name_action: str, 
        max_records_per_file: int = 50
    ) -> list[str]:
        """
        Сохраняет данные в JSON файл(ы). 
        Если записей больше max_records_per_file, разбивает на автономные чанки-файлы.
        """
        saved_filepaths = []
        try:
            os.makedirs(self.path_json, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

            # Разбиваем большой список на чанки
            chunks = [
                data[i : i + max_records_per_file] 
                for i in range(0, len(data), max_records_per_file)
            ]
            total_parts = len(chunks)

            for index, chunk in enumerate(chunks, start=1):
                # Если файл всего один — сохраняем без суффикса _partN
                if total_parts == 1:
                    filename = f"{name_action}_{timestamp}.json"
                else:
                    filename = f"{name_action}_{timestamp}_part{index:02d}_of_{total_parts:02d}.json"

                filepath = os.path.join(os.getcwd(), self.path_json, filename)

                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(
                        chunk, 
                        f, 
                        ensure_ascii=False, 
                        indent=4,
                        default=self._json_serializer
                    )
                saved_filepaths.append(filepath)

            return saved_filepaths

        except Exception as e:
            logger.error(f"Error save file to JSON ({name_action}): {e}")
            return []


    async def _push_files(
        self, 
        name_action: str, 
        filepaths: list[str], 
        delay_seconds: float = 0.7
    ) -> bool:
        """Передает один или серию файлов JSON админу в Aiogram с небольшой задержкой"""
        if not filepaths:
            return False

        success = True
        total_files = len(filepaths)

        # Достаем иконку для красивой подписи (по умолчанию 📁)
        icon = self.TABLE_ICONS.get(name_action, "📁")

        for idx, filepath in enumerate(filepaths, start=1):
            try:
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:

                    # Красивая визуальная подпись с иконкой
                    caption_text = f"{icon} Backup: {name_action}"
                    if total_files > 1:
                        caption_text += f" (Часть {idx}/{total_files})"

                    await self.bot.send_document(
                        chat_id=self.admin_id,
                        document=FSInputFile(filepath),
                        caption=caption_text
                    )

                    # Пауза между файлами, чтобы Telegram API не выкинул Too Many Requests
                    if idx < total_files:
                        await asyncio.sleep(delay_seconds)

                else:
                    logger.warning(f"File {filepath} is empty or not found.")
                    success = False
            except Exception as e:
                logger.error(f"Error pushing file {filepath} to admin: {e}")
                success = False

        return success


    async def _process_table_backup(
        self, 
        data: list[dict], 
        name_action: str, 
        max_records_per_file: int = 50
    ):
        """Единый конвейер для всех таблиц"""
        if not data:
            logger.warning(f"No data for {name_action}, skipping.")
            return
        
        # 1. Сохраняем чанки
        filepaths = await self.save_json_files(
            data=data, 
            name_action=name_action, 
            max_records_per_file=max_records_per_file
        )
        
        # 2. Отправляем все сформированные чанки в Telegram
        if filepaths:
            await self._push_files(name_action, filepaths)



    # ==========================================
    # Методы получения данных (с номерами для FK)
    # ==========================================


    async def get_users_cat(self):
        data = self.db_users.users_categories
        await self._process_table_backup(data, "01_user_categories", max_records_per_file=500)


    async def get_facts_cat(self):
        # Обычно категории фактов лежат в db_memory
        data = self.db_memory.fact_categories
        await self._process_table_backup(data, "02_facts_categories", max_records_per_file=500)


    async def get_users(self):
        data = await self.db_users.get_users()
        print("\n\n", data, "\n\n")
        await self._process_table_backup(data, "03_users", max_records_per_file=500)


    async def get_memories(self):
        data = await self.db_memory.get_all_facts()
        await self._process_table_backup(data, "04_facts", max_records_per_file=1000)


    async def get_messages(self):
        data = await self.db_messages.get_all_messages()
        await self._process_table_backup(data, "05_messages", max_records_per_file=1000)


    async def get_tasks(self):
        data = await self.db_tasks.get_tasks()
        await self._process_table_backup(data, "06_tasks", max_records_per_file=1000)


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
