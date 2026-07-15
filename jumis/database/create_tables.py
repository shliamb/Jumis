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

        # USERS from the dialog
        create_table_users = '''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            tg_id BIGINT UNIQUE,                           -- Telegram ID (ожет быть NULL, пока не отправили первое сообщение)
            username VARCHAR(100) UNIQUE,                  -- Юзернейм без @ (уникальный, чтобы не плодить дубли заготовок)
            full_name VARCHAR(250),                        -- Имя + Фамилия из профиля Telegram
            phone VARCHAR(50),                             -- Телефон (если поделится контактом)
            
            category VARCHAR(100),                         -- Категория (например: client, friend, spam)
            comment TEXT,                                  -- Твоя личная ручная заметка о человеке
            summary TEXT,                                  -- Краткая выжимка диалога от ИИ для быстрого контекста
            
            is_blocked BOOLEAN DEFAULT FALSE,              -- Игнорировать любые сообщения от него
            is_whitelisted BOOLEAN DEFAULT FALSE,          -- Белый список (например, отвечать ему в приоритете)
            is_bot BOOLEAN DEFAULT FALSE,                  -- Флаг бота (для фильтрации авто-спамеров)
            lang_code VARCHAR(10),                         -- Язык интерфейса (ru, en, uz...)
            
            created_at TIMESTAMP DEFAULT NOW(),            -- Дата первого контакта
            updated_at TIMESTAMP DEFAULT NOW()             -- Дата любого изменения профиля
        );
        '''

        cursor.execute(create_table_users)



        # MESSAGES from the dialog # если не прокатит, то sudo apt install postgresql-14-pgvector  # замени 14 на твою версию
        create_table_messages = '''
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,                                        -- Привязка к нашему юзеру из БД
            tg_msg_id BIGINT,                                                                                   -- ID сообщения в самом Telegram (нужно для reply/редактирования)
            
            -- Роль отправителя (идеально ложится на формат API большинства LLM: user, assistant, system)
            role VARCHAR(50) NOT NULL,                                   
            
            -- Текстовое содержимое (или расшифрованный текст для голосовых)
            content TEXT,                                                
            
            -- Для работы с медиа (голос, фото, файлы)
            msg_type VARCHAR(50) DEFAULT 'text',                                                                -- text, voice, photo, document, video
            media_file_id VARCHAR(500),                                                                         -- file_id в Telegram (чтобы переслать или скачать заново)
            media_local_path VARCHAR(1000),                                                                     -- Путь к файлу на твоем сервере (например, к скачанному .wav)
            
            -- Поля под будущий RAG (векторный поиск)
            embedding VECTOR(1536),                                                                             -- Вектор сообщения (1536 — стандарт для OpenAI, у других моделей может быть 1024 или 768)
            is_embedded BOOLEAN DEFAULT FALSE,                                                                  -- Флаг: создали ли мы уже вектор для этого сообщения
            
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_messages_user_id_created ON messages(user_id, created_at DESC);          -- Индекс для быстрой выборки истории конкретного пользователя по времени

        '''

        cursor.execute(create_table_messages)





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










