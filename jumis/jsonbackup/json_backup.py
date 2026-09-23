# jumis/jsonbackup/json_backup.py
import asyncio
import json
import os
import datetime
from zoneinfo import ZoneInfo
from uuid import UUID
from decimal import Decimal
from aiogram import types
from typing import Any, Dict, List
from aiogram.types import FSInputFile
from logs.set_logger import set_logger
from utils.common import get_date_str
logger = set_logger(name="backup_json")
from config import PATH_JSON, ADMIN_ID




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


    @staticmethod
    def _json_serializer(obj: Any) -> Any:
        """Сериализатор: из типов Python/БД в валидный JSON-формат."""
        if isinstance(obj, datetime.datetime):
            return obj.replace(tzinfo=None).isoformat()
            # return obj.isoformat()
        if isinstance(obj, datetime.date):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, Decimal):
            return float(obj)
        raise TypeError(f"Type {type(obj)} is not JSON serializable")


    @staticmethod
    def _prepare_for_db(rec: Dict[str, Any]) -> Dict[str, Any]:
        """Десериализатор: из ISO-строк JSON обратно в объекты Python/БД."""
        parsed_rec = {}

        for key, val in rec.items():
            # 1. Выбрасываем внутренний id таблицы — БД сама сгенерирует новый SERIAL
            if key == "id":
                continue

            # 2. Парсим только строки с ключами дат (_at, _date, due_*)
            if isinstance(val, str) and (
                key.endswith("_at") or key.endswith("_date") or key.startswith("due_")
            ):
                try:
                    # 1. Парсим ISO-строку в datetime
                    dt = datetime.datetime.fromisoformat(val)
                    parsed_rec[key] = dt.replace(tzinfo=None)
                except ValueError:
                    parsed_rec[key] = val
            else:
                parsed_rec[key] = val
        return parsed_rec


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
            timestamp = get_date_str()

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
        # print("\n\n", data, "\n\n")
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



    # ===================================================
    # Методы воссатновления таблиц в базе по JSON файлам
    # ===================================================


    async def restore_table_from_file(self, name_action: str, filepath: str) -> tuple[bool, int, int]:
        """Читает JSON-файл и распределяет запись по соответствующим сервисам БД."""
        good_count, bad_count = 0, 0

        if not os.path.exists(filepath):
            logger.error(f"File not found: {filepath}")
            return False, good_count, bad_count

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                list_data = json.load(f)

            if not isinstance(list_data, list):
                logger.error(f"JSON structure invalid in {filepath}: expected list.")
                return False, good_count, bad_count
                
        except Exception as e:
            logger.error(f"Error reading JSON file {filepath}: {e}")
            return False, good_count, bad_count

        # Диспетчер методов сохранения в БД
        save_handlers = {
            "01_user_categories": getattr(self.db_users, "add_category", None),
            "02_facts_categories": getattr(self.db_memory, "add_category", None),
            "03_users": getattr(self.db_users, "add_user", None),
            "04_facts": getattr(self.db_memory, "add_fact", None),
            "05_messages": getattr(self.db_messages, "add_message", None),
            "06_tasks": getattr(self.db_tasks, "add_task", None),
        }

        db_handler = save_handlers.get(name_action)
        if not db_handler:
            logger.error(f"Unknown action table: {name_action}")
            return False, good_count, bad_count

        # Запись элементов
        for rec in list_data:
            try:
                # 💡 Превращаем строки дат обратно в datetime объекты
                clean_rec = self._prepare_for_db(rec)

                success = await db_handler(clean_rec)
                if success:
                    good_count += 1
                else:
                    bad_count += 1
            except Exception as e:
                logger.error(f"Error inserting row into {name_action}: {e}")
                bad_count += 1

        overall_success = (bad_count == 0 and good_count > 0)
        return overall_success, good_count, bad_count












    # #
    # async def restore_tasks_from_file(self, name_action: str, filepath: str) -> tuple[bool, int, int]:
    #     """Читает JSON-файл и загружает их в БД."""
    #     good_count, bad_count = 0, 0

    #     try:
    #         if not os.path.exists(filepath):
    #             logger.error(f"File not found: {filepath}")
    #             return False, good_count, bad_count

    #         with open(filepath, "r", encoding="utf-8") as f:
    #             # Тут явно нужно будет нам обратную сериализацию сделать даты + время позже разберем
    #             list_data = json.load(f)

    #         if not isinstance(list_data, list):
    #             logger.error("JSON structure invalid: expected a list of dicts.")
    #             return False, good_count, bad_count
            
    #     except Exception as e:
    #         logger.error(f"Error get file JSON {filepath}: {e}")
    #         return False, good_count, bad_count


    #     #### SAVE DATA to DB ####

    #     # Cat Users
    #     if name_action == "01_user_categories":
    #         try:
    #             for rec in list_data:
    #                 sucsses = await self.db_users.add_category(rec)
    #                 if sucsses:
    #                     good_count += 1
    #                 else:
    #                     bad_count += 1
    #             return True, good_count, bad_count
            
    #         except Exception as e:
    #             logger.error(f"Error restoring {name_action} from DB: {e}")
    #             return False, good_count, bad_count

    #     # CAT Memries
    #     elif name_action == "02_facts_categories":
    #         ..
    #         await self.db_memory.add_category(rec)

    #     # Users
    #     elif name_action == "03_users":
    #         await self.db_users.add_user(rec)

    #     # Facts in to memories
    #     elif name_action == "04_facts":
    #         await self.db_memory.add_fact(rec)

    #     # Messages
    #     elif name_action == "05_messages":
    #         await self.db_messages.add_message(rec)

    #     # Tasks
    #     elif name_action == "06_tasks":
    #         await self.db_tasks.add_task(rec)




















