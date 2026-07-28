from config import USER_DB, PORT, PASSWORD_DB, DB_NAME, HOST
import psycopg2
from logs.set_logger import set_logger
logger = set_logger(name="db")




# Create TABLES:
def create_tables_in_db():
    connection, cursor = False, False

    try:
        # Connect to db:
        connection = psycopg2.connect(host=HOST, port=PORT, database=DB_NAME, user=USER_DB, password=PASSWORD_DB)

        cursor = connection.cursor()


        create_pgvector = '''
        -- 1. Включаем расширение pgvector (выполнить 1 раз)
        CREATE EXTENSION IF NOT EXISTS vector;
        '''
        cursor.execute(create_pgvector)


        create_table_users = '''
        -- 2. Таблица пользователей
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            tg_id BIGINT UNIQUE,                           -- Telegram ID (может быть NULL, пока не отправили первое сообщение)
            username VARCHAR(100) UNIQUE,                  -- Юзернейм без @ (уникальный, чтобы не плодить дубли заготовок)
            full_name VARCHAR(250),                        -- Имя + Фамилия из профиля Telegram
            phone VARCHAR(50),                             -- Телефон (если поделится контактом)
            
            category VARCHAR(100),                         -- Категория (например: client, friend, spam)
            comment TEXT,                                  -- Твоя личная ручная заметка о человеке
            summary TEXT,                                  -- Краткая выжимка диалога от ИИ для быстрого контекста
            
            is_admin BOOLEAN DEFAULT FALSE,                
            is_blocked BOOLEAN DEFAULT FALSE,              -- Игнорировать любые сообщения от него
            is_whitelisted BOOLEAN DEFAULT FALSE,          -- Белый список (например, отвечать ему в приоритете)
            is_bot BOOLEAN DEFAULT FALSE,                  -- Флаг бота (для фильтрации авто-спамеров)
            lang_code VARCHAR(10),                         -- Язык интерфейса (ru, en, uz...)
            
            model_default VARCHAR(100),                    -- Модель по умолчанию, меняется через агента, например - deepseek/deepseek-v4-flash
            model_cheap VARCHAR(100),
            model_smart VARCHAR(100),

            
            created_at TIMESTAMP DEFAULT NOW(),            -- Дата первого контакта
            updated_at TIMESTAMP DEFAULT NOW()             -- Дата любого изменения профиля
        );
        '''
        cursor.execute(create_table_users)


        create_table_messages = '''
        -- 3. Таблица сырых сообщений (История диалогов)
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tg_msg_id BIGINT,
            
            role VARCHAR(50) NOT NULL,                     -- user, assistant, system
            content TEXT,                                  -- Текст сообщения или расшифровка
            
            msg_type VARCHAR(50) DEFAULT 'text',           -- text, voice, photo, document
            media_file_id VARCHAR(500),
            media_local_path VARCHAR(1000),
            
            embedding VECTOR(768),                         -- Исправлено на 768 под нашу модель!
            is_embedded BOOLEAN DEFAULT FALSE,
            
            created_at TIMESTAMP DEFAULT NOW()
        );

        -- Быстрые индексы для messages
        CREATE INDEX IF NOT EXISTS idx_messages_user_id_created ON messages(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_messages_embedding ON messages USING hnsw (embedding vector_cosine_ops);

        '''
        cursor.execute(create_table_messages)


        create_table_memories = '''
        -- 4. Таблица Долговременной Памяти / Фактов (RAG)
        CREATE TABLE IF NOT EXISTS memories (
            id SERIAL PRIMARY KEY,
            
            -- Привязка: Если user_id указан — это факт о КОНКРЕТНОМ клиенте.
            -- Если user_id IS NULL — это ГЛОБАЛЬНАЯ память Jumis (твои личные инструкции, правила, контекст)
            user_id INT REFERENCES users(id) ON DELETE CASCADE,
            
            category VARCHAR(50) DEFAULT 'fact',           -- fact, preference, hardware, agreement
            content TEXT NOT NULL,                         -- Сухой факт ("Залит ноутбук K53, согласован ремонт до 5000р")
            
            embedding VECTOR(768),                         -- Вектор факта для локального поиска
            
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );

        -- Индексы для таблицы памяти
        CREATE INDEX IF NOT EXISTS idx_memories_user_id ON memories(user_id);
        CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops);
        '''
        cursor.execute(create_table_memories)


        create_table_llm_logs = '''
        -- Таблица учета расходов и использования LLM
        CREATE TABLE IF NOT EXISTS llm_logs (
            id SERIAL PRIMARY KEY,
            
            -- Юзер, запустивший запрос (если запрос был в рамках диалога с клиентом, иначе NULL)
            user_id INT REFERENCES users(id) ON DELETE SET NULL, 
            
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
            
            created_at TIMESTAMP DEFAULT NOW()
        );

        -- Индексы для быстрой аналитики и построения отчетов
        CREATE INDEX IF NOT EXISTS idx_llm_logs_created_at ON llm_logs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_llm_logs_model ON llm_logs(model);
        CREATE INDEX IF NOT EXISTS idx_llm_logs_user_id ON llm_logs(user_id);
        '''
        cursor.execute(create_table_llm_logs)


        create_table_scheduled_tasks = '''
        -- Таблица отложенных задач, будильников и системных кронов
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id SERIAL PRIMARY KEY,
            
            -- Кому принадлежит задача:
            -- user_id IS NULL  -> Системная задача (например, ночная уборка БД в 02:00)
            -- user_id = ID     -> Задача для конкретного человека (Алекса или клиента)
            user_id INT REFERENCES users(id) ON DELETE CASCADE,
            
            -- Тип задачи: 'alarm' (будильник), 'reminder' (напоминалка), 'agent_action' (авто-ответ/сообщение), 'system_cron' (ночная уборка)
            task_type VARCHAR(50) NOT NULL DEFAULT 'reminder',
            
            -- Короткая суть задачи для тебя ("Тренировка в 17:00", "Будильник 12:00")
            title VARCHAR(250) NOT NULL,
            
            -- Инструкция для Агента (System Prompt для Jumis):
            -- Например: "Мягко напомни Алексу про тренировку. Если молчит — пиши снова через 3 минуты."
            -- Или для ночной уборки: "Удали одноразовые просроченные таски и сожги дубли в memories."
            agent_instruction TEXT NOT NULL,
            
            -- Время следующего/первого запуска
            scheduled_at TIMESTAMP WITH TIME ZONE NOT NULL,
            
            -- Повторения и Крон:
            -- cron_expression: '0 2 * * *' (каждую ночь в 2:00) или NULL для разовых задач
            cron_expression VARCHAR(100),
            
            -- Режим «Дятел» (Назойливые напоминания):
            repeat_interval_minutes INT DEFAULT 0,              -- Через сколько минут повторить (например, каждые 3 мин)
            requires_ack BOOLEAN DEFAULT FALSE,                 -- Требуется ли подтверждение от тебя ("Да, встал", "Ок")
            is_ack_received BOOLEAN DEFAULT FALSE,              -- Флаг: подтвердил ли ты получение
            max_nag_attempts INT DEFAULT 5,                     -- Сколько раз максимум "долбить" перед сдачей
            current_nag_count INT DEFAULT 0,                    -- Сколько раз уже отправил
            
            -- Статус задачи: 'pending' (ждет времени), 'running' (в процессе), 'completed' (завершена), 'cancelled' (отменена)
            status VARCHAR(50) DEFAULT 'pending',
            
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );

        -- Индекс для фонового демона: ищет только задачи, время которых наступило!
        CREATE INDEX IF NOT EXISTS idx_tasks_execution ON scheduled_tasks (status, scheduled_at) 
        WHERE status = 'pending';
        '''
        cursor.execute(create_table_scheduled_tasks)





        # Saving changes:
        connection.commit()
        logger.info("Adding tables is done!")
        print("Adding tables is done!")
        return True

    except Exception as error:
        logger.error(f"Error Create Tables in DB: {error}")
        print("Error Create Tables in DB:", error)
        return False

    finally:

        # Closing the cursor and database connection
        if cursor:
            cursor.close()

        if connection:
            connection.close()

# create_tables_in_db()










