# jumis/llm/functions.py
import asyncio
from typing import Any, Dict, List, Optional
from vector import embedder
from utils.common import sanitize_human_text, get_now_datetime, get_date_str, to_cleen, get_date_datetime, to_app_tz_naive
from datetime import datetime, timezone, timedelta
from croniter import croniter
from config import ADMIN_ID
from logs.set_logger import logger
# Регистрируют названия своих кронов: по ним задачи защищены от агента.
from cleaner.task import TITLE as _CLEANER_CRON_TITLE  # noqa: F401
from pgbackup.task import TITLE as _BACKUP_CRON_TITLE  # noqa: F401
from utils.system_cron import is_system_cron as is_system_task
import json





# Множество для хранения ссылок на активные фоновые задачи (защита от GC в Python 3.11+)
background_tasks = set()



###### REASONING ##########


async def think(thought: str, **kwargs) -> str:
    """Records an internal reasoning step. Changes nothing, returns a short ack."""
    clean = (thought or "").strip()
    if not clean:
        return "Error: 'thought' must not be empty."

    # Мысль не сохраняем (scratchpad) и не логируем целиком — только длину.
    logger.info("think: %d chars", len(clean))
    return "OK"



###### FINAL ANSWER / OPTIONS ##########

OPTIONS_MIN = 2
OPTIONS_MAX = 6
OPTION_MAX_LEN = 60
QUESTION_MAX_LEN = 300


async def ask_options(question: str = "", options: list = None, **kwargs) -> dict:
    """Финальный шаг ответа: нормализует варианты для кнопок.

    Сам ничего не отправляет и не пишет в БД — рендером кнопок и остановкой
    tool-loop занимается JumisAgent. Успешный результат содержит
    status="options_shown", по которому агент понимает, что итерацию надо закрыть.
    """
    if not isinstance(options, list):
        return {"status": "error", "message": "The 'options' argument must be an array of strings."}

    clean_options = []
    for raw in options:
        text_opt = str(raw or "").strip()
        if not text_opt or text_opt in clean_options:
            continue
        if len(text_opt) > OPTION_MAX_LEN:
            return {
                "status": "error",
                "message": (
                    f"Option '{text_opt[:30]}...' is too long ({len(text_opt)} chars). "
                    f"Shorten every option to {OPTION_MAX_LEN} characters or fewer and call again."
                ),
            }
        clean_options.append(text_opt)

    if len(clean_options) < OPTIONS_MIN:
        return {
            "status": "error",
            "message": f"Provide at least {OPTIONS_MIN} distinct non-empty options.",
        }

    clean_options = clean_options[:OPTIONS_MAX]

    clean_question = str(question or "").strip()
    if len(clean_question) > QUESTION_MAX_LEN:
        clean_question = clean_question[:QUESTION_MAX_LEN].rstrip()

    logger.info("ask_options: %d options, question_len=%d", len(clean_options), len(clean_question))
    return {"status": "options_shown", "question": clean_question, "options": clean_options}



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
        if embedder:
            try:
                embedding = await embedder.get_embedding(content)
            except Exception as e:
                logger.error("[background_vectorize_fact] Ошибка получения эмбеддинга: %s", e)
        else:
            logger.warning("[background_vectorize_fact] Embedder не передан.")

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


    if embedder:
        try:
            embedding_query = await embedder.get_embedding(query)
        except Exception as e:
            logger.error("[search_facts] Ошибка получения эмбеддинга: %s", e)
    else:
        logger.warning("[search_facts] Embedder не передан")


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
        if embedder:
            try:
                update_fields["embedding"] = await embedder.get_embedding(content.strip())
            except Exception as e:
                logger.error("[update_fact] Ошибка получения эмбеддинга: %s", e)
        else:
            logger.warning("[update_fact] Embedder не передан")


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




async def update_user(
    user_id: int | None = None,
    tg_id: int | None = None,
    target_username: str | None = None,
    db_users=None,
    llm=None,
    **kwargs
) -> str:
    """Обновление профиля пользователя по одному из идентификаторов."""
    if not db_users:
        return "Error: Database service 'db_users' is not available."

    user_data = {}

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
    EXCLUDE_KEYS = {"user_id", "tg_id", "target_username", "id", "embedder", "db_users"}
    
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

    # 3. Пересчет вектора алиасов, если они переданы
    if "aliases" in update_fields:
        aliases_text = str(update_fields["aliases"] or "").strip()
        
        if aliases_text:
            if embedder:
                try:
                    update_fields["aliases_vector"] = await embedder.get_embedding(aliases_text)
                except Exception as e:
                    logger.error(f"[update_user] Ошибка генерации эмбеддинга: {e}")
            else:
                logger.warning("[update_user] Переданы aliases, но embedder не предоставлен.")
        else:
            # Если алиасы очистили/занулили — сбрасываем вектор
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

    # 6. Если обновились модели у пользователя, обновляем в классе LLM
    model_keys = {"model_default", "model_cheap", "model_smart"}
    if any(k in update_fields for k in model_keys):
        if llm:
            if await llm.refresh_llm_models():
                logger.info(f"[update_user] LLM models cache refreshed after updating {target_label}.")
            else:
                logger.error(f"[update_user] Failed to refresh LLM models cache for {target_label}.")
        else:
            logger.warning("[update_user] Models were updated, but LLM instance was not provided to refresh cache.")

    # 7. Подтверждение для LLM (скрываем внутреннее векторное поле из ответа)
    changed_keys = ", ".join([k for k in update_fields.keys() if k != "aliases_vector"])
    logger.info("Successfully updated User (%s) fields: %s", target_label, changed_keys)
    return f"Success: User ({target_label}) updated. Fields changed: [{changed_keys}]."



async def search_users(
    query: str = None,
    username: str = None,
    tg_id: int = None,
    category: str = None,
    limit: int = 5,
    db_users=None,
    **kwargs  # Защита от лишних аргументов LLM
) -> str:
    """Инструмент поиска пользователей с поддержкой векторного и текстового поиска."""
    if not db_users:
        return "Error: Users service is unavailable."

    vector = None

    # 1. Если есть текстовый запрос и доступен embedder — генерируем вектор
    if query and str(query).strip():
        clean_query = str(query).strip()
        if embedder:
            try:
                vector = await embedder.get_embedding(clean_query)
            except Exception as e:
                logger.error("[search_users] Ошибка получения эмбеддинга: %s", e)
        else:
            logger.warning("[search_users] Embedder не передан, поиск будет только по тексту.")

    # 2. Поиск в БД (БД сама решает: искать по vector, по query или гибридно)
    users = await db_users.search_users(
        query=query,
        vector=vector,
        username=username,
        tg_id=tg_id,
        category=category,
        limit=limit
    )

    if not users:
        return "No users found matching the specified search criteria."

    # 3. Форматирование полной информации обо всех полях (без aliases_vector)
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
    **kwargs  # Защита от лишних аргументов LLM
) -> str:
    """
    Выполняет семантический векторный поиск по истории сообщений в БД
    и возвращает отформатированный результат для LLM.
    """
    if not db_messages:
        return "Error: Database service 'db_messages' is not available."

    if embedder:
        try:
            embedding_query = await embedder.get_embedding(query)
        except Exception as e:
            logger.error(f"[msg_search] Ошибка генерации эмбеддинга для '{query}': {e}")
            return "Error: Could not process search query embedding."
        else:
            logger.warning("[msg_search] Embedder не передан")


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



async def send_mess_peer(
    text_mess: str,
    peer_id: int | None = None,
    username: str | None = None,
    mytelethon=None,
    queue_new_mess=None
) -> str:
    """
    Отправляет прямое сообщение пользователю/каналу Telegram через Telethon от имени Владельца
    и прокидывает событие в очередь для сброса виджета JumisAgent.
    """

    # 1. Валидация входных параметров
    if not (peer_id or username):
        return "Error: Invalid arguments. Provide 'peer_id' or 'username'."
    
    if not text_mess or not text_mess.strip():
        return "Error: Invalid arguments. Provide 'text_mess'."

    # Формируем удобную метку для логов и ответов
    target_type = "user_id" if peer_id else "username"
    target_val = peer_id or username
    target_str = f"{target_type}:{target_val}"

    clean_text = sanitize_human_text(text_mess)
    if not clean_text:
        return "Error: Message became empty after cleaning emojis and tags."

    try:
        answer: int | str = await mytelethon.send_message(
            message_text=clean_text,
            telegram_id=peer_id,
            username=username
        )

        # 2. Если вернулась строка — это ошибка отправки
        if isinstance(answer, str):
            logger.error(f"[send_mess_peer] Telethon error for target ({target_str}): {answer}")
            return f"Error sending message ({target_str}): {answer}"

        if answer is None:
            logger.error(f"[send_mess_peer] Failed to send message to target ({target_str}): returned None")
            return "Error: Message was not sent (service returned None)."

        # 3. Успешно отправлено — извлекаем метаданные ответа
        if isinstance(answer, int):
            tg_msg_id = answer
            created_at = get_now_datetime()
            
            if answer < 0:
                text_answer = f"[send_mess_peer] Target is CHANNEL/GROUP ({target_str})"
            else:
                text_answer = f"[send_mess_peer] Target is USER ({target_str})"
            
            logger.info(text_answer)
        else:
            tg_msg_id = getattr(answer, "id", None)
            raw_date = getattr(answer, "date", None)
            created_at = to_cleen(raw_date) if isinstance(raw_date, datetime) else get_now_datetime()
            
            text_answer = f"[send_mess_peer] Target returned Telethon object ({target_str}) (tg_msg_id={tg_msg_id})"
            logger.info(text_answer)


        # 4. Формируем таск для воркера/JumisAgent 
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

        if queue_new_mess:
            await queue_new_mess.put(task_payload)
            logger.info(f"[send_mess_peer] Message successfully sent to ({target_str}) (tg_msg_id={tg_msg_id}) and pushed to queue.")

        return f"Success: Message successfully sent to ({target_str})."
    
    except Exception as e:
        logger.error(f"[send_mess_peer] Unexpected error for ({target_str}): {e}", exc_info=True)
        return f"Fatal error: Exception occurred while sending message: {type(e).__name__} - {str(e)}"





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





##### TASKS #######

async def add_task(scheduler, db_tasks, **kwargs) -> str:
    """Создает и регистрирует новую задачу (разовую или Cron)."""

    header = "=== Task Creation Status ==="

    if not scheduler:
        logger.error("[add_task] SmartScheduler instance is missing or None.")
        return f"{header}\n[CRITICAL ERROR] Scheduler instance is unavailable."

    if not db_tasks:
        logger.error("[add_task] Database handler (db_tasks) is missing or None.")
        return f"{header}\n[CRITICAL ERROR] Database connection is unavailable."

    # Отфильтровываем None значения
    task_data = {key: val for key, val in kwargs.items() if val is not None}

    title = task_data.get("title")
    agent_instruction = task_data.get("agent_instruction")
    scheduled_at_raw = task_data.get("scheduled_at")
    cron_expression = task_data.get("cron_expression")

    # По умолчанию тип 'reminder', если ничего не передали
    task_type = task_data.get("task_type", "reminder")
    task_data["task_type"] = task_type

    # Валидация обязательных полей
    if not title or not agent_instruction:
        logger.warning(f"[add_task] Missing mandatory fields: {task_data}")
        return (
            f"{header}\n"
            f"[INVALID PARAMETERS] Task creation failed. Mandatory parameters missing: "
            f"'title' and 'agent_instruction' are required."
        )
    
    now = get_now_datetime()

    # 1. Приведение scheduled_at к чистой настенной дате (Naive datetime)
    parsed_scheduled_at = None
    if scheduled_at_raw:
        if isinstance(scheduled_at_raw, str):
            try:
                # Очищаем от tzinfo, чтобы не было конфликтов с настенным временем
                parsed_scheduled_at = get_date_datetime(scheduled_at_raw)
            except ValueError as e:
                logger.error(f"[add_task] Ошибка формата даты: {e}")
                return f"{header}\n[INVALID PARAMETERS] Неверный формат даты ISO: {scheduled_at_raw}"
        elif isinstance(scheduled_at_raw, datetime):
            parsed_scheduled_at = to_cleen(scheduled_at_raw)

    # 2. Валидация и расчет времени для ВСЕХ CRON задач (любого task_type)
    if cron_expression:
        if not croniter.is_valid(cron_expression):
            logger.error(f"[add_task] Невалидное Cron-выражение: {cron_expression}")
            return f"{header}\n[INVALID PARAMETERS] Ошибка: Невалидное Cron-выражение '{cron_expression}'."

        # Если дата не передана или переданный scheduled_at уже в прошлом — считаем следующий запуск
        if not parsed_scheduled_at or parsed_scheduled_at <= now:
            parsed_scheduled_at = croniter(cron_expression, now).get_next(datetime)
            logger.info(f"[add_task] Авто-расчет первого запуска Cron-задачи: {parsed_scheduled_at}")

    # Для разовых задач дата обязательна
    elif not parsed_scheduled_at:
        return (
            f"{header}\n"
            f"[INVALID PARAMETERS] For one-time tasks, 'scheduled_at' is required."
        )

    task_data["scheduled_at"] = parsed_scheduled_at

    try:
        task_id = await db_tasks.db_add_task(task_data)

        if task_id:
            # Будим поток планировщика для мгновенного перерасчета времени сна
            scheduler.notify_new_task()

            logger.info(
                f"[add_task] Task #{task_id} ('{title}') [{task_type}] "
                f"successfully created for {parsed_scheduled_at} (cron: {cron_expression or 'N/A'})."
            )
            return (
                f"{header}\n"
                f"[SUCCESS] Task #{task_id} successfully created and queued!\n"
                f"• Task ID: {task_id}\n"
                f"• Type: {task_type}\n"
                f"• Title: {title}\n"
                f"• Scheduled Time: {parsed_scheduled_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"• Cron Expression: {cron_expression or 'N/A'}"
            )

        logger.error(f"[add_task] DB returned no task ID for payload: {task_data}")
        return f"{header}\n[FAILED] Unable to save the scheduled task to database."

    except Exception as e:
        logger.error(f"[add_task] Unexpected error during task creation: {e}", exc_info=True)
        return f"{header}\n[EXCEPTION] Failed to create task due to an internal error: {e}"



async def update_task(scheduler, db_tasks, **kwargs) -> str:
    """Обновляет параметры существующей задачи."""
    header = "=== Task Update Status ==="

    if not scheduler:
        logger.error("[update_task] SmartScheduler instance is missing or None.")
        return f"{header}\n[CRITICAL ERROR] Scheduler instance is unavailable."

    if not db_tasks:
        logger.error("[update_task] Database handler (db_tasks) is missing or None.")
        return f"{header}\n[CRITICAL ERROR] Database connection is unavailable."

    # Отфильтровываем None значения
    task_data = {key: val for key, val in kwargs.items() if val is not None}

    task_id = task_data.get("id")
    if not task_id:
        logger.warning("[update_task] Update failed: missing 'id' parameter.")
        return f"{header}\n[INVALID PARAMETERS] Task update failed. Mandatory parameter 'id' is required."

    if len(task_data) <= 1:
        logger.warning(f"[update_task] No fields to update provided for task #{task_id}.")
        return f"{header}\n[NO CHANGES] No valid update parameters provided for task #{task_id}."

    # У системной задачи можно менять только статус (пауза/возобновление),
    # но не название, тип, cron и инструкцию — иначе крон потеряет обработчик.
    protected_fields = {"title", "cron_expression", "task_type", "agent_instruction"}
    if protected_fields & set(task_data.keys()):
        try:
            found = await db_tasks.db_search_tasks(task_id=task_id, limit=1)
            if found and is_system_task(found[0]):
                logger.warning("[update_task] Отказ: задача #%s системная, защищённые поля.", task_id)
                return (
                    f"{header}\n[FORBIDDEN] Task #{task_id} is a system task: its title, type, "
                    "cron expression and instruction cannot be changed. Only 'status' may be updated."
                )
        except Exception as e:
            logger.warning("[update_task] Не удалось проверить тип задачи #%s: %s", task_id, e)

    # 1. Приведение scheduled_at к настенному datetime
    if "scheduled_at" in task_data:
        raw_scheduled = task_data["scheduled_at"]
        if isinstance(raw_scheduled, str):
            try:
                task_data["scheduled_at"] = get_date_datetime(raw_scheduled)
            except ValueError as e:
                logger.error(f"[update_task] Ошибка формата даты: {e}")
                return f"{header}\n[INVALID PARAMETERS] Неверный формат даты ISO: {raw_scheduled}"
        elif isinstance(raw_scheduled, datetime):
            task_data["scheduled_at"] = raw_scheduled.replace(tzinfo=None)

    # 2. Если меняется cron_expression, пересчитываем ближайший запуск (если scheduled_at не передали вручную)
    cron_expr = task_data.get("cron_expression")
    if cron_expr:
        if not croniter.is_valid(cron_expr):
            logger.error(f"[update_task] Невалидное Cron-выражение: {cron_expr}")
            return f"{header}\n[INVALID PARAMETERS] Ошибка: Невалидное Cron-выражение '{cron_expr}'."
        
        if "scheduled_at" not in task_data:
            now = get_now_datetime()
            task_data["scheduled_at"] = croniter(cron_expr, now).get_next(datetime)

    try:
        success = await db_tasks.db_update_task(task_data)

        if success:
            # Будим поток планировщика для мгновенного перерасчета времени сна
            scheduler.notify_new_task()

            logger.info(f"[update_task] Task #{task_id} updated successfully.")
            
            updated_fields = ", ".join([k for k in task_data.keys() if k != "id"])
            return (
                f"{header}\n"
                f"[SUCCESS] Task #{task_id} successfully updated!\n"
                f"• Task ID: {task_id}\n"
                f"• Updated Fields: {updated_fields}"
            )

        logger.error(f"[update_task] DB update returned false or task #{task_id} not found.")
        return f"{header}\n[FAILED] Task #{task_id} was not found or could not be updated."

    except Exception as e:
        logger.error(f"[update_task] Unexpected error while updating task #{task_id}: {e}", exc_info=True)
        return f"{header}\n[EXCEPTION] Failed to update task #{task_id} due to an internal error: {e}"



async def search_tasks(db_tasks, id: int = None, status: str = None, limit: int = 20) -> str:
    """
    Retrieves and formats a list of scheduled tasks based on ID or status filtering for Agent context.

    Args:
        db_tasks: Database handler instance for scheduled tasks.
        id (int, optional): Specific task ID to search for.
        status (str, optional): Filter by task status ('pending', 'running', 'completed', 'cancelled').
        limit (int, optional): Maximum number of tasks to return. Defaults to 20.

    Returns:
        str: Formatted string containing the list of matching tasks or status message.
    """
    header_title = "=== Scheduled Tasks ==="

    if not db_tasks:
        logger.error("[search_tasks] Database handler (db_tasks) is missing or None.")
        return f"{header_title}\n[CRITICAL ERROR] Database connection is unavailable."

    try:
        tasks: List[Dict[str, Any]] = await db_tasks.db_search_tasks(task_id=id, status=status, limit=limit)

        if not tasks:
            logger.info(f"[search_tasks] No tasks found for criteria (id={id}, status='{status}').")
            return f"{header_title} (0)\n[NO TASKS FOUND] No tasks matched the specified criteria."

        formatted_lines = []
        for task in tasks:
            t_id = task.get("id", "N/A")
            t_title = task.get("title", "Untitled")
            t_status = task.get("status", "unknown")
            t_type = task.get("task_type", "reminder")
            t_scheduled = task.get("scheduled_at", "N/A")
            t_instruction = task.get("agent_instruction", "").strip()

            # Truncate long instructions to keep agent context clean
            # short_instruction = (t_instruction[:120] + "...") if len(t_instruction) > 120 else t_instruction

            item = (
                f"• Task #{t_id} | Status: [{t_status.upper()}] | Type: {t_type} | Scheduled: {t_scheduled}\n"
                f"  Title: {t_title}\n"
                f"  Instruction: {t_instruction}"
            )
            formatted_lines.append(item)

        header = f"=== Scheduled Tasks List ({len(tasks)}) ==="
        logger.info(f"[search_tasks] Successfully retrieved {len(tasks)} tasks.")
        return f"{header}\n" + "\n\n".join(formatted_lines)

    except Exception as e:
        logger.error(f"[search_tasks] Unexpected error searching tasks: {e}", exc_info=True)
        return f"{header_title}\n[EXCEPTION] Failed to retrieve tasks due to an internal error: {e}"


async def del_task(id: int, db_tasks, scheduler) -> str:
    """
    Deletes a scheduled task by its unique ID and returns a structured response for the Agent.

    Args:
        id (int): Database ID of the task to be deleted.
        db_tasks: Database handler instance for scheduled tasks.

    Returns:
        str: Formatted string header and status message for Agent context.
    """
    header = "=== Task Deletion Status ==="

    if not db_tasks:
        logger.error("[del_task] Database handler (db_tasks) is missing or uninitialized.")
        return f"{header}\n[CRITICAL ERROR] Database connection is unavailable."

    # Системные задачи (ночная уборка и пр.) удалять запрещено — только пауза.
    if db_tasks is not None:
        try:
            found = await db_tasks.db_search_tasks(task_id=id, limit=1)
            if found and is_system_task(found[0]):
                logger.warning("[del_task] Отказ: задача #%s — системная.", id)
                return (
                    f"{header}\n[FORBIDDEN] Task #{id} is a system task and cannot be deleted.\n"
                    "It can be paused with update_task(status='cancelled') if really needed."
                )
        except Exception as e:
            logger.warning("[del_task] Не удалось проверить тип задачи #%s: %s", id, e)

    try:
        is_deleted = await db_tasks.db_del_task(id)

        if is_deleted:
            # Instantly wake up the scheduler loop to recalculate sleep timer
            scheduler.notify_new_task()
            logger.info(f"[del_task] Task #{id} deleted successfully.")
            return f"{header}\n[SUCCESS] Task #{id} has been permanently deleted from the scheduler."
        else:
            logger.warning(f"[del_task] Task #{id} was not found or could not be deleted.")
            return f"{header}\n[NOT FOUND / FAILED] Task #{id} does not exist or could not be removed."

    except Exception as e:
        logger.error(f"[del_task] Unexpected error while deleting task #{id}: {e}", exc_info=True)
        return f"{header}\n[EXCEPTION] Failed to delete task #{id} due to an internal error: {e}"



###### CLEANER: HISTORY MAINTENANCE ######

async def cleanup_scan(cleaner=None) -> str:
    """Dry run: reports how many junk and duplicate messages would be deleted. Nothing is deleted."""
    header = "=== Cleanup Scan (dry run) ==="

    if not cleaner:
        logger.error("[cleanup_scan] Cleaner service is not available.")
        return f"{header}\n[CRITICAL ERROR] Cleaner service is unavailable."

    try:
        stats = await cleaner.scan()
    except Exception as e:
        logger.error("[cleanup_scan] Scan failed: %s", e, exc_info=True)
        return f"{header}\n[EXCEPTION] Scan failed: {e}"

    return (
        f"{header}\n"
        f"• Messages in DB: {stats['total']}\n"
        f"• Empty / media without text: {stats['empty'] + stats['media']}\n"
        f"• Chatter ('ok', ')', 'hi'...): {stats['chatter']}\n"
        f"• Exact duplicates (same chat + day): {stats['dups']}\n"
        f"• TOTAL to delete: {stats['candidates']}\n"
        f"• Will remain: {stats['after']}"
    )


async def cleanup_run(confirm: bool = False, cleaner=None) -> str:
    """Permanently deletes junk and duplicate messages from history, then vacuums the table. Requires confirm=true."""
    header = "=== Cleanup Run ==="

    if not cleaner:
        logger.error("[cleanup_run] Cleaner service is not available.")
        return f"{header}\n[CRITICAL ERROR] Cleaner service is unavailable."

    if not confirm:
        return (
            f"{header}\n[NOT EXECUTED] This permanently deletes messages. "
            "Run cleanup_scan first to preview, then call again with confirm=true."
        )

    try:
        report = await cleaner.run_hygiene()
    except Exception as e:
        logger.error("[cleanup_run] Cleanup failed: %s", e, exc_info=True)
        return f"{header}\n[EXCEPTION] Cleanup failed: {e}"

    return (
        f"{header}\n"
        f"• Junk deleted: {report['deleted_junk']}\n"
        f"• Duplicates deleted: {report['deleted_dups']}\n"
        f"• Messages: {report['total_before']} -> {report['total_after']}\n"
        f"• Table size: {report['size_before']} -> {report['size_after']}\n"
        f"• Vacuum: {'ok' if report['vacuum'] else 'skipped (see logs)'}"
    )


async def digest_run(max_batches: int = 3, cleaner=None) -> str:
    """Compresses the oldest history batches into digests. Raw messages are deleted after the digest is stored."""
    header = "=== History Digest Run ==="

    if not cleaner:
        logger.error("[digest_run] Cleaner service is not available.")
        return f"{header}\n[CRITICAL ERROR] Cleaner service is unavailable."

    try:
        batches = max(1, min(int(max_batches or 1), 20))
    except (TypeError, ValueError):
        batches = 1

    try:
        report = await cleaner.run_digest(max_batches=batches)
    except Exception as e:
        logger.error("[digest_run] Digest failed: %s", e, exc_info=True)
        return f"{header}\n[EXCEPTION] Digest failed: {e}"

    if report.get("skipped"):
        reason = report.get("reason")
        if reason == "empty_backlog":
            return f"{header}\n[NOTHING TO DO] No messages older than the quarantine window."
        if reason == "already_running":
            return f"{header}\n[BUSY] A digest run is already in progress."
        return f"{header}\n[SKIPPED] {reason}"

    return (
        f"{header}\n"
        f"• Batches compressed: {report.get('batches', 0)}\n"
        f"• Messages digested: {report.get('messages', 0)}\n"
        f"• Chats touched: {report.get('chats', 0)}\n"
        f"• Failed batches: {report.get('failed', 0)}"
    )


async def digest_list(chat_id: int = None, limit: int = 5, cleaner=None) -> str:
    """Lists stored chat digests (compressed history) for one chat or for all chats."""
    header = "=== Chat Digests ==="

    if not cleaner:
        logger.error("[digest_list] Cleaner service is not available.")
        return f"{header}\n[CRITICAL ERROR] Cleaner service is unavailable."

    try:
        rows = await cleaner.list_digests(chat_id=chat_id, limit=max(1, min(int(limit or 5), 20)))
    except Exception as e:
        logger.error("[digest_list] Failed: %s", e, exc_info=True)
        return f"{header}\n[EXCEPTION] {e}"

    if not rows:
        return f"{header}\nNo digests found."

    lines = []
    for row in rows:
        period_from = row.get("period_from")
        period_to = row.get("period_to")
        lines.append(
            f"• [#{row.get('id')} | Chat: {row.get('chat_id')} | "
            f"{to_cleen(period_from):%d.%m.%Y} — {to_cleen(period_to):%d.%m.%Y} | "
            f"msgs: {row.get('msg_count')}]\n"
            f"  {str(row.get('summary') or '').strip()[:400]}"
        )

    return f"{header} ({len(lines)})\n" + "\n\n".join(lines)


###### PGBACKUP: DATABASE BACKUP ######

async def backup_now(backups=None) -> str:
    """Creates a full pg_dump backup of the database right now and rotates old copies."""
    header = "=== Database Backup ==="

    if not backups:
        logger.error("[backup_now] Backup service is not available.")
        return f"{header}\n[CRITICAL ERROR] Backup service is unavailable."

    report = await backups.create()

    if not report.get("ok"):
        reason = report.get("error")
        if reason == "already_running":
            return f"{header}\n[BUSY] A backup is already in progress."
        return f"{header}\n[FAILED] {reason}"

    return (
        f"{header}\n"
        f"• File: {report['name']}\n"
        f"• Size: {report['size_human']}\n"
        f"• Duration: {report['duration']} s\n"
        f"• Old copies deleted: {len(report.get('deleted') or [])}\n"
        f"• Copies kept: {report.get('keep')}"
    )


async def backup_list(limit: int = 10, backups=None) -> str:
    """Lists the stored database backup files, newest first."""
    header = "=== Database Backups ==="

    if not backups:
        logger.error("[backup_list] Backup service is not available.")
        return f"{header}\n[CRITICAL ERROR] Backup service is unavailable."

    try:
        items = backups.list(limit=max(1, min(int(limit or 10), 20)))
    except Exception as e:
        logger.error("[backup_list] Failed: %s", e, exc_info=True)
        return f"{header}\n[EXCEPTION] {e}"

    if not items:
        return f"{header}\nNo backups found yet (folder: {backups.directory})."

    lines = [
        f"• {item['name']} | {item['size_human']} | "
        f"{to_cleen(item['created_at']):%d.%m.%Y %H:%M}"
        for item in items
    ]

    return (
        f"{header} ({len(lines)})\n"
        + "\n".join(lines)
        + f"\n\nTotal size: {backups.human_size(backups.total_size())}, keeping last {backups.keep}. "
        + f"Restore is manual: {backups.RESTORE_HINT}"
    )


###### WORLD NEWS (rss) ######

STATUS_LABELS = {
    "ok": "ok",
    "new": "not checked yet",
    "warn": "flaky",
    "stale": "silent",
    "dead": "dead",
}


def _rss_stamp(dt) -> str:
    return f"{to_app_tz_naive(dt):%d.%m %H:%M}" if dt else "?"


async def rss_digest(hours: int = 12, fresh: bool = False, rss=None) -> str:
    """Collects a world news digest from all active feeds and stores it in the database."""
    header = "=== World News Digest ==="

    if not rss:
        logger.error("[rss_digest] News service is not available.")
        return f"{header}\n[CRITICAL ERROR] News service is unavailable."

    result = await rss.collect(hours=hours, fresh=fresh)

    if not result.get("ok"):
        reason = result.get("error")
        if reason == "busy":
            return f"{header}\n[BUSY] A digest is already being collected. Try again in a minute."
        failed = result.get("failed") or []
        tail = ""
        if failed:
            names = ", ".join(f"{r.get('title')} ({r.get('error')})" for r in failed)
            tail = f"\nFeeds that failed: {names}"
        return f"{header}\n[FAILED] {reason}{tail}"

    digest = result.get("digest") or {}
    gist = (digest.get("gist") or "").strip()
    body = (digest.get("body") or "").strip()

    lines = [
        header,
        f"• Digest id: {digest.get('id')}",
        f"• Period: {_rss_stamp(digest.get('period_from'))} — {_rss_stamp(digest.get('period_to'))} "
        f"(MSK, last {hours}h)",
        f"• Items: {digest.get('items_count')} from {digest.get('feeds_used')} feeds",
    ]
    if digest.get("feeds_failed"):
        lines.append(f"• Feeds that failed: {digest['feeds_failed']}")
    if result.get("cached"):
        lines.append("• This digest was collected earlier and is still fresh (under 2 hours).")

    if gist:
        lines.append("")
        lines.append("SHORT GIST:")
        lines.append(gist)

    lines.append("")
    lines.append("FULL DIGEST:")
    lines.append(body or "(empty)")
    lines.append("")
    lines.append(
        "Give this to the user as a news summary: keep the source attributions in brackets, "
        "keep the conclusions block separated and marked as an assessment. "
        "Do not add facts that are not in the digest."
    )
    return "\n".join(lines)


async def rss_archive(days: int = 30, query: str = None, limit: int = 20,
                      digest_id: int = 0, rss=None) -> str:
    """Reads stored news digests: short gists for a period, or one full digest by id."""
    header = "=== News Archive ==="

    if not rss:
        logger.error("[rss_archive] News service is not available.")
        return f"{header}\n[CRITICAL ERROR] News service is unavailable."

    if digest_id:
        digest = await rss.read(int(digest_id))
        if not digest:
            return f"{header}\n[NOT FOUND] No digest with id {digest_id}."
        return (
            f"{header}\n"
            f"• Digest id: {digest.get('id')}\n"
            f"• Collected: {_rss_stamp(digest.get('created_at'))}\n"
            f"• Period: {_rss_stamp(digest.get('period_from'))} — {_rss_stamp(digest.get('period_to'))}\n"
            f"• Items: {digest.get('items_count')} from {digest.get('feeds_used')} feeds\n\n"
            f"SHORT GIST:\n{(digest.get('gist') or '').strip()}\n\n"
            f"FULL DIGEST:\n{(digest.get('body') or '').strip()}"
        )

    days = max(1, min(int(days or 30), 3650))
    limit = max(1, min(int(limit or 20), 100))
    rows = await rss.archive(days=days, query=query, limit=limit)

    if not rows:
        suffix = f" matching '{query}'" if query else ""
        return f"{header}\nNo digests in the last {days} days{suffix}."

    blocks = [f"{header} (last {days} days, {len(rows)} shown)"]
    for row in rows:
        mark = f"id={row['id']} | {_rss_stamp(row.get('period_to'))} | {row.get('items_count')} items"
        blocks.append(f"[{mark}]\n{(row.get('gist') or '').strip()}")
    blocks.append("\nThese are short gists only. Call rss_archive again with digest_id to read the full digests.")
    return "\n\n".join(blocks)


async def rss_feeds(rss=None) -> str:
    """Lists the configured news feeds with their health status."""
    header = "=== News Feeds ==="

    if not rss:
        logger.error("[rss_feeds] News service is not available.")
        return f"{header}\n[CRITICAL ERROR] News service is unavailable."

    feeds = await rss.feeds()
    if not feeds:
        return (f"{header}\nNo feeds configured. The owner can replace the list in the admin panel "
                f"(/rss -> Replace list).")

    lines, dead = [], []
    for feed in feeds:
        status = rss.status_of(feed)
        if status == "dead":
            dead.append(feed.get("title") or feed.get("url"))
        note = f" — {feed['note']}" if feed.get("note") else ""
        last_item = feed.get("last_item_at")
        freshness = f"last item {_rss_stamp(last_item)}" if last_item else "no items yet"
        error = f" | error: {feed['last_error']}" if feed.get("last_error") else ""
        flag = "" if feed.get("active") else " [disabled]"
        lines.append(
            f"[id={feed['id']}] {feed.get('title') or feed['url']}{note}{flag} "
            f"| {STATUS_LABELS.get(status, status)} | {freshness} | fails={feed.get('fail_count') or 0}{error}"
        )

    text = f"{header} ({len(lines)})\n" + "\n".join(lines)
    if dead:
        text += ("\n\nDead feeds (no news for a long time): " + ", ".join(dead) +
                 ". You may suggest removing them, but only the owner decides.")
    return text


async def rss_feeds_edit(add: str = None, remove: str = None, note: str = None, rss=None) -> str:
    """Adds one news feed by URL (validated for real) or removes one by id/url/name."""
    header = "=== News Feeds Edit ==="

    if not rss:
        logger.error("[rss_feeds_edit] News service is not available.")
        return f"{header}\n[CRITICAL ERROR] News service is unavailable."

    if not add and not remove:
        return f"{header}\n[ERROR] Pass either 'add' (feed URL) or 'remove' (id, URL or feed name)."

    if add:
        report = await rss.add_one(add, note=note)
        if not report.get("ok"):
            return f"{header}\n[REJECTED] {report.get('error')} (url: {report.get('url')})"
        extra = f"\n• {report['note']}" if report.get("note") else ""
        return (f"{header}\n[ADDED] {report.get('title')} — {report.get('url')}{extra}\n"
                f"• Items in feed: {report.get('items')}\n"
                f"• Newest item: {_rss_stamp(report.get('newest'))}")

    deleted = await rss.remove_one(remove)
    if not deleted:
        return f"{header}\n[NOT FOUND] No feed matching '{remove}'."
    return f"{header}\n[REMOVED] {deleted} feed(s) removed. Remember: the owner can restore the whole list in the admin panel."


###### CHANGE LLM MODEL ######

async def change_model(name_model: str = "default", llm=None, **kwargs) -> str:
    """Инструмент для смены уровня активной LLM модели."""
    if not llm:
        logger.error("[change_model] Сервис LLM не передан в инструмент.")
        return "Error: LLM service is not available."

    # Очищаем значение на случай лишних пробелов или регистра
    clean_level = str(name_model).strip().lower()

    # Вызываем метод переключения в экземпляре LLM
    success = llm.change_model(name_model=clean_level)

    if success:
        current_active = getattr(llm, "current_model", clean_level)
        logger.info(f"⚡ [Tool: change_model] Модель успешно переключена на уровень '{clean_level}' ({current_active})")
        return f"Success: LLM model switched to level '{clean_level}' (active model: {current_active})."
    else:
        logger.warning(f"⚠️ [Tool: change_model] Не удалось переключить модель на уровень '{clean_level}'")
        return f"Error: Failed to switch model to '{clean_level}'. Requested level is not configured or invalid."
    












FUNCTIONS = {

    "get_date": {
        "description": "Returns the current system date and time. Provides precise temporal context for relative date calculations, scheduling, and time-sensitive queries.",
        "function": get_date_str,
        "schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    "think": {
        "description": (
            "Private scratchpad for your own reasoning. Call it whenever and as often as you need: "
            "to plan, compare options, verify assumptions, or structure an answer. "
            "It changes nothing and returns OK. The thought is never shown to the user."
        ),
        "function": think,
        "schema": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "Private plan or self-check. Not shown to the user."
                }
            },
            "required": ["thought"]
        }
    },

    "ask_options": {
        "description": (
            "FINAL STEP ONLY. Attaches 2-6 tappable reply options to the answer you have just written. "
            "Pressing an option sends it as the user's next message. Calling this tool ends the current "
            "agent turn immediately: no more tools and no more text are produced afterwards. "
            "Use it only when this turn's work is finished, to offer concrete next steps."
        ),
        "function": ask_options,
        "schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "Short line shown under your answer, above the buttons (e.g. 'Что дальше?'). "
                        "Write it in the user's language. Optional if the answer itself already ends with a question."
                    )
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 6,
                    "description": (
                        "2-6 short replies the user might send next, max 60 characters each. "
                        "Each option must be a complete, self-contained message."
                    )
                }
            },
            "required": ["options"]
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
                "user": {
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
                "is_blocked": {"type": "boolean", "description": "Block or unblock user. Blocked users' messages are still saved to history but never appear in the inbox widget."},
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
                "username": {
                    "type": "string",
                    "description": "Telegram username: @username."
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
        "description": "Sends a Telegram message to a specific user or channel (via peer_id or username) on behalf of the Owner. MUST be called ONLY after explicit confirmation from the Owner.",
        "function": send_mess_peer,
        "schema": {
            "type": "object",
            "properties": {
                "peer_id": {
                    "type": "integer",
                    "description": "Telegram numeric user/channel ID (peer_id) of the recipient."
                },
                "username": {
                    "type": "string",
                    "description": "Telegram username (e.g., 'username' or '@username') if peer_id is unknown."
                },
                "text_mess": {
                    "type": "string",
                    "description": "The exact final message text approved by the Owner. MUST be plain text only: strictly NO emojis or formatting."
                }
            },
            "required": ["text_mess"]
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
    },

    # TASKS TOOLS SCHEMA FOR LLM

    "add_task": {
        "description": "Create a new scheduled task, alarm, reminder, or automated action for a user or system.",
        "function": add_task,
        "schema": {
            "type": "object",
            "properties": {
                "tg_id": {
                    "type": "integer",
                    "description": "Telegram ID of the target user. Omit or pass null for system tasks."
                },
                "task_type": {
                    "type": "string",
                    "enum": ["alarm", "reminder", "agent_action", "system_cron"],
                    "description": "Type of task: 'alarm' (alarm clock), 'reminder' (standard notification), 'agent_action' (autonomous action/message), or 'system_cron' (background system task)."
                },
                "title": {
                    "type": "string",
                    "description": "Short title or summary of the task (e.g., 'Boxing practice', 'Nightly DB cleanup')."
                },
                "agent_instruction": {
                    "type": "string",
                    "description": "Detailed instruction for Agent Jumis on what action to execute when triggered (e.g., 'Gently remind Alex about boxing practice at 17:00. If silent, nag every 3 minutes.')."
                },
                "scheduled_at": {
                    "type": "string",
                    "description": "Execution timestamp in ISO format WITHOUT timezone offset (e.g., '2026-09-12T17:00:00'). Use exact local wall-clock time."
                },
                "cron_expression": {
                    "type": "string",
                    "description": "Cron expression for recurring tasks (e.g., '0 2 * * *' for daily at 02:00). CRITICAL: Write hours and minutes in exact LOCAL time. DO NOT convert hours to UTC. Pass null for one-time tasks."
                }
                #,
                # "repeat_interval_minutes": {
                #     "type": "integer",
                #     "description": "Repeat interval in minutes for nag mode (how often to retry if unacknowledged)."
                # },
                # "requires_ack": {
                #     "type": "boolean",
                #     "description": "Whether explicit user acknowledgment (e.g., 'Got it', 'I am up') is required."
                # },
                # "max_nag_attempts": {
                #     "type": "integer",
                #     "description": "Maximum number of retry attempts in nag mode before giving up."
                # }
            },
            "required": ["title", "agent_instruction", "scheduled_at"]
        }
    },

    "update_task": {
        "description": "Update an existing task (modify scheduled time, status, instructions, or retry settings).",
        "function": update_task,
        "schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Unique database ID of the task."
                },
                "title": {
                    "type": "string",
                    "description": "Updated short title."
                },
                "agent_instruction": {
                    "type": "string",
                    "description": "Updated system prompt/instruction for the Agent."
                },
                "scheduled_at": {
                    "type": "string",
                    "description": "Execution timestamp in ISO format WITHOUT timezone offset (e.g., '2026-09-12T17:00:00'). Use exact local wall-clock time."
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "running", "completed", "expired", "cancelled"],
                    "description": "Updated task status."
                },
                "cron_expression": {
                    "type": "string",
                    "description": "Cron expression for recurring tasks (e.g., '0 2 * * *' for daily at 02:00). CRITICAL: Write hours and minutes in exact LOCAL time. DO NOT convert hours to UTC. Pass null for one-time tasks."
                }
                #,
                # "is_ack_received": {
                #     "type": "boolean",
                #     "description": "Flag indicating whether user acknowledgment was received."
                # },
                # "repeat_interval_minutes": {
                #     "type": "integer",
                #     "description": "Updated repeat interval in minutes."
                # }
            },
            "required": ["id"]
        }
    },

    "search_tasks": {
        "description": "Retrieve a list of tasks with optional filtering by ID and status.",
        "function": search_tasks,
        "schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Filter tasks by ID TASK."
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "running", "completed", "expired", "cancelled"],
                    "description": "Filter tasks by status (defaults to 'pending')."
                },
                "limit": {
                    "type": "integer",
                    "description": "Limit the maximum number of returned tasks (default: 20)."
                }
            },
            "required": []
        }
    },

    "del_task": {
        "description": "Delete or cancel a task by its database ID.",
        "function": del_task,
        "schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Task ID to be deleted."
                }
            },
            "required": ["id"]
        }
    },

    "change_model": {
        "description": "Switches the active LLM model power level for the current session (default, cheap, smart). Use 'smart' for complex tasks, architecture, and debugging. Use 'cheap' for simple routine operations.",
        "function": change_model,
        "schema": {
            "type": "object",
            "properties": {
                "name_model": {
                    "type": "string",
                    "enum": ["default", "cheap", "smart"],
                    "description": "Model tier: 'default' (standard), 'cheap' (inexpensive/fast), 'smart' (advanced/capable)"
                }
            },
            "required": ["name_model"]
        }
    },

    # CLEANER: HISTORY MAINTENANCE

    "cleanup_scan": {
        "description": "Dry run report of history cleanup: how many junk messages (empty, media without text, 'ok'/'hi' chatter) and exact duplicates would be deleted. Deletes nothing. Run this before cleanup_run.",
        "function": cleanup_scan,
        "schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    "cleanup_run": {
        "description": "Permanently deletes junk and duplicate messages from history and vacuums the table. Irreversible. Call ONLY on explicit user request, after showing cleanup_scan numbers.",
        "function": cleanup_run,
        "schema": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true to actually delete. Without it the tool only explains what to do."
                }
            },
            "required": ["confirm"]
        }
    },

    "digest_run": {
        "description": "Compresses the oldest history batches into digests (chat_digests) and deletes the raw messages afterwards. Old messages only (quarantine 7 days). Runs a limited number of batches.",
        "function": digest_run,
        "schema": {
            "type": "object",
            "properties": {
                "max_batches": {
                    "type": "integer",
                    "description": "How many batches to process now, 1-20 (one batch is about 50 messages of one chat). Default 3."
                }
            },
            "required": []
        }
    },

    "digest_list": {
        "description": "Lists stored history digests (compressed conversation records) for one chat or all chats.",
        "function": digest_list,
        "schema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "integer",
                    "description": "Telegram chat/user ID. Omit to list digests across all chats."
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of digests to return, default 5."
                }
            },
            "required": []
        }
    },

    # PGBACKUP: DATABASE BACKUP

    "backup_now": {
        "description": "Creates a full database backup file (pg_dump) right now and deletes the oldest copies beyond the keep limit (3). Safe: reads the database, never modifies it. Use when the user asks to back up the database.",
        "function": backup_now,
        "schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    "backup_list": {
        "description": "Lists the stored database backup files with size and date, newest first. Read-only. Use it to tell the user when the last backup was made.",
        "function": backup_list,
        "schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many copies to show, default 10."
                }
            },
            "required": []
        }
    },

    "rss_digest": {
        "description": (
            "Collects a world news digest from all configured feeds (different countries and angles), "
            "merges the same event reported by several outlets into one story with source attributions, "
            "and SAVES it to the database. Returns a short gist plus the full digest. "
            "The digest is written in Russian. Use it when the user asks for news, world news, "
            "'what's happening', a news summary or a digest. A digest younger than 2 hours is returned "
            "from cache; pass fresh=true to force a new one."
        ),
        "function": rss_digest,
        "schema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to cover, default 12, max 72."
                },
                "fresh": {
                    "type": "boolean",
                    "description": "true = collect a new digest even if a recent one exists."
                }
            },
            "required": []
        }
    },

    "rss_archive": {
        "description": (
            "Reads already stored news digests. Without digest_id it returns SHORT GISTS for the last N days "
            "(cheap, good for 'what happened this week/month/year'), optionally filtered by a keyword. "
            "With digest_id it returns that one digest in full. Read-only."
        ),
        "function": rss_archive,
        "schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days back to look, default 30."
                },
                "query": {
                    "type": "string",
                    "description": "Optional keyword filter inside gists and full texts."
                },
                "limit": {
                    "type": "integer",
                    "description": "How many digests to show, default 20, max 100."
                },
                "digest_id": {
                    "type": "integer",
                    "description": "If set, return this single digest with its full text."
                }
            },
            "required": []
        }
    },

    "rss_feeds": {
        "description": (
            "Lists the configured news feeds with their health: ok / flaky / silent (no new items for weeks) / "
            "dead (failing repeatedly), plus last item time and last error. Read-only."
        ),
        "function": rss_feeds,
        "schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    "rss_feeds_edit": {
        "description": (
            "Adds one news feed by URL — the URL is REALLY fetched and parsed before saving, so if it is not "
            "a feed or the site blocks robots you get an explicit rejection, never a false 'added'. "
            "Or removes one feed by id, exact URL or name fragment. "
            "Full list replacement is manual work in the admin panel, not here."
        ),
        "function": rss_feeds_edit,
        "schema": {
            "type": "object",
            "properties": {
                "add": {
                    "type": "string",
                    "description": "Feed URL to add (http/https). A website URL also works — its feed is discovered."
                },
                "remove": {
                    "type": "string",
                    "description": "Feed id, exact URL or a distinctive part of its name to delete."
                },
                "note": {
                    "type": "string",
                    "description": "Optional short label stored with the feed, e.g. 'RU, state-owned'."
                }
            },
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