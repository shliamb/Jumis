# jumis/llm/functions.py
from database.memories import add_fact, get_facts_by_category, get_facts_by_user_id
from database.users import get_all_users, db_get_user, db_update_user
from datetime import datetime
from logs.set_logger import set_logger
logger = set_logger(name="llmfunc")
import json


########## DATE ###############

async def get_date():
    """ Получение даты """
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
 


###### MEMORIES ##########

async def write_fact(category: str, content: str, user_id: int = None) -> str:
    """ Сохранить важную информацию/факт в долговременную память."""
    fact_data = {
        "user_id": user_id,
        "category": category,
        "content": content
    }
    return await add_fact(fact_data)



async def write_fact(content: str, category: str, user_id: int = None) -> str:
    """ Сохранить важную информацию/факт в долговременную память."""
    try:
        # 1. Собираем словарь данных
        fact_data = {
            "content": content,
            "category": category,
        }
        
        # user_id добавляем только если он передан (чтобы не перебивать NULL)
        if user_id is not None and user_id != 0:
            fact_data["user_id"] = user_id

        # 2. Генерируем вектор (если используешь векторизацию)
        # embedding = await get_embedding(content)
        # if embedding:
        #     fact_data["embedding"] = embedding


        # # Запись в БД быстро
        # fact_id = await save_fact_to_db(...) 

        # # Фоновая задача улетает параллельно, не блокируя ответ клиенту
        # asyncio.create_task(background_vectorize_fact(fact_id, content))

        # 3. ПЕРЕДАЕМ РОВНО ОДИН АРГУМЕНТ (СЛОВАРЬ)
        success = await add_fact(fact_data)

        if success:
            return "Fact successfully written to memory."
        else:
            return "Error: Could not save fact to database."

    except Exception as e:
        #logger.error(f"Error in write_fact handler: {e}")
        return f"Error executing write_fact: {str(e)}"





async def facts_by_cat(category: str) -> str:
    """Возвращает отформатированный список фактов категории с флагом наличия вектора."""
    if not category or not category.strip():
        logger.warning("Attempted to fetch facts with an empty category.")
        return "No facts found: Category was not specified."

    cat_clean = category.strip()
    facts: list[dict] = await get_facts_by_category(cat_clean)

    if not facts:
        logger.info("No facts found for category: '%s'", cat_clean)
        return f"No facts stored in category '{cat_clean}'."

    formatted_lines = []
    
    for item in facts:
        content = item.get("content", "").strip()
        if not content:
            continue

        # 1. Дата без миллисекунд и секунд
        created_at = item.get("created_at")
        if isinstance(created_at, datetime):
            date_str = created_at.strftime("%Y-%m-%d %H:%M")
        elif created_at:
            date_str = str(created_at)[:16]
        else:
            date_str = "no-date"

        # 2. Метка владельца
        user_id = item.get("user_id")
        owner_tag = f"[User #{user_id}]" if user_id else "[Global]"

        # 3. Флаг наличия эмбеддинга (True / False)
        # Проверяем, что вектор не None и не пустой
        has_vector = bool(item.get("embedding"))
        vec_tag = "[vec: true]" if has_vector else "[vec: false]"

        # 4. Собираем итоговую строчку
        formatted_lines.append(f"• ({date_str}) {owner_tag} {vec_tag} {content}")

    if not formatted_lines:
        return f"No valid facts found in category '{cat_clean}'."

    header = f"=== Stored facts in '{cat_clean}' ({len(formatted_lines)}) ==="
    return f"{header}\n" + "\n".join(formatted_lines)




async def facts_by_user(user_id: int) -> str:
    """Возвращает сжатый и отформатированный список фактов по конкретному user_id для LLM."""
    if not user_id or user_id <= 0:
        logger.warning("Attempted to fetch facts with an invalid user_id: %s", user_id)
        return "No facts found: Invalid or missing user_id."

    facts: list[dict] = await get_facts_by_user_id(user_id)

    if not facts:
        logger.info("No facts found for user_id: %s", user_id)
        return f"No stored facts found for user #{user_id}."

    formatted_lines = []
    
    for item in facts:
        content = item.get("content", "").strip()
        if not content:
            continue

        # 1. Дата без секунд и миллисекунд
        created_at = item.get("created_at")
        if isinstance(created_at, datetime):
            date_str = created_at.strftime("%Y-%m-%d %H:%M")
        elif created_at:
            date_str = str(created_at)[:16]
        else:
            date_str = "no-date"

        # 2. Категория факта (чтобы модель понимала контекст)
        category = item.get("category", "general")
        cat_tag = f"[{category}]"

        # 3. Флаг наличия эмбеддинга
        has_vector = bool(item.get("embedding"))
        vec_tag = "[vec: true]" if has_vector else "[vec: false]"

        # 4. Собираем строчку
        formatted_lines.append(f"• ({date_str}) {cat_tag} {vec_tag} {content}")

    if not formatted_lines:
        return f"No valid facts found for user #{user_id}."

    header = f"=== Stored facts for User #{user_id} ({len(formatted_lines)}) ==="
    return f"{header}\n" + "\n".join(formatted_lines)



# async def del_mem(category: str, key: str) -> str:
#     """ Удалить восспоминание из долгосрочной памяти. """
#     return await del_memory(category, key)


####### USERS ###########



async def get_users() -> str:
    """Возвращает отформатированный список всех пользователей со всеми полями из БД для LLM."""
    users: list[dict] = await get_all_users()

    if not users:
        logger.info("No users found in database.")
        return "No registered users found in the database."

    formatted_users = []
    
    for u in users:
        user_id = u.get("id", "N/A")
        
        # 1. Собираем активные флаги статуса
        flags = []
        if u.get("is_admin"):
            flags.append("ADMIN")
        if u.get("is_blocked"):
            flags.append("BLOCKED")
        if u.get("is_whitelisted"):
            flags.append("WHITELISTED")
        if u.get("is_bot"):
            flags.append("BOT")
        flags_str = f" [{', '.join(flags)}]" if flags else ""

        # 2. Дата регистрации без секунды/миллисекунд
        created_at = u.get("created_at")
        if isinstance(created_at, datetime):
            date_str = created_at.strftime("%Y-%m-%d %H:%M")
        elif created_at:
            date_str = str(created_at)[:16]
        else:
            date_str = "no-date"

        # Заголовок карточки пользователя
        user_lines = [f"• User #{user_id}{flags_str} (registered: {date_str})"]

        # 3. Идентификаторы и контакты
        contacts = []
        if tg_id := u.get("tg_id"):
            contacts.append(f"TG ID: {tg_id}")
        if username := u.get("username"):
            contacts.append(f"@{username}")
        if full_name := u.get("full_name"):
            contacts.append(f"Name: {full_name}")
        if phone := u.get("phone"):
            contacts.append(f"Phone: {phone}")
        
        if contacts:
            user_lines.append(f"  - Contacts: {' | '.join(contacts)}")

        # 4. Категория, язык и модели
        sys_info = []
        if category := u.get("category"):
            sys_info.append(f"Category: {category}")
        if lang := u.get("lang_code"):
            sys_info.append(f"Lang: {lang}")
        if model_def := u.get("model_default"):
            sys_info.append(f"Model: {model_def}")
        if model_cheap := u.get("model_cheap"):
            sys_info.append(f"Model Cheap: {model_cheap}")
        if model_smart := u.get("model_smart"):
            sys_info.append(f"Model Smart: {model_smart}")

        if sys_info:
            user_lines.append(f"  - System: {' | '.join(sys_info)}")

        # 5. Ручные заметки и ИИ-саммари
        if comment := u.get("comment"):
            user_lines.append(f"  - Comment: {comment}")
        if summary := u.get("summary"):
            user_lines.append(f"  - AI Summary: {summary}")

        formatted_users.append("\n".join(user_lines))

    header = f"=== Registered Users ({len(users)}) ==="
    return f"{header}\n\n" + "\n\n".join(formatted_users)






async def get_user(
    user_id: int | None = None,
    tg_id: int | None = None,
    username: str | None = None
) -> str:
    """Поиск профиля пользователя по user_id (БД), tg_id или username."""
    if not user_id and not tg_id and not username:
        logger.warning("Attempted to call get_user without any identifier.")
        return "Error: Provide at least one identifier (user_id, tg_id, or username)."

    # Запрашиваем пользователя через единую функцию БД
    data_user = await db_get_user(user_id=user_id, tg_id=tg_id, username=username)

    if not data_user:
        target = f"id={user_id}" if user_id else (f"tg_id={tg_id}" if tg_id else f"username='{username}'")
        logger.info("User not found for %s", target)
        return f"User not found ({target})."

    # Извлечение полей
    u_id = data_user.get("id")
    u_tg_id = data_user.get("tg_id") or "N/A"
    u_uname = data_user.get("username")
    uname_str = f"@{u_uname}" if u_uname else "no username"

    # Флаги статуса
    flags = []
    if data_user.get("is_admin"):
        flags.append("ADMIN")
    if data_user.get("is_blocked"):
        flags.append("BLOCKED")
    if data_user.get("is_whitelisted"):
        flags.append("WHITELISTED")
    if data_user.get("is_bot"):
        flags.append("BOT")
    
    flags_str = f" [{', '.join(flags)}]" if flags else ""

    # Дата регистрации
    created_at = data_user.get("created_at")
    if isinstance(created_at, datetime):
        date_str = created_at.strftime("%Y-%m-%d %H:%M")
    elif created_at:
        date_str = str(created_at)[:16]
    else:
        date_str = "no-date"

    # Формирование ответа
    lines = [
        f"=== User Profile #{u_id}{flags_str} ===",
        f"• TG ID: {u_tg_id} | Username: {uname_str}"
    ]

    if full_name := data_user.get("full_name"):
        lines.append(f"• Name: {full_name}")
    if phone := data_user.get("phone"):
        lines.append(f"• Phone: {phone}")
    if category := data_user.get("category"):
        lines.append(f"• Category: {category}")
    if lang := data_user.get("lang_code"):
        lines.append(f"• Lang: {lang}")
    if model_default := data_user.get("model_default"):
        lines.append(f"• Model: {model_default}")
    if comment := data_user.get("comment"):
        lines.append(f"• Comment: {comment}")
    if summary := data_user.get("summary"):
        lines.append(f"• AI Summary: {summary}")

    lines.append(f"• Registered: {date_str}")

    return "\n".join(lines)






async def update_user(
    user_id: int | None = None,
    tg_id: int | None = None,
    target_username: str | None = None,
    **kwargs
) -> str:
    """Обновление профиля пользователя по одному из идентификаторов."""
    
    user_data = {}
    target_label = ""

    # 1. Выбираем СТРОГО один приоритетный идентификатор для поиска
    if user_id:
        user_data["id"] = user_id
        target_label = f"ID #{user_id}"
    elif tg_id:
        user_data["tg_id"] = tg_id
        target_label = f"TG ID #{tg_id}"
    elif target_username:
        clean_target = target_username.strip().lstrip("@")
        user_data["username"] = clean_target
        target_label = f"@{clean_target}"
    else:
        logger.warning("Attempted to call update_user without any target identifier.")
        return "Error: Provide at least one identifier (user_id, tg_id, or target_username)."

    # 2. Исключаем ключи-идентификаторы из kwargs, чтобы AI не изменил случайно ключевые ID
    IDENTIFIER_KEYS = {"user_id", "tg_id", "target_username", "id"}
    
    # Собираем только валидные поля для изменения
    update_fields = {}
    for key, val in kwargs.items():
        if key in IDENTIFIER_KEYS or val is None:
            continue
        
        # Если меняется логин пользователя (колонка username) — зачищаем @
        if key == "username" and isinstance(val, str):
            val = val.strip().lstrip("@")
            
        update_fields[key] = val

    # 3. Проверяем, передал ли AI хоть одно поле для изменения
    if not update_fields:
        return f"Error: No fields provided to update for user ({target_label})."

    # Объединяем идентификатор и редактируемые поля в один словарь для db_update_user
    user_data.update(update_fields)

    # 4. Вызываем функцию БД (db_update_user возвращает True / False)
    success = await db_update_user(user_data)

    if not success:
        logger.error("Failed to update user %s", target_label)
        return f"Error: User '{target_label}' not found or database update failed."

    # 5. Возвращаем чёткое подтверждение для LLM
    changed_keys = ", ".join(update_fields.keys())
    logger.info("Successfully updated User (%s) fields: %s", target_label, changed_keys)
    return f"Success: User ({target_label}) updated. Fields changed: [{changed_keys}]."





FUNCTIONS = {

    "get_date": {
        "description": "Returns the current system date and time. Provides precise temporal context for relative date calculations, scheduling, and time-sensitive queries.",
        "function": get_date,
        "schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    "write_fact": {
        "description": "Persists a key fact, user preference, or system rule into long-term memory for future context retrieval.",
        "function": write_fact,
        "schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string", 
                    "description": "Concise, distilled factual statement to store in memory."
                },
                "category": {
                    "type": "string", 
                    "enum": ["fact", "preference", "hardware", "agreement", "global_rule"],
                    "description": "Functional classification of the stored memory."
                },
                "user_id": {
                    "type": "integer", 
                    "description": "Target client ID. Omit if storing a global system instruction or personal rule for Jumis."
                }
            },
            "required": ["content", "category"]
        }
    },

    "facts_by_cat": {
        "description": "Retrieves all long-term memory facts filtered by a specified category.",
        "function": facts_by_cat,
        "schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["fact", "preference", "hardware", "agreement", "global_rule"],
                    "description": "Target category to filter facts."
                }
            },
            "required": ["category"]
        }
    },

    "facts_by_user": {
        "description": "Retrieves all long-term memory facts associated with a specific user_id.",
        "function": facts_by_user,
        "schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "integer", 
                    "description": "Target client or user ID to fetch facts for."
                }
            },
            "required": ["user_id"]
        }
    },

    "get_users": {
        "description": "Retrieves a list of all registered users and clients in the system.",
        "function": get_users,
        "schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    "get_user": {
        "description": "Retrieves user profile details by internal Database User ID, Telegram ID, or username.",
        "function": get_user,
        "schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "integer", 
                    "description": "Internal database primary key user ID (from get_users list)."
                },
                "tg_id": {
                    "type": "integer", 
                    "description": "Telegram user ID."
                },
                "username": {
                    "type": "string", 
                    "description": "Telegram username (e.g., 'john_doe' or '@john_doe')."
                }
            },
            "required": []
        }
    },

    "update_user": {
        "description": "Updates profile attributes, notes, or flags for a user in the database.",
        "function": update_user,
        "schema": {
            "type": "object",
            "properties": {
                # --- Идентификаторы (нужен хотя бы один) ---
                "user_id": {
                    "type": "integer", 
                    "description": "Internal database user ID (PK)."
                },
                "tg_id": {
                    "type": "integer", 
                    "description": "Telegram user ID."
                },
                "target_username": {
                    "type": "string", 
                    "description": "Telegram username to search user by (e.g. 'john_doe')."
                },

                # --- Редактируемые поля ---
                "full_name": {"type": "string", "description": "Full name of the user."},
                "phone": {"type": "string", "description": "Phone number."},
                "category": {"type": "string", "description": "Category (e.g., 'client', 'friend', 'spam', 'hardware')."},
                "comment": {"type": "string", "description": "Personal manual note about the user."},
                "summary": {"type": "string", "description": "AI-generated summary of past dialogue context."},
                
                # --- Флаги доступа ---
                "is_admin": {"type": "boolean", "description": "Set admin status."},
                "is_blocked": {"type": "boolean", "description": "Block or unblock user."},
                "is_whitelisted": {"type": "boolean", "description": "Priority queue whitelist status."},
                
                # --- Настройки ---
                "lang_code": {"type": "string", "description": "Language code (e.g. 'ru', 'en')."},
                "model_default": {"type": "string", "description": "Default model name (e.g. 'deepseek/deepseek-v4-flash')."}
            },
            "required": []
        }
    }

    # "delete_memory": {
    #     "description": "Удалить конкретное воспоминание, если оно стало неактуальным.",
    #     "function": del_mem,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "category": {"type": "string"},
    #             "key": {"type": "string"}
    #         },
    #         "required": ["category", "key"]
    #     }
    # }

}