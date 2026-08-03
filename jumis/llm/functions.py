# jumis/llm/functions.py
from database.users import get_all_users, db_get_user, db_update_user
from vector import embedder
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



async def add_category_facts(name: str, description: str = "", db_memory=None) -> str:
    """Creates a new fact category in the database."""
    if not db_memory:
        return "Error: Memory service is unavailable."
    
    category_data = {
        "name": name,
        "description": description
    }

    success = await db_memory.add_category(category_data)

    if success:
        return f"Category '{name}' successfully created and available for storing facts."
    else:
        return f"Failed to create category '{name}'."



async def get_categories_facts(db_memory=None) -> str:
    """Форматирует список категорий фактов в удобный для LLM текст"""
    if not db_memory:
        return "Error: Memory service is unavailable."

    # Обновляем кэш в db_memory
    await db_memory._refresh_categories()

    # Забираем список из self.fact_categories (если там None, подставится пустой список)
    categories = db_memory.fact_categories or []

    if not categories:
        return "No fact categories available in the database."

    formatted_lines = []

    for item in categories:
        cat_id = item.get("id", "N/A")
        name = item.get("name", "unnamed")
        description = item.get("description") or "No description provided."

        # 1. Форматирование даты (created_at или updated_at)
        created_at = item.get("created_at") or item.get("updated_at")
        if isinstance(created_at, datetime):
            date_str = created_at.strftime("%Y-%m-%d %H:%M")
        elif created_at:
            date_str = str(created_at)[:16]
        else:
            date_str = "no-date"

        # 2. Метка владельца
        user_id = item.get("user_id")
        owner_tag = f"[User #{user_id}]" if user_id else "[Global]"

        # 3. Собираем строчку
        formatted_lines.append(
            f"• Category '{name}' (ID: {cat_id}) ({date_str}) {owner_tag}: {description}"
        )

    header = f"=== Available Fact Categories ({len(formatted_lines)}) ==="
    return f"{header}\n" + "\n".join(formatted_lines)



async def write_fact(content: str, facts_category: str, user_id: int = None, db_memory = None) -> str:
    """ Сохранить важную информацию/факт в долговременную память."""
    if not db_memory:
        return "Error: Memory service is not available."
    try:
        # 1. Собираем словарь данных
        fact_data = {
            "content": content,
            "category": facts_category,
        }
        
        # user_id добавляем только если он передан (чтобы не перебивать NULL)
        if user_id is not None and user_id != 0:
            fact_data["user_id"] = user_id

        # 2. Генерируем вектор (если используешь векторизацию)
        embedding = await embedder.get_embedding(content)
        # asyncio.create_task(background_vectorize_fact(fact_id, content))
        if embedding:
            fact_data["embedding"] = embedding
        else:
            print("Error embedding")

        # 3. Сохраняем
        success = await db_memory.add_fact(fact_data)

        if success:
            return "Fact successfully written to memory."
        else:
            return "Error: Could not save fact to database."

    except Exception as e:
        #logger.error(f"Error in write_fact handler: {e}")
        return f"Error executing write_fact: {str(e)}"



async def search_facts(
    query: str, 
    facts_category: str = None, 
    user_id: int = None, 
    limit: int = 10, 
    db_memory = None
) -> str:
    """Возвращает отформатированный список векторно релевантных фактов"""
    if not db_memory:
        return "Error: Memory service is not available."

    embedding_query = await embedder.get_embedding(query)
    if not embedding_query:
        logger.error("Failed to generate embedding for query: %s", query)
        return "Error: Could not process search query embedding."

    # Универсальный вызов: отработает и глобально, и по фильтрам (любым комбинациям)
    facts: list[dict] = await db_memory.search_vectors(
        embedding=embedding_query,
        user_id=user_id,
        category=facts_category,
        limit=limit
    )

    # Красивое описание области поиска для логов/ответа
    scope_parts = []
    if user_id:
        scope_parts.append(f"user #{user_id}")
    if facts_category and facts_category.strip():
        scope_parts.append(f"category '{facts_category.strip()}'")
    search_scope = ", ".join(scope_parts) if scope_parts else "global (all facts)"

    if not facts:
        return f"No relevant facts found for query in {search_scope}."

    formatted_lines = []
    for item in facts:
        content = item.get("content", "").strip()
        if not content:
            continue

        fact_id = item.get("id", "N/A")
        created_at = item.get("created_at") or item.get("updated_at")
        date_str = (
            created_at.strftime("%Y-%m-%d %H:%M") 
            if isinstance(created_at, datetime) 
            else str(created_at)[:16] if created_at else "no-date"
        )
        
        item_user_id = item.get("user_id")
        user_tag = f"[User #{item_user_id}]" if item_user_id else "[Global]"
        cat_tag = f"[{item.get('category')}]" if item.get("category") else ""

        formatted_lines.append(
            f"• Fact #{fact_id} ({date_str}) {user_tag} {cat_tag}: {content}"
        )

    if not formatted_lines:
        return f"No valid facts found in {search_scope}."

    header = f"=== Found facts in {search_scope} ({len(formatted_lines)}) ==="
    return f"{header}\n" + "\n".join(formatted_lines)



# async def search_facts(
#     query: str, 
#     facts_category: str = None, 
#     user_id: int = None, 
#     limit: int = 10, 
#     db_memory = None
# ) -> str:
#     """Возвращает отформатированный список векторно релевантных фактов"""
#     if not db_memory:
#         return "Error: Memory service is not available."

#     # 1. Получаем эмбеддинг поискового запроса
#     embedding_query = await embedder.get_embedding(query)
#     if not embedding_query:
#         logger.error("Failed to generate embedding for search query: %s", query)
#         return "Error: Could not process search query embedding."

#     # 2. Выполняем векторный поиск в зависимости от фильтров
#     search_scope = "global"
#     if user_id:
#         search_scope = f"user #{user_id}"
#         facts: list[dict] = await db_memory.get_vector_by_user_id(
#             user_id=int(user_id), embedding=embedding_query, n=int(limit)
#         )
#     elif facts_category and facts_category.strip():
#         cat_clean = facts_category.strip()
#         search_scope = f"category '{cat_clean}'"
#         facts: list[dict] = await db_memory.get_vector_by_category(
#             category=cat_clean, embedding=embedding_query, n=int(limit)
#         )
#     else:
#         facts: list[dict] = await db_memory.get_vector_by_jumis(
#             embedding=embedding_query, n=int(limit)
#         )

#     if not facts:
#         logger.info("No vector facts found for query in scope [%s]", search_scope)
#         return f"No relevant facts found for query in {search_scope}."

#     # 3. Форматируем найденные факты
#     formatted_lines = []

#     for item in facts:
#         content = item.get("content", "").strip()
#         if not content:
#             continue

#         fact_id = item.get("id", "N/A")

#         # Форматирование даты
#         created_at = item.get("created_at") or item.get("updated_at")
#         if isinstance(created_at, datetime):
#             date_str = created_at.strftime("%Y-%m-%d %H:%M")
#         elif created_at:
#             date_str = str(created_at)[:16]
#         else:
#             date_str = "no-date"

#         # Метки владельца и категории
#         item_user_id = item.get("user_id")
#         user_tag = f"[User #{item_user_id}]" if item_user_id else "[Global]"
        
#         category = item.get("category")
#         cat_tag = f"[{category}]" if category else ""

#         # Собираем красивую и сжатую строку
#         formatted_lines.append(
#             f"• Fact #{fact_id} ({date_str}) {user_tag} {cat_tag}: {content}"
#         )

#     if not formatted_lines:
#         return f"No valid facts found in {search_scope}."

#     header = f"=== Found facts in {search_scope} ({len(formatted_lines)}) ==="
#     return f"{header}\n" + "\n".join(formatted_lines)



# async def search_facts(query: str, facts_category: str = "", user_id: int = None, limit: int = 10,  db_memory = None) -> str:
#     """Возвращает отформатированный список фактов векторно релевантный по категории или пользователю с максимальным количеством"""
#     if not db_memory:
#         return "Error: Memory service is not available."

#     # Пока что не уверен, но допустим что, если нет категории и юзер айди то ищет по всему 
#     # или только по системным фактам..
#     # if not facts_category and not user_id:
#     #     logger.warning("Attempted to fetch facts with an empty category or user id.")
#     #     return "No facts found: Category or user id was not specified."

#     embedding_query = await embedder.get_embedding(query)
#     if not embedding_query:
#         log(...)
#         print("error..")
#         return "Error get..."

#     if user_id:
#         facts: list[dict] = await db_memory.get_vector_by_user_id(user_id=str(user_id), embedding=embedding_query, n=limit)
#     elif facts_category:
#         cat_clean = facts_category.strip()
#         facts: list[dict] = await db_memory.get_vector_by_category(category=cat_clean, embedding=embedding_query, n=limit)
#     else:
#         facts: list[dict] = await db_memory.get_vector_by_jumis(embedding=embedding_query, n=limit)



#     if not facts:
#         logger.info("No facts found")
#         return "No facts stored"

#     formatted_lines = []
    
#     for item in facts:
#         fact_text = ""
#         content = item.get("content", "").strip()
#         if not content:
#             continue

#         fact_id = fact_id + f"[Content: #{content}"

#         # 1. Извлекаем ID факта из записи БД
#         fact_id = item.get("id", "N/A")
#         fact_text = fact_text + f"[Id: #{fact_id}]"

#         # 2. Дата без миллисекунд и секунд
#         created_at = item.get("created_at")
#         if isinstance(created_at, datetime):
#             date_str = created_at.strftime("%Y-%m-%d %H:%M")
#         elif created_at:
#             date_str = str(created_at)[:16]
#             fact_text = fact_text + f"Date: #{date_str}" 
#         else:
#             date_str = "no-date"

#         # 3. Метка владельца
#         user_id = item.get("user_id")
#         if user_id:
#             fact_text = fact_text + f"[User #{user_id}]"

#         category = item.get("category")
#         if category:
#             fact_text = fact_text + f"[Category #{category}]"

#         # 5. Собираем итоговую строчку с Fact #ID
#         formatted_lines.append(f"• Fact #{fact_text}")

#     if not formatted_lines:
#         return f"No valid facts found in category '{cat_clean}'."

#     header = f"=== Stored facts in '{cat_clean}' ({len(formatted_lines)}) ==="
#     return f"{header}\n" + "\n".join(formatted_lines)

    

# async def facts_by_cat(facts_category: str, db_memory = None) -> str:
#     """Возвращает отформатированный список фактов категории с их ID и флагом наличия вектора."""
#     if not db_memory:
#         return "Error: Memory service is not available."

#     if not facts_category or not facts_category.strip():
#         logger.warning("Attempted to fetch facts with an empty category.")
#         return "No facts found: Category was not specified."

#     cat_clean = facts_category.strip()
#     facts: list[dict] = await db_memory.get_facts_by_category(cat_clean)

#     if not facts:
#         logger.info("No facts found for category: '%s'", cat_clean)
#         return f"No facts stored in category '{cat_clean}'."

#     formatted_lines = []
    
#     for item in facts:
#         content = item.get("content", "").strip()
#         if not content:
#             continue

#         # 1. Извлекаем ID факта из записи БД
#         fact_id = item.get("id", "N/A")

#         # 2. Дата без миллисекунд и секунд
#         created_at = item.get("created_at")
#         if isinstance(created_at, datetime):
#             date_str = created_at.strftime("%Y-%m-%d %H:%M")
#         elif created_at:
#             date_str = str(created_at)[:16]
#         else:
#             date_str = "no-date"

#         # 3. Метка владельца
#         user_id = item.get("user_id")
#         owner_tag = f"[User #{user_id}]" if user_id else "[Global]"

#         # 4. Флаг наличия эмбеддинга (True / False)
#         has_vector = bool(item.get("embedding"))
#         vec_tag = "[vec: true]" if has_vector else "[vec: false]"

#         # 5. Собираем итоговую строчку с Fact #ID
#         formatted_lines.append(f"• Fact #{fact_id} ({date_str}) {owner_tag} {vec_tag} {content}")

#     if not formatted_lines:
#         return f"No valid facts found in category '{cat_clean}'."

#     header = f"=== Stored facts in '{cat_clean}' ({len(formatted_lines)}) ==="
#     return f"{header}\n" + "\n".join(formatted_lines)




# async def facts_by_user(user_id: int, db_memory = None) -> str:
#     """Возвращает сжатый и отформатированный список фактов по конкретному user_id с их ID для LLM."""
#     if not db_memory:
#         return "Error: Memory service is not available."

#     if not user_id or user_id <= 0:
#         logger.warning("Attempted to fetch facts with an invalid user_id: %s", user_id)
#         return "No facts found: Invalid or missing user_id."

#     facts: list[dict] = await db_memory.get_facts_by_user_id(user_id)

#     if not facts:
#         logger.info("No facts found for user_id: %s", user_id)
#         return f"No stored facts found for user #{user_id}."

#     formatted_lines = []
    
#     for item in facts:
#         content = item.get("content", "").strip()
#         if not content:
#             continue

#         # 1. Извлекаем ID факта из записи БД
#         fact_id = item.get("id", "N/A")

#         # 2. Дата без секунд и миллисекунд
#         created_at = item.get("created_at")
#         if isinstance(created_at, datetime):
#             date_str = created_at.strftime("%Y-%m-%d %H:%M")
#         elif created_at:
#             date_str = str(created_at)[:16]
#         else:
#             date_str = "no-date"

#         # 3. Категория факта
#         category = item.get("category", "general")
#         cat_tag = f"[{category}]"

#         # 4. Флаг наличия эмбеддинга
#         has_vector = bool(item.get("embedding"))
#         vec_tag = "[vec: true]" if has_vector else "[vec: false]"

#         # 5. Собираем строчку: Fact #ID передается прямо перед контекстом
#         formatted_lines.append(f"• Fact #{fact_id} ({date_str}) {cat_tag} {vec_tag} {content}")

#     if not formatted_lines:
#         return f"No valid facts found for user #{user_id}."

#     header = f"=== Stored facts for User #{user_id} ({len(formatted_lines)}) ==="
#     return f"{header}\n" + "\n".join(formatted_lines)



async def del_fact(id: int, db_memory = None) -> str:
    """Удаление факта по ID с информативным ответом для агента."""
    if not db_memory:
        return "Error: Memory service is not available."

    if not id or id <= 0:
        logger.warning("Attempted to call del_fact with invalid id: %s", id)
        return "Error: Provide a valid positive fact ID."

    success = await db_memory.db_del_fact(id)

    if success:
        logger.info("Fact #%s successfully deleted.", id)
        return f"Success: Fact #{id} has been permanently deleted."
    else:
        logger.error("Failed to delete fact #%s.", id)
        return f"Error: Fact #{id} was not found or could not be deleted."



async def update_fact(
    id: int, 
    content: str = None, 
    facts_category: str = None, 
    user_id: int = None,
    db_memory = None,
    **kwargs
) -> str:
    """Обновление факта в таблице memories"""

    if not db_memory:
        return "Error: Memory service is not available."
    
    # 1. Проверяем наличие основного ID
    if not id:
        logger.warning("Attempted to call update_fact without fact 'id'.")
        return "Error: 'id' parameter is required to identify the fact to update."

    update_fields = {}

    # 2. Собираем переданные явные параметры
    if content is not None and content.strip():
        update_fields["content"] = content.strip()
        # КРИТИЧНО: При изменении текста факта генерируем новый вектор!
        update_fields["embedding"] = await embedder.get_embedding(content.strip())

    if facts_category is not None:
        update_fields["category"] = facts_category

    if user_id is not None:
        update_fields["user_id"] = user_id

    # 3. Дособираем доп. параметры из kwargs (если передали что-то ещё, фильтруя системные ключи)
    FORBIDDEN_KEYS = {"id", "embedding", "created_at"}
    for key, val in kwargs.items():
        if key not in FORBIDDEN_KEYS and val is not None:
            update_fields[key] = val

    # 4. Проверяем, передал ли AI хоть одно поле для изменения
    if not update_fields:
        logger.warning("No valid fields provided to update for fact ID %s.", id)
        return f"Error: No fields provided to update for fact ID {id}."

    # 5. Вызываем функцию БД (передаем ID и словарь с обновляемыми полями)
    fact_data = {"id": id, **update_fields}
    success = await db_memory.edit_fact(fact_data=fact_data)

    if not success:
        logger.error("Failed to update fact ID %s in database.", id)
        return f"Error: Fact with ID {id} not found or database update failed."

    # 6. Возвращаем чёткое подтверждение для LLM
    changed_keys = ", ".join(k for k in update_fields.keys() if k != "embedding")
    logger.info("Successfully updated Fact (ID: %s). Fields changed: %s", id, changed_keys)
    
    return f"Success: Fact ID {id} updated. Fields changed: [{changed_keys}]."



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

    "get_categories_facts": {
        "description": "Retrieves the full list of available fact categories with their descriptions.",
        "function": get_categories_facts,
        "schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    "add_category_facts": {
        "description": "Creates a new facts_category for facts if none of the existing categories match the context.",
        "function": add_category_facts,
        "schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique name of the facts_category in snake_case (e.g., 'medical_history', 'hobbies')."
                },
                "description": {
                    "type": "string",
                    "description": "Short explanation of what kind of facts belong to this facts_category."
                }
            },
            "required": ["name"]
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
                "facts_category": {
                    "type": "string", 
                    # "enum": ["fact", "preference", "hardware", "agreement", "global_rule"], само подставит
                    "description": "Functional classification of the stored memory."
                },
                "user_id": {
                    "type": "integer", 
                    "description": "Target client ID. Omit if storing a global system instruction or personal rule for Jumis."
                }
            },
            "required": ["content", "facts_category"]
        }
    },

    "update_fact": {
        "description": "Updates an existing memory fact by its ID. Use this if you need to modify the fact's text, its facts_category, or reassign it to another user.",
        "function": update_fact,
        "schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer", 
                    "description": "Unique database ID of the fact (record) to update."
                },
                "content": {
                    "type": "string", 
                    "description": "New concise text of the fact. Pass this only if you need to alter the meaning or fix the phrasing."
                },
                "facts_category": {
                    "type": "string", 
                    # "enum": ["fact", "preference", "hardware", "agreement", "global_rule"], само подставит
                    "description": "New functional facts_category for the fact."
                },
                "user_id": {
                    "type": "integer", 
                    "description": "User ID. Omit or pass null if this fact becomes a global system rule."
                }
            },
            "required": ["id"]
        }
    },

    "search_facts": {
        "description": "Performs semantic (vector) search to find relevant memory facts based on a natural language query. Can be filtered by category or user ID.",
        "function": search_facts,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search prompt or key topic in natural language to find semantically related facts."
                },
                "facts_category": {
                    "type": "string",
                    # Динамический enum подставится автоматически через get_tools_for_agent
                    "description": "Optional category name to filter search results."
                },
                "user_id": {
                    "type": "integer",
                    "description": "Optional user ID to search facts specific to a user."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of facts to return. Optional, defaults to 10."
                }
            },
            "required": ["query"]
        }
    },

    "del_fact": {
        "description": "Permanently deletes a specific long-term memory fact by its unique ID.",
        "function": del_fact,
        "schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer", 
                    "description": "Unique identifier (ID) of the fact to delete."
                }
            },
            "required": ["id"]
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

}









    # "facts_by_cat": {
    #     "description": "Retrieves all long-term memory facts filtered by a specified facts_category.",
    #     "function": facts_by_cat,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "facts_category": {
    #                 "type": "string",
    #                 # "enum": ["fact", "preference", "hardware", "agreement", "global_rule"], само подставит
    #                 "description": "Target facts_category to filter facts."
    #             }
    #         },
    #         "required": ["facts_category"]
    #     }
    # },

    # "facts_by_user": {
    #     "description": "Retrieves all long-term memory facts associated with a specific user_id.",
    #     "function": facts_by_user,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "user_id": {
    #                 "type": "integer", 
    #                 "description": "Target client or user ID to fetch facts for."
    #             }
    #         },
    #         "required": ["user_id"]
    #     }
    # },