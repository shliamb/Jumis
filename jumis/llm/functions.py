# jumis/llm/functions.py
import asyncio
from typing import Any, Dict, List, Optional
from vector import embedder
from utils.common import sanitize_human_text
from datetime import datetime, timezone
from config import ADMIN_ID
from logs.set_logger import set_logger
logger = set_logger(name="llmfunc")
import json



# Множество для хранения ссылок на активные фоновые задачи (защита от GC в Python 3.11+)
background_tasks = set()


########## DATE ###############

async def get_date():
    """ Получение даты """
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
        # Обновляем кэш в db_memory
        await db_memory._refresh_categories()
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
        tg_id = item.get("tg_id")
        owner_tag = f"[User #{tg_id}]" if tg_id else "[Global]"

        # 3. Собираем строчку
        formatted_lines.append(
            f"• Category '{name}' (ID: {cat_id}) ({date_str}) {owner_tag}: {description}"
        )

    header = f"=== Available Fact Categories ({len(formatted_lines)}) ==="
    return f"{header}\n" + "\n".join(formatted_lines)



async def background_vectorize_fact(fact_id: int, content: str, db_memory):
    """Фоновая векторизация факта и обновление его записи в БД."""
    try:
        # 1. Получаем вектор
        embedding = await embedder.get_embedding(content)
        if not embedding:
            logger.error(f"[BG Embedding] Failed to generate embedding for fact_id={fact_id}")
            return

        fact_data = {
            "id": fact_id,
            "embedding": embedding
        }

        # 2. Обновляем факт в базе
        success = await db_memory.edit_fact(fact_data)
        if success:
            logger.info(f"[BG Embedding] Fact ID {fact_id} successfully vectorized and updated.")
        else:
            logger.error(f"[BG Embedding] Failed to update embedding in DB for fact_id={fact_id}")

    except Exception as e:
        logger.error(f"[BG Embedding] Critical error vectorizing fact_id={fact_id}: {e}", exc_info=True)


    
async def write_fact(content: str, facts_category: str, tg_id: int = None, db_memory = None) -> str:
    """Сохраняет факт в БД и фоново запускает векторизацию."""
    if not db_memory:
        return "Error: Memory service is not available."

    try:
        clean_content = content.strip()
        if not clean_content:
            return "Error: Fact content cannot be empty."

        fact_data = {
            "content": clean_content,
            "category": facts_category,
        }

        # tg_id добавляем только если он валиден
        if tg_id is not None and tg_id != 0:
            fact_data["tg_id"] = tg_id

        # 1. Быстро сохраняем факт в БД без вектора
        fact_id = await db_memory.add_fact(fact_data)

        if fact_id:
            # 2. Регистрируем фоновую задачу в множестве, чтобы GC её не уничтожил
            task = asyncio.create_task(background_vectorize_fact(fact_id, clean_content, db_memory))
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

            return f"Fact successfully written to memory (ID: {fact_id})."
        else:
            return "Error: Could not save fact to database."

    except Exception as e:
        logger.error(f"Error in write_fact handler: {e}", exc_info=True)
        return f"Error executing write_fact: {str(e)}"



async def search_facts(
    query: str, 
    facts_category: str = None, 
    tg_id: int = None, 
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
        tg_id=tg_id,
        category=facts_category,
        limit=limit
    )

    # Красивое описание области поиска для логов/ответа
    scope_parts = []
    if tg_id:
        scope_parts.append(f"user #{tg_id}")
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
        
        item_tg_id= item.get("tg_id")
        user_tag = f"[User #{item_tg_id}]" if item_tg_id else "[Global]"
        cat_tag = f"[{item.get('category')}]" if item.get("category") else ""

        formatted_lines.append(
            f"• Fact #{fact_id} ({date_str}) {user_tag} {cat_tag}: {content}"
        )

    if not formatted_lines:
        return f"No valid facts found in {search_scope}."

    header = f"=== Found facts in {search_scope} ({len(formatted_lines)}) ==="
    return f"{header}\n" + "\n".join(formatted_lines)




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
    tg_id: int = None,
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

    if tg_id is not None:
        update_fields["tg_id"] = tg_id

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


async def add_category_users(name: str, description: str = "", db_users=None) -> str:
    """Creates a new users category in the database."""
    if not db_users:
        return "Error: Users service is unavailable."

    # Минимальная валидация имени
    clean_name = name.strip()
    if not clean_name:
        return "Error: Category name cannot be empty."

    category_data = {
        "name": clean_name,
        "description": description.strip()
    }

    success = await db_users.add_category(category_data)

    if success:
        # Обновляем кэш в db_users
        await db_users._refresh_categories()
        return f"Category '{clean_name}' successfully created and available for storing users."
    else:
        return f"Failed to create category '{clean_name}'."



async def get_categories_users(db_users=None) -> str:
    """Форматирует список категорий пользователей в удобный для LLM текст"""
    if not db_users:
        return "Error: Users service is unavailable."

    # Обновляем кэш в db_users
    await db_users._refresh_categories()

    # Забираем список из self.users_categories (если там None, подставится пустой список)
    categories = db_users.users_categories or []

    if not categories:
        return "No user categories available in the database."

    formatted_lines = []

    for item in categories:
        cat_id = item.get("id", "N/A")
        name = item.get("name", "unnamed")
        description = item.get("description") or "No description provided."

        # Форматирование даты (created_at или updated_at)
        created_at = item.get("created_at") or item.get("updated_at")
        if isinstance(created_at, datetime):
            date_str = created_at.strftime("%Y-%m-%d %H:%M")
        elif created_at:
            date_str = str(created_at)[:16]
        else:
            date_str = "no-date"

        # Собираем строчку
        formatted_lines.append(
            f"• Category '{name}' (ID: {cat_id}) ({date_str}): {description}"
        )

    header = f"=== Available Users Categories ({len(formatted_lines)}) ==="
    return f"{header}\n" + "\n".join(formatted_lines)



# async def get_users(db_users=None) -> str:
#     """Возвращает отформатированный список всех пользователей со всеми полями из БД для LLM."""
#     users: list[dict] = await db_users.get_all_users()

#     if not users:
#         logger.info("No users found in database.")
#         return "No registered users found in the database."

#     formatted_users = []
    
#     for u in users:
#         user_id = u.get("id", "N/A")
        
#         # 1. Собираем активные флаги статуса
#         flags = []
#         if u.get("is_admin"):
#             flags.append("ADMIN")
#         if u.get("is_blocked"):
#             flags.append("BLOCKED")
#         if u.get("is_whitelisted"):
#             flags.append("WHITELISTED")
#         if u.get("is_bot"):
#             flags.append("BOT")
#         flags_str = f" [{', '.join(flags)}]" if flags else ""

#         # 2. Дата регистрации без секунды/миллисекунд
#         created_at = u.get("created_at")
#         if isinstance(created_at, datetime):
#             date_str = created_at.strftime("%Y-%m-%d %H:%M")
#         elif created_at:
#             date_str = str(created_at)[:16]
#         else:
#             date_str = "no-date"

#         # Заголовок карточки пользователя
#         user_lines = [f"• User #{user_id}{flags_str} (registered: {date_str})"]

#         # 3. Идентификаторы и контакты
#         contacts = []
#         if tg_id := u.get("tg_id"):
#             contacts.append(f"TG ID: {tg_id}")
#         if username := u.get("username"):
#             contacts.append(f"@{username}")
#         if full_name := u.get("full_name"):
#             contacts.append(f"Name: {full_name}")
#         if phone := u.get("phone"):
#             contacts.append(f"Phone: {phone}")
        
#         if contacts:
#             user_lines.append(f"  - Contacts: {' | '.join(contacts)}")

#         # 4. Категория, язык и модели
#         sys_info = []
#         if category := u.get("category"):
#             sys_info.append(f"Category: {category}")
#         if lang := u.get("lang_code"):
#             sys_info.append(f"Lang: {lang}")
#         if model_def := u.get("model_default"):
#             sys_info.append(f"Model: {model_def}")
#         if model_cheap := u.get("model_cheap"):
#             sys_info.append(f"Model Cheap: {model_cheap}")
#         if model_smart := u.get("model_smart"):
#             sys_info.append(f"Model Smart: {model_smart}")

#         if sys_info:
#             user_lines.append(f"  - System: {' | '.join(sys_info)}")

#         # 5. Ручные заметки и ИИ-саммари
#         if comment := u.get("comment"):
#             user_lines.append(f"  - Comment: {comment}")
#         if summary := u.get("summary"):
#             user_lines.append(f"  - AI Summary: {summary}")

#         formatted_users.append("\n".join(user_lines))

#     header = f"=== Registered Users ({len(users)}) ==="
#     return f"{header}\n\n" + "\n\n".join(formatted_users)





# async def get_user(
#     user_id: int | None = None,
#     tg_id: int | None = None,
#     username: str | None = None,
#     db_users=None,
#     **kwargs  # Защита от лишних аргументов LLM
# ) -> str:
#     """Поиск профиля пользователя по user_id (БД), tg_id или username."""
#     if not db_users:
#         return "Error: Database service 'db_users' is not available."

#     if not user_id and not tg_id and not username:
#         logger.warning("Attempted to call get_user without any identifier.")
#         return "Error: Provide at least one identifier (user_id, tg_id, or username)."

#     # Запрашиваем пользователя через единую функцию БД
#     data_user = await db_users.db_get_user(user_id=user_id, tg_id=tg_id, username=username)

#     if not data_user:
#         target = f"id={user_id}" if user_id else (f"tg_id={tg_id}" if tg_id else f"username='{username}'")
#         logger.info("User not found for %s", target)
#         return f"User not found ({target})."

#     # Извлечение полей
#     u_id = data_user.get("id")
#     u_tg_id = data_user.get("tg_id") or "N/A"
#     u_uname = data_user.get("username")
#     uname_str = f"@{u_uname}" if u_uname else "no username"

#     # Флаги статуса
#     flags = []
#     if data_user.get("is_admin"):
#         flags.append("ADMIN")
#     if data_user.get("is_blocked"):
#         flags.append("BLOCKED")
#     if data_user.get("is_whitelisted"):
#         flags.append("WHITELISTED")
#     if data_user.get("is_bot"):
#         flags.append("BOT")
    
#     flags_str = f" [{', '.join(flags)}]" if flags else ""

#     # Дата регистрации
#     created_at = data_user.get("created_at")
#     if isinstance(created_at, datetime):
#         date_str = created_at.strftime("%Y-%m-%d %H:%M")
#     elif created_at:
#         date_str = str(created_at)[:16]
#     else:
#         date_str = "no-date"

#     # Формирование ответа
#     lines = [
#         f"=== User Profile #{u_id}{flags_str} ===",
#         f"• TG ID: {u_tg_id} | Username: {uname_str}"
#     ]

#     if full_name := data_user.get("full_name"):
#         lines.append(f"• Name: {full_name}")
#     if phone := data_user.get("phone"):
#         lines.append(f"• Phone: {phone}")
#     if category := data_user.get("category"):
#         lines.append(f"• Category: {category}")
#     if lang := data_user.get("lang_code"):
#         lines.append(f"• Lang: {lang}")
#     if aliases := data_user.get("aliases"):
#         lines.append(f"• Aliases: {aliases}")
#     if comment := data_user.get("comment"):
#         lines.append(f"• Comment: {comment}")
#     if summary := data_user.get("summary"):
#         lines.append(f"• AI Summary: {summary}")

#     # Назначенные модели
#     models = []
#     if m_def := data_user.get("model_default"):
#         models.append(f"default={m_def}")
#     if m_cheap := data_user.get("model_cheap"):
#         models.append(f"cheap={m_cheap}")
#     if m_smart := data_user.get("model_smart"):
#         models.append(f"smart={m_smart}")
#     if models:
#         lines.append(f"• Models: {', '.join(models)}")

#     lines.append(f"• Registered: {date_str}")

#     return "\n".join(lines)




async def update_user(
    user_id: int | None = None,
    tg_id: int | None = None,
    target_username: str | None = None,
    db_users=None,
    embedder=None,
    **kwargs
) -> str:
    """Обновление профиля пользователя по одному из идентификаторов."""
    if not db_users:
        return "Error: Database service 'db_users' is not available."

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

    # 2. Исключаем системные сервисы и идентификаторы из списка обновляемых полей
    EXCLUDE_KEYS = {"user_id", "tg_id", "target_username", "id", "embedder"}
    
    update_fields = {}
    for key, val in kwargs.items():
        if key in EXCLUDE_KEYS or val is None:
            continue
        
        # Если меняется логин пользователя — зачищаем @
        if key == "username" and isinstance(val, str):
            val = val.strip().lstrip("@")

        # В схеме user_category, в базе category
        if key == "user_category":
            key = "category"
            
        update_fields[key] = val

    # 3. Автоматический расчет aliases_vector при изменении aliases
    if "aliases" in update_fields:
        aliases_text = update_fields["aliases"]
        if aliases_text and str(aliases_text).strip():
            emb_service = embedder or kwargs.get("embedder") or globals().get("embedder")
            if emb_service:
                try:
                    vector = await embedder.get_embedding(str(aliases_text).strip())
                    if vector:
                        update_fields["aliases_vector"] = vector
                except Exception as e:
                    logger.error(f"[update_user] Ошибка генерации эмбеддинга для aliases: {e}")
        else:
            # Если алиасы занулили или очистили — сбрасываем и вектор
            update_fields["aliases_vector"] = None

    # 4. Проверяем, передал ли AI хоть одно поле для изменения
    if not update_fields:
        return f"Error: No fields provided to update for user ({target_label})."

    # Объединяем идентификатор и редактируемые поля
    user_data.update(update_fields)

    # 5. Вызываем метод БД
    success = await db_users.db_update_user(user_data)

    if not success:
        logger.error("Failed to update user %s", target_label)
        return f"Error: User '{target_label}' not found or database update failed."

    # 6. Подтверждение для LLM (скрываем внутреннее векторное поле из ответа)
    changed_keys = ", ".join([k for k in update_fields.keys() if k != "aliases_vector"])
    logger.info("Successfully updated User (%s) fields: %s", target_label, changed_keys)
    return f"Success: User ({target_label}) updated. Fields changed: [{changed_keys}]."




# async def update_user(
#     user_id: int | None = None,
#     tg_id: int | None = None,
#     target_username: str | None = None,
#     embedder=None,
#     db_users=None,
#     **kwargs
# ) -> str:
#     """Обновление профиля пользователя по одному из идентификаторов."""

#     if not db_users:
#         return "Error: Database service 'db_users' is not available."
    
#     user_data = {}
#     target_label = ""

#     # 1. Выбираем СТРОГО один приоритетный идентификатор для поиска
#     if user_id:
#         user_data["id"] = user_id
#         target_label = f"ID #{user_id}"
#     elif tg_id:
#         user_data["tg_id"] = tg_id
#         target_label = f"TG ID #{tg_id}"
#     elif target_username:
#         clean_target = target_username.strip().lstrip("@")
#         user_data["username"] = clean_target
#         target_label = f"@{clean_target}"
#     else:
#         logger.warning("Attempted to call update_user without any target identifier.")
#         return "Error: Provide at least one identifier (user_id, tg_id, or target_username)."

#     # 2. Исключаем ключи-идентификаторы из kwargs, чтобы AI не изменил случайно ключевые ID
#     IDENTIFIER_KEYS = {"user_id", "tg_id", "target_username", "id"}
    
#     # Собираем только валидные поля для изменения
#     update_fields = {}
#     for key, val in kwargs.items():
#         if key in IDENTIFIER_KEYS or val is None:
#             continue
        
#         # Если меняется логин пользователя (колонка username) — зачищаем @
#         if key == "username" and isinstance(val, str):
#             val = val.strip().lstrip("@")

#         # В схеме разделяем - user_category, в базе category
#         if key == "user_category":
#             key = "category"
            
#         update_fields[key] = val

#     # 3. Проверяем, передал ли AI хоть одно поле для изменения
#     if not update_fields:
#         return f"Error: No fields provided to update for user ({target_label})."

#     # Объединяем идентификатор и редактируемые поля в один словарь для db_update_user
#     user_data.update(update_fields)

#     # 4. Вызываем функцию БД (db_update_user возвращает True / False)
#     success = await db_users.db_update_user(user_data)

#     if not success:
#         logger.error("Failed to update user %s", target_label)
#         return f"Error: User '{target_label}' not found or database update failed."

#     # 5. Возвращаем чёткое подтверждение для LLM
#     changed_keys = ", ".join(update_fields.keys())
#     logger.info("Successfully updated User (%s) fields: %s", target_label, changed_keys)
#     return f"Success: User ({target_label}) updated. Fields changed: [{changed_keys}]."



async def search_users(
    query: str = None,
    user_id: int = None,
    tg_id: int = None,
    category: str = None,
    limit: int = 5,
    db_users=None,
    embedder=None, # Заебал...
    **kwargs  # Защита от лишних аргументов LLM
) -> str:
    """Инструмент поиска пользователей с выводом всех полей профиля (кроме эмбеддинга)."""
    if not db_users:
        return "Error: Users service is unavailable."

    emb_service = embedder or kwargs.get("embedder") or globals().get("embedder")

    vector = None
    if query and str(query).strip():
        if not emb_service:
            logger.warning("[search_users] Embedder is not available for semantic query.")
            return "Error: Embedding service is not configured for text search."

        try:
            vector = await emb_service.get_embedding(str(query).strip())
        except Exception as e:
            logger.error("[search_users] Ошибка получения эмбеддинга: %s", e)

    # Поиск в БД по всем переданным фильтрам
    users = await db_users.search_users(
        query=query,
        vector=vector,
        user_id=user_id,
        tg_id=tg_id,
        category=category,
        limit=limit
    )

    if not users:
        return "No users found matching the specified search criteria."

    # Форматирование полной информации обо всех полях (без aliases_vector)
    formatted_blocks = []
    for u in users:
        uid = u.get("id", "N/A")
        utg = u.get("tg_id") or "N/A"
        username = f"@{u['username']}" if u.get("username") else "no-username"
        name = u.get("full_name") or "N/A"
        phone = u.get("phone") or "N/A"
        cat = u.get("category", "not_defined")
        lang = u.get("lang_code") or "ru"
        
        # Флаги статуса
        flags = []
        if u.get("is_admin"): flags.append("ADMIN")
        if u.get("is_blocked"): flags.append("BLOCKED")
        if u.get("is_whitelisted"): flags.append("WHITELISTED")
        if u.get("is_bot"): flags.append("BOT")
        flags_str = ", ".join(flags) if flags else "regular"

        # Назначенные модели
        models = []
        if m_def := u.get("model_default"): models.append(f"default={m_def}")
        if m_cheap := u.get("model_cheap"): models.append(f"cheap={m_cheap}")
        if m_smart := u.get("model_smart"): models.append(f"smart={m_smart}")
        models_str = ", ".join(models) if models else "default"

        # Форматирование дат
        c_at = u.get("created_at")
        c_str = c_at.strftime("%Y-%m-%d %H:%M") if hasattr(c_at, "strftime") else str(c_at or "N/A")

        lines = [
            f"• [User #{uid} | TG: {utg} | Username: {username}]",
            f"  Name: {name} | Phone: {phone} | Category: '{cat}' | Lang: {lang}",
            f"  Flags: [{flags_str}] | Models: [{models_str}]"
        ]

        if u.get("aliases"):
            lines.append(f"  Aliases: {u['aliases']}")
        if u.get("comment"):
            lines.append(f"  Comment: {u['comment']}")
        if u.get("summary"):
            lines.append(f"  AI Summary: {u['summary']}")

        lines.append(f"  Registered: {c_str}")

        formatted_blocks.append("\n".join(lines))

    header = f"=== Found Users ({len(formatted_blocks)}) ==="
    return f"{header}\n\n" + "\n\n".join(formatted_blocks)




#####  MESAGESS  ######

async def msg_search(
    query: str, 
    chat_id: int = None, 
    limit: int = 5,
    db_messages=None,
    embedder=None,
    **kwargs  # Защита от лишних аргументов LLM
) -> str:
    """
    Выполняет семантический векторный поиск по истории сообщений в БД
    и возвращает отформатированный результат для LLM.
    """
    if not db_messages:
        return "Error: Database service 'db_messages' is not available."

    # Извлечение эмбеддера из аргументов или контекста
    emb_service = embedder or kwargs.get("embedder")
    if not emb_service:
        try:
            emb_service = globals().get("embedder")
        except Exception:
            pass

    if not emb_service:
        return "Error: Embedding service is not configured."

    try:
        embedding_query = await emb_service.get_embedding(query)
    except Exception as e:
        logger.error(f"[msg_search] Ошибка генерации эмбеддинга для '{query}': {e}")
        return "Error: Could not process search query embedding."

    if not embedding_query:
        logger.error("[msg_search] Вектор для запроса '%s' не получен.", query)
        return "Error: Could not process search query embedding."

    # Векторный поиск по таблице messages
    messages: list[dict] = await db_messages.search_similar_messages(
        embedding=embedding_query, 
        chat_id=chat_id, 
        limit=limit
    )

    if not messages:
        logger.info(f"[msg_search] Релевантных сообщений не найдено (chat_id={chat_id}).")
        return f"No semantically relevant messages found for chat_id={chat_id or 'ALL'}."

    formatted_lines = []
    for m in messages:
        msg_id = m.get("id", "N/A")
        tg_msg_id = m.get("tg_msg_id", "N/A")
        m_chat_id = m.get("chat_id", "N/A")
        sender_id = m.get("sender_id", "N/A")
        msg_type = m.get("msg_type", "text")
        
        # Процент схожести
        similarity = m.get("similarity")
        sim_str = f" | Sim: {similarity:.2f}" if similarity is not None else ""
        
        # Разбор направления согласно CHECK (direction IN ('inbound_peer', 'outbound_owner'))
        direction = m.get("direction", "")
        if direction == "inbound_peer":
            dir_label = "IN (peer)"
        elif direction == "outbound_owner":
            dir_label = "OUT (owner)"
        else:
            dir_label = direction or "unknown"

        # Форматирование даты created_at (TIMESTAMPTZ)
        created_at = m.get("created_at")
        time_str = (
            created_at.strftime("%Y-%m-%d %H:%M")
            if hasattr(created_at, "strftime")
            else str(created_at or "N/A")
        )

        # Обработка контента с учетом типа медиа
        raw_content = m.get("content")
        if not raw_content:
            clean_content = f"[{msg_type.upper()} media content]"
        else:
            clean_content = raw_content.replace("\n", " ").strip()

        formatted_lines.append(
            f"• [ID: {msg_id} | TG_MSG: {tg_msg_id} | Chat: {m_chat_id} | Sender: {sender_id} | Dir: {dir_label} | Type: {msg_type} | Time: {time_str}{sim_str}]\n"
            f"  Content: \"{clean_content}\""
        )

    header = f"=== Found Similar Messages ({len(formatted_lines)}) ==="
    return f"{header}\n" + "\n\n".join(formatted_lines)




async def msg_range(
    start_id: int,
    end_id: int,
    db_messages=None,
    **kwargs  # Защита от лишних аргументов LLM
) -> str:
    """
    Вытаскивает диапазон сообщений по ID (start_id - end_id)
    и форматирует результат для LLM.
    """
    if not db_messages:
        return "Error: Database service 'db_messages' is not available."

    # 1. Защита: переворачиваем ID, если модель передала их задом наперед
    if start_id > end_id:
        start_id, end_id = end_id, start_id

    # 2. Защита от раздувания контекста (ограничение до 50 сообщений за раз)
    max_limit = 50
    if (end_id - start_id + 1) > max_limit:
        end_id = start_id + max_limit - 1

    messages: list[dict] = await db_messages.get_messages_context(
        start_id=start_id,
        end_id=end_id
    )

    if not messages:
        logger.info(f"[msg_range] Сообщения не найдены в диапазоне ({start_id}..{end_id}).")
        return f"No messages found in range [{start_id}..{end_id}]."

    formatted_lines = []
    for m in messages:
        msg_id = m.get("id", "N/A")
        tg_msg_id = m.get("tg_msg_id", "N/A")
        chat_id = m.get("chat_id", "N/A")
        msg_type = m.get("msg_type", "text")
        
        direction = m.get("direction", "")
        if direction == "inbound_peer":
            role_str = "IN (peer)"
        elif direction == "outbound_owner":
            role_str = "OUT (owner)"
        else:
            role_str = m.get("role", "unknown")

        created_at = m.get("created_at")
        time_str = (
            created_at.strftime("%Y-%m-%d %H:%M")
            if hasattr(created_at, "strftime")
            else str(created_at or "N/A")
        )

        raw_content = m.get("content") or f"[{msg_type} without text]"
        clean_content = raw_content.replace("\n", " ").strip()

        formatted_lines.append(
            f"• [ID: {msg_id} | TG_MSG: {tg_msg_id} | Chat: {chat_id} | Dir: {role_str} | Time: {time_str} | Type: {msg_type}]\n"
            f"  Content: \"{clean_content}\""
        )

    header = f"=== Dialog Range ID {start_id}..{end_id} ({len(formatted_lines)} msgs) ==="
    return f"{header}\n" + "\n\n".join(formatted_lines)





###### SEND MESSAGES TELETHONE #####



async def send_mess_peer(peer_id: int, text_mess: str, mytelethon, queue_new_mess) -> str:
    """
    Отправляет прямое сообщение пользователю Telegram через Telethon от имени Владельца
    и напрямую прокидывает событие в очередь для сброса виджета JumisAgent.
    """
    if not peer_id or not text_mess or not text_mess.strip():
        return "Error: Invalid arguments. Both 'peer_id' and non-empty 'text_mess' are required."

    # Жёсткая зачистка ИИ-артефактов перед отправкой
    clean_text = sanitize_human_text(text_mess)

    if not clean_text:
        return "Error: Message became empty after cleaning emojis and tags."

    try:
        answer = await mytelethon.send_message(
            message_text=clean_text,
            telegram_id=peer_id,
            username=None
        )

        # 1. Проверка на ошибки отправки (если отвалилось — в очередь НЕ кладем)
        if answer is None:
            logger.error(f"[send_mess_peer] Failed to send message to {peer_id}: returned None")
            return f"Error: Message to {peer_id} was not sent (service returned None)."

        if isinstance(answer, str):
            logger.error(f"[send_mess_peer] Telethon error for {peer_id}: {answer}")
            return f"Error sending message to {peer_id}: {answer}"

        # 2. Успешно отправлено — извлекаем tg_msg_id и дату
        if isinstance(answer, int):
            tg_msg_id = answer
            created_at = datetime.now(timezone.utc)
        else:
            tg_msg_id = getattr(answer, "id", None)
            created_at = getattr(answer, "date", datetime.now(timezone.utc))

        # 3. Формируем таск для воркера/JumisAgent
        task_payload = {
            "chat_id": peer_id,
            "sender_id": ADMIN_ID,
            "recipient_id": peer_id,
            "tg_msg_id": tg_msg_id,
            "msg_db_id": None,
            "username": "admin",
            "content": clean_text,
            "direction": "outbound_owner",
            "msg_type": "text",
            "created_at": created_at
        }

        await queue_new_mess.put(task_payload)
        logger.info(f"[send_mess_peer] Message successfully sent to {peer_id} (tg_msg_id={tg_msg_id}) and pushed to queue.")

        return f"Success: Message successfully sent to peer_id {peer_id}."

    except Exception as e:
        logger.error(f"[send_mess_peer] Unexpected error for {peer_id}: {e}", exc_info=True)
        return f"Fatal error: Exception occurred while sending message: {type(e).__name__} - {str(e)}"






# async def send_mess_peer(peer_id: int, text_mess: str, mytelethon, queue_new_mess) -> str:
#     """
#     Отправляет прямое сообщение пользователю Telegram через Telethon от имени Владельца
#     и напрямую прокидывает событие в очередь для сброса виджета JumisAgent.
#     :param peer_id: Telegram ID получателя
#     :param text_mess: Согласованный текст сообщения
#     :param mytelethon: Клиент Telethon
#     :return: Понятный статус выполнения для LLM
#     """
#     if not peer_id or not text_mess or not text_mess.strip():
#         return "Error: Invalid arguments. Both 'peer_id' and non-empty 'text_mess' are required."

#     # Жёсткая зачистка ИИ-артефактов перед отправкой
#     clean_text = sanitize_human_text(text_mess)

#     if not clean_text:
#         return "Error: Message became empty after cleaning emojis and tags."

#     try:
#         answer = await mytelethon.send_message(
#             message_text=clean_text,
#             telegram_id=peer_id,
#             username=None
#         )

#         if answer is None:
#             logger.error(f"[send_mess_peer] Failed to send message to {peer_id}: returned None")
#             return f"Error: Message to {peer_id} was not sent (service returned None)."

#         ####### 

#         task_payload = {
#             "chat_id": peer_id,
#             "sender_id": ADMIN_ID,
#             "recipient_id": peer_id,

#             "tg_msg_id": ...,
#             "msg_db_id": None,

#             "username": "admin",
#             "content": clean_text,
#             "direction": "outbound_owner",
#             "msg_type": "text",
#             "created_at": ....
#         }

#         await queue_new_mess.put(task_payload)

#         ######

#         if isinstance(answer, int):
#             logger.info(f"[send_mess_peer] Message sent to {peer_id}")
#             return f"Success: Message successfully sent to peer_id {peer_id}."

#         if isinstance(answer, str):
#             logger.error(f"[send_mess_peer] Telethon error for {peer_id}: {answer}")
#             return f"Error sending message to {peer_id}: {answer}"

#         return f"Success: Message sent to {peer_id}." # ))))

#     except Exception as e:
#         logger.error(f"[send_mess_peer] Unexpected error for {peer_id}: {e}", exc_info=True)
#         return f"Fatal error: Exception occurred while sending message: {type(e).__name__} - {str(e)}"





async def clear_inbox_notifs(jumis_agent=None) -> str:
    """Clears all pending incoming Telegram message notifications and deletes the notification widget."""
    if not jumis_agent:
        return "Error: Agent service is unavailable."

    return await jumis_agent.clear_pending_queue()



async def get_pending_queue(jumis_agent=None) -> str:
    """Возвращает текущее состояние очереди неотвеченных сообщений."""
    if not jumis_agent:
        return "Error: Agent service is unavailable."

    return await jumis_agent.get_notifs_queue()




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
                "tg_id": {
                    "type": "integer", 
                    "description": "Target Telegram user ID. Omit if storing a global system instruction or personal rule for Jumis."
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
                "tg_id": {
                    "type": "integer", 
                    "description": "Telegram User ID. Omit or pass null if this fact becomes a global system rule."
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
                "tg_id": {
                    "type": "integer",
                    "description": "Optional user Telegram ID to search facts specific to a user."
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

    "get_categories_users": {
        "description": "Retrieve a list of all available user (client) categories and their descriptions.",
        "function": get_categories_users,
        "schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    "add_category_users": {
        "description": "Create a new category for classifying or segmenting users (clients).",
        "function": add_category_users,
        "schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short name of the user category (e.g., 'vip', 'service', 'wholesale')."
                },
                "description": {
                    "type": "string",
                    "description": "Detailed description of the category's purpose and assignment rules."
                }
            },
            "required": ["name"]
        }
    },

    "update_user": {
        "description": "Updates profile attributes, notes, traits, or settings for a user in the database.",
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
                "user_category": {"type": "string", "description": "Optional category name to group users."},
                "comment": {"type": "string", "description": "Personal manual note about the user."},
                "summary": {"type": "string", "description": "AI-generated summary of past dialogue context."},
                "aliases": {"type": "string", "description": "Comma-separated search traits, keywords, or aliases for vector index (e.g. 'Петя, электрик, младший брат')."},
                
                # --- Флаги доступа ---
                "is_admin": {"type": "boolean", "description": "Set admin status."},
                "is_blocked": {"type": "boolean", "description": "Block or unblock user."},
                "is_whitelisted": {"type": "boolean", "description": "Priority queue whitelist status."},
                
                # --- Настройки и модели ---
                "lang_code": {"type": "string", "description": "Language code (e.g. 'ru', 'en')."},
                "model_default": {"type": "string", "description": "Default model name (e.g. 'deepseek/deepseek-v4-flash')."},
                "model_cheap": {"type": "string", "description": "Cheap model identifier for lightweight tasks."},
                "model_smart": {"type": "string", "description": "Smart model identifier for complex reasoning."}
            },
            "required": []
        }
    },

    "search_users": {
        "description": "Search users in the database by exact ID, Telegram ID, category, or semantic query (username, full name, notes, or aliases/traits).",
        "function": search_users,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search text or phrase to match against username, full name, comments, or aliases/traits."
                },
                "user_id": {
                    "type": "integer",
                    "description": "Exact internal database primary key user ID."
                },
                "tg_id": {
                    "type": "integer",
                    "description": "Exact Telegram user ID."
                },
                "category": {
                    "type": "string",
                    "description": "Filter users by specific category name."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 5)."
                }
            },
            "required": []
        }
    },

    "msg_search": {
        "description": "Perform semantic vector search across chat messages to find contextually relevant conversation history.",
        "function": msg_search,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search text query or phrase to find contextually and semantically similar messages."
                },
                "chat_id": {
                    "type": "integer",
                    "description": "Optional. Telegram chat/user ID to restrict search to a specific conversation. Omit to search globally across all chats."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of semantically relevant messages to return (default: 5)."
                }
            },
            "required": ["query"]
        }
    },

    "msg_range": {
        "description": "Retrieves a slice of saved messages from DB by message ID range (start_id to end_id inclusive). Use this to read dialog context around specific message IDs.",
        "function": msg_range,
        "schema": {
            "type": "object",
            "properties": {
                "start_id": {
                    "type": "integer",
                    "description": "Starting message database ID (inclusive)."
                },
                "end_id": {
                    "type": "integer",
                    "description": "Ending message database ID (inclusive)."
                }
            },
            "required": ["start_id", "end_id"]
        }
    },

    "send_mess_peer": {
        "description": "Sends a Telegram message to a specific user (peer_id) on behalf of the Owner. MUST be called ONLY after explicit confirmation from the Owner.",
        "function": send_mess_peer,
        "schema": {
            "type": "object",
            "properties": {
                "peer_id": {
                    "type": "integer",
                    "description": "Telegram user ID (peer_id) of the recipient."
                },
                "text_mess": {
                    "type": "string",
                    "description": "The exact final message text approved by the Owner. MUST be plain text only: strictly NO emojis or formatting."
                }
            },
            "required": ["peer_id", "text_mess"]
        }
    },

    "clear_inbox_notifs": {
        "description": "Clears inbox notifications and widget. Call ONLY when explicitly requested by user. NEVER execute automatically.",
        "function": clear_inbox_notifs,
        "schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    "get_pending_queue": {
        "description": "Returns current pending/unread inbox messages queue. Call this if you need to re-check pending messages.",
        "function": get_pending_queue,
        "schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }

}





    # "get_users": {
    #     "description": "Retrieves a list of all registered users and clients in the system.",
    #     "function": get_users,
    #     "schema": {
    #         "type": "object",
    #         "properties": {},
    #         "required": []
    #     }
    # },

    # "get_user": {
    #     "description": "Retrieves user profile details by internal ID, Telegram ID, username, or semantically searches users by aliases/traits.",
    #     "function": get_user,
    #     "schema": {
    #         "type": "object",
    #         "properties": {
    #             "user_id": {
    #                 "type": "integer",
    #                 "description": "Internal database primary key user ID."
    #             },
    #             "tg_id": {
    #                 "type": "integer",
    #                 "description": "Telegram user ID."
    #             },
    #             "username": {
    #                 "type": "string",
    #                 "description": "Telegram username (e.g. 'john_doe')."
    #             }
    #         },
    #         "required": []
    #     }
    # },



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