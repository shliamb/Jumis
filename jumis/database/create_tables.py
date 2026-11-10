from config import USER_DB, PORT, PASSWORD_DB, DB_NAME, HOST
import psycopg2
from logs.set_logger import logger


# =====================================================================
# ЕДИНОЕ МЕСТО СХЕМЫ БД
#
# Здесь объявлены все таблицы и индексы. Два входа используют одни и те же
# операторы: `create_tables_in_db()` (psycopg2, команда `/crTabDb`) и
# `ensure_schema(db)` (asyncpg, старт бота). Модули не объявляют таблиц сами.
# =====================================================================

# Векторный HNSW-индекс по истории: без него `msg_search` перечитывает и
# сортирует все векторы (12k × 3 КБ), с ним — идёт по графу ближайших соседей.
SQL_MESSAGES_EMBEDDING_INDEX = """
CREATE INDEX IF NOT EXISTS idx_messages_embedding
    ON messages USING hnsw (embedding vector_cosine_ops);
"""

# Дайджесты старой истории (модуль cleaner). Рабочие таблицы не меняются —
# это единственная НОВАЯ таблица, и она объявлена здесь, вместе с остальными.
SQL_CHAT_DIGESTS = (
    """
    CREATE TABLE IF NOT EXISTS chat_digests (
        id BIGSERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        period_from TIMESTAMPTZ NOT NULL,
        period_to TIMESTAMPTZ NOT NULL,
        msg_count INT NOT NULL DEFAULT 0,
        summary TEXT NOT NULL,
        facts TEXT,
        model VARCHAR(100),
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_digests_chat
        ON chat_digests (chat_id, period_to DESC)
    """,
)

# Мировые новости (модуль rss).
#   rss_feeds   — откуда берём: список фидов и их здоровье;
#   rss_digests — что накопили: сводка (body) и короткая выжимка (gist).
# Сводки пишутся только добавлением, поэтому индекс по времени DESC.
SQL_RSS = (
    """
    CREATE TABLE IF NOT EXISTS rss_feeds (
        id SERIAL PRIMARY KEY,
        url TEXT NOT NULL UNIQUE,
        title VARCHAR(200),
        note VARCHAR(200),
        active BOOLEAN NOT NULL DEFAULT TRUE,
        last_ok_at TIMESTAMPTZ,
        last_item_at TIMESTAMPTZ,
        last_error TEXT,
        fail_count INT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rss_feeds_active ON rss_feeds (active)
    """,
    """
    CREATE TABLE IF NOT EXISTS rss_digests (
        id SERIAL PRIMARY KEY,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        period_from TIMESTAMPTZ NOT NULL,
        period_to TIMESTAMPTZ NOT NULL,
        gist TEXT NOT NULL,
        body TEXT NOT NULL,
        items_count INT NOT NULL DEFAULT 0,
        feeds_used INT NOT NULL DEFAULT 0,
        feeds_failed TEXT,
        model VARCHAR(100)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rss_digests_created ON rss_digests (created_at DESC)
    """,
)




# Память в промпте (модуль memory).
#   facts_categories.in_prompt  — раздел, который рендерится в system-промпт;
#   memories.memory_status      — 'live' участвует в поиске и рендере,
#                                 'superseded'/'archived' остаётся в базе, но
#                                 не мешает (duбли не удаляем — данные не теряем);
#   memories.superseded_by      — на какой факт заменён (след, а не удаление).
SQL_MEMORY_PROMPT = (
    "ALTER TABLE facts_categories ADD COLUMN IF NOT EXISTS in_prompt BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE facts_categories ADD COLUMN IF NOT EXISTS prompt_order INT NOT NULL DEFAULT 0",
    "ALTER TABLE facts_categories ADD COLUMN IF NOT EXISTS prompt_title VARCHAR(80)",
    "ALTER TABLE memories ADD COLUMN IF NOT EXISTS memory_status VARCHAR(20) NOT NULL DEFAULT 'live'",
    "ALTER TABLE memories ADD COLUMN IF NOT EXISTS superseded_by BIGINT",
    """
    CREATE INDEX IF NOT EXISTS idx_memories_live
        ON memories (category, id) WHERE memory_status = 'live'
    """,
    """
    INSERT INTO facts_categories (name, description, in_prompt, prompt_order, prompt_title) VALUES
        ('rule', 'Behavior rules that always apply: prohibitions, order of actions, memory hygiene, clients.', TRUE, 10, 'RULES'),
        ('style_owner', 'How to answer the owner: length, tone, what to omit.', TRUE, 20, 'STYLE: OWNER'),
        ('style_client', 'How to write to clients and new contacts: address form, greeting, length.', TRUE, 30, 'STYLE: CLIENTS'),
        ('style_personal', 'How to write to family and close friends: address form, tone, shorthand.', TRUE, 40, 'STYLE: CLOSE PEOPLE'),
        ('profile', 'Owner passport: address, schedule, prices, family, key people, debts.', TRUE, 50, 'OWNER — PASSPORT')
    ON CONFLICT (name) DO NOTHING
    """,
)


# Create TABLES:
def create_tables_in_db():
    connection, cursor = False, False

    try:
        # Connect to db:
        connection = psycopg2.connect(host=HOST, port=PORT, database=DB_NAME, user=USER_DB, password=PASSWORD_DB)

        cursor = connection.cursor()


        create_pgvector = '''
        -- Включаем расширение pgvector (выполнить 1 раз)
        CREATE EXTENSION IF NOT EXISTS vector;
        '''
        cursor.execute(create_pgvector)


        create_table_users_categories = '''
        CREATE TABLE IF NOT EXISTS users_categories (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50) UNIQUE NOT NULL,    -- not_defined, client, friend, spam, family, lead, partner
            description TEXT,                    -- описание для понимания LLM
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        -- Базовый набор категорий пользователей с понятным описанием для LLM
        INSERT INTO users_categories (name, description) VALUES
            ('not_defined', 'Новый или неидентифицированный контакт, статус не определен'),
            ('client', 'Клиент по ремонту, покупке запчастей или согласованию работ'),
            ('friend', 'Друг или хорошая личный знакомый'),
            ('family', 'Член семьи или близкий родственник'),
            ('partner', 'Коллега, поставщик запчастей или бизнес-партнер'),
            ('spam', 'Заблокированный спамер, бот или нежелательный контакт')
        ON CONFLICT (name) DO NOTHING;
        '''
        cursor.execute(create_table_users_categories)


        create_table_users = '''
        -- Таблица пользователей
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            tg_id BIGINT UNIQUE,                           -- Telegram ID (может быть NULL, пока не отправили первое сообщение)
            username VARCHAR(100) UNIQUE,                  -- Юзернейм без @ (уникальный, чтобы не плодить дубли заготовок)
            full_name VARCHAR(250),                        -- Имя + Фамилия из профиля Telegram
            phone VARCHAR(50),                             -- Телефон (если поделится контактом)
            
            category VARCHAR(50) DEFAULT 'not_defined'
              REFERENCES users_categories(name) ON UPDATE CASCADE ON DELETE RESTRICT,
            
            comment TEXT,                                  -- Твоя личная ручная заметка о человеке
            summary TEXT,                                  -- Краткая выжимка диалога от ИИ для быстрого контекста
            
            aliases TEXT,                                  -- Приметы/алиасы: "Петя, петух, младший брат, братик.."
            aliases_vector vector(768),                   -- Вектор для поиска (размерность модель эмбеддингов 768)

            is_admin BOOLEAN DEFAULT FALSE,                
            is_blocked BOOLEAN DEFAULT FALSE,              -- Игнорировать любые сообщения от него
            is_whitelisted BOOLEAN DEFAULT FALSE,          -- Белый список (например, отвечать ему в приоритете)
            is_bot BOOLEAN DEFAULT FALSE,                  -- Флаг бота (для фильтрации авто-спамеров)
            lang_code VARCHAR(10),                         -- Язык интерфейса (ru, en, uz...)
            
            model_default VARCHAR(100),                    -- Модель по умолчанию, меняется через агента, например - deepseek/deepseek-v4-flash
            model_cheap VARCHAR(100),
            model_smart VARCHAR(100),
            -- model_super_smart VARCHAR(100), -- излишне

            
            created_at TIMESTAMPTZ DEFAULT NOW(),            -- Дата первого контакта
            updated_at TIMESTAMPTZ DEFAULT NOW()             -- Дата любого изменения профиля
        );

        -- Индексы
        CREATE INDEX IF NOT EXISTS idx_users_category ON users(category);

        -- Векторный индекс HNSW для мгновенного косинусного поиска по алиасам
        CREATE INDEX IF NOT EXISTS idx_users_aliases_vector 
        ON users USING hnsw (aliases_vector vector_cosine_ops);
        '''
        cursor.execute(create_table_users)


        create_table_messages = '''
        -- Таблица сырых сообщений (История диалогов)
        CREATE TABLE IF NOT EXISTS messages (
            id BIGSERIAL PRIMARY KEY,
            
            -- Кто и Кому (Участники)
            chat_id BIGINT NOT NULL,                        -- ID чата (для лички = ID собеседника)
            sender_id BIGINT NOT NULL,                      -- Telegram ID ОТПРАВИТЕЛЯ
            recipient_id BIGINT NOT NULL,                   -- Telegram ID ПОЛУЧАТЕЛЯ
            
            -- Идентификация сообщения в TG
            tg_msg_id BIGINT NOT NULL,                      -- ID сообщения внутри Telegram
            
            -- Направление и Контент
            direction VARCHAR(20) NOT NULL,                 -- 'inbound_peer' (входящее) или 'outbound_owner' (исходящее)
            content TEXT,                                   -- Текст сообщения / транскрибация
            
            -- Тип и медиа
            msg_type VARCHAR(50) DEFAULT 'text',            -- text, photo, voice, video, document
            media_file_id VARCHAR(500),
            media_local_path VARCHAR(1000),
            media_duration INT,                             -- Длительность для аудио/видео
            
            -- Векторы и системные поля
            embedding VECTOR(768),
            is_embedded BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW(),

            -- Защита от дублей
            CONSTRAINT unique_msg_per_chat UNIQUE (chat_id, tg_msg_id),
            -- Проверка валидности направления
            CONSTRAINT check_direction CHECK (direction IN ('inbound_peer', 'outbound_owner'))
        );

        -- Индексы для реактивной выборки истории
        CREATE INDEX IF NOT EXISTS idx_messages_chat_history ON messages (chat_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages (sender_id);

        '''
        cursor.execute(create_table_messages)
        cursor.execute(SQL_MESSAGES_EMBEDDING_INDEX)


        create_table_facts_categories = '''
        CREATE TABLE IF NOT EXISTS facts_categories (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50) UNIQUE NOT NULL,    -- fact, preference, hardware, agreement
            description TEXT,                    -- описание для понимания LLM
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        -- Наполняем дефолтными категориями, если их еще нет (ON CONFLICT DO NOTHING)
        INSERT INTO facts_categories (name, description) VALUES
            ('fact', 'Общие факты о пользователе или системе'),
            ('preference', 'Предпочтения, привычки и пожелания клиента'),
            ('hardware', 'Железо, спецификации, ремонты, запчасти'),
            ('agreement', 'Договоренности, цены, сроки и статусы заказов')
        ON CONFLICT (name) DO NOTHING;
        '''
        cursor.execute(create_table_facts_categories)


        create_table_memories = '''
        CREATE TABLE IF NOT EXISTS memories (
            id BIGSERIAL PRIMARY KEY,
            tg_id BIGINT,                                                    -- Telegram ID пользователя
            
            -- Внешняя связь сразу на facts_categories(name)
            category VARCHAR(50) DEFAULT 'fact'
              REFERENCES facts_categories(name) ON UPDATE CASCADE ON DELETE RESTRICT,
            
            content TEXT NOT NULL,
            embedding VECTOR(768),
            
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        -- Индексы
        CREATE INDEX IF NOT EXISTS idx_memories_tg_id ON memories(tg_id);
        CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
        CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops);
        '''
        cursor.execute(create_table_memories)


        create_table_llm_logs = '''
        -- Таблица учета расходов и использования LLM (Жесткая привязка к владельцу/пользователю бота)
        CREATE TABLE IF NOT EXISTS llm_logs (
            id BIGSERIAL PRIMARY KEY,
            
            -- Юзер, запустивший запрос (если запрос был в рамках диалога с клиентом, иначе NULL)
            tg_id BIGINT REFERENCES users(tg_id) ON DELETE SET NULL, 
            
            -- Использованная модель (например: gemini/gemini-2.5-flash, deepseek/deepseek-chat)
            model VARCHAR(100) NOT NULL,                         
            
            -- Метрики токенов
            prompt_tokens INT DEFAULT 0,                         -- Входные токены
            completion_tokens INT DEFAULT 0,                     -- Генерация (выходные)
            cached_tokens INT DEFAULT 0,                         -- Токены, взятые из кэша (Gemini / DeepSeek)
            total_tokens INT DEFAULT 0,                          -- Сумма токенов
            
            -- Финансы и задержки
            cost_usd NUMERIC(10, 6) DEFAULT 0.0,                 -- Точная стоимость запроса в $ (от LiteLLM)
            execution_time_ms INT DEFAULT 0,                     -- Время выполнения запроса в миллисекундах
            
            -- Категория (chat, summary, tool_call, embedding)
            call_type VARCHAR(50) DEFAULT 'chat',                
            
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        -- Индексы для быстрой аналитики и построения отчетов
        CREATE INDEX IF NOT EXISTS idx_llm_logs_created_at ON llm_logs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_llm_logs_model ON llm_logs(model);
        CREATE INDEX IF NOT EXISTS idx_llm_logs_tg_id ON llm_logs(tg_id);
        '''
        cursor.execute(create_table_llm_logs)


        create_table_scheduled_tasks = '''
        -- Таблица отложенных задач, будильников и системных кронов
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id SERIAL PRIMARY KEY,
            
            -- Кому принадлежит задача:
            -- tg_id IS NULL  -> Системная задача (например, ночная уборка БД в 02:00)
            -- tg_id = tg_id    -> Задача для конкретного человека (Алекса или клиента)
            tg_id BIGINT REFERENCES users(tg_id) ON DELETE CASCADE,
            
            -- Тип задачи: 'alarm' (будильник), 'reminder' (напоминалка), 'agent_action' (авто-ответ/сообщение), 'system_cron' (ночная уборка)
            task_type VARCHAR(50) NOT NULL DEFAULT 'reminder',
            
            -- Короткая суть задачи ("Тренировка в 17:00", "Будильник 12:00")
            title VARCHAR(250) NOT NULL,
            
            -- Инструкция для Агента (System Prompt для Jumis):
            -- Например: "Мягко напомни Алексу про тренировку. Если молчит — пиши снова через 3 минуты."
            -- Или для ночной уборки: "Удали одноразовые просроченные таски и сожги дубли в memories."
            agent_instruction TEXT NOT NULL,
            
            -- Время следующего/первого запуска
            scheduled_at TIMESTAMP NOT NULL,
            
            -- Повторения и Крон:
            -- cron_expression: '0 2 * * *' (каждую ночь в 2:00) или NULL для разовых задач
            cron_expression VARCHAR(100),
            
            -- Режим «Дятел» (Назойливые напоминания):
            repeat_interval_minutes INT DEFAULT 0,              -- Через сколько минут повторить (например, каждые 3 мин)
            requires_ack BOOLEAN DEFAULT FALSE,                 -- Требуется ли подтверждение от тебя ("Да, встал", "Ок")
            is_ack_received BOOLEAN DEFAULT FALSE,              -- Флаг: подтвердил ли ты получение
            max_nag_attempts INT DEFAULT 5,                     -- Сколько раз максимум "долбить" перед сдачей
            current_nag_count INT DEFAULT 0,                    -- Сколько раз уже отправил
            
            -- Статус задачи: 'pending' (ждет времени), 'running' (в процессе), 'completed' (завершена), 'cancelled' (отменена), 'expired' (истекла)
            status VARCHAR(50) DEFAULT 'pending',
            
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        -- Индекс для фонового демона: ищет только задачи, время которых наступило!
        CREATE INDEX IF NOT EXISTS idx_tasks_execution ON scheduled_tasks (status, scheduled_at) 
        WHERE status = 'pending';
        '''
        cursor.execute(create_table_scheduled_tasks)

        # Новая таблица модуля чистки истории (объявлена в начале файла)
        for statement in SQL_CHAT_DIGESTS:
            cursor.execute(statement)

        # Таблицы модуля мировых новостей: фиды и накопленные сводки
        for statement in SQL_RSS:
            cursor.execute(statement)

        # Память в промпте: разделы правил/стилей/паспорта и статусы фактов
        for statement in SQL_MEMORY_PROMPT:
            cursor.execute(statement)





        # Saving changes:
        connection.commit()
        logger.info("Database tables created")
        return True

    except Exception as error:
        logger.error("Error creating DB tables: %s", error)
        return False

    finally:

        # Closing the cursor and database connection
        if cursor:
            cursor.close()

        if connection:
            connection.close()

# create_tables_in_db()


async def ensure_schema(db) -> bool:
    """
    Идемпотентно создаёт объекты, которых может не быть на живой базе:
    дайджесты истории, таблицы новостей (rss_feeds / rss_digests),
    колонки и разделы памяти в промпте, векторный индекс истории.

    Нужен на старте бота: `create_tables_in_db()` синхронный и вызывается
    только командой `/crTabDb`, а работать чистке, новостям и поиску нужно сразу.
    Рабочие таблицы не создаёт и не меняет.
    """
    ok = True

    # Новые служебные таблицы: дайджесты истории, мировые новости, память в промпте.
    for label, statements in (
        ("chat_digests", SQL_CHAT_DIGESTS),
        ("rss", SQL_RSS),
        ("memory_prompt", SQL_MEMORY_PROMPT),
    ):
        for statement in statements:
            try:
                await db.execute(statement)
            except Exception as e:
                ok = False
                logger.error("[Schema] Не удалось применить оператор для %s: %s", label, e, exc_info=True)

    # Индекс можно ставить только если таблица сообщений уже существует.
    try:
        # без префикса схемы: имена таблиц в проекте живут по search_path
        has_messages = await db.fetchval("SELECT to_regclass('messages') IS NOT NULL;")
    except Exception as e:
        logger.warning("[Schema] Не удалось проверить наличие messages: %s", e)
        has_messages = False

    if has_messages:
        try:
            await db.execute(SQL_MESSAGES_EMBEDDING_INDEX)
        except Exception as e:
            ok = False
            logger.error("[Schema] Не удалось создать idx_messages_embedding: %s", e, exc_info=True)
    else:
        logger.warning("[Schema] Таблицы messages нет — индекс истории пропущен (создайте таблицы: /crTabDb)")

    if ok:
        logger.info("[Schema] Схема служебных объектов готова: chat_digests + rss_feeds/rss_digests + память в промпте + idx_messages_embedding")
    return ok










