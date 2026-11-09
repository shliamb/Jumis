# jumis/cleaner/schema.py
"""
Схема модуля чистки.

ВАЖНО: рабочие таблицы (messages, users, scheduled_tasks) не меняются.
Модуль создаёт только одну НОВУЮ таблицу — chat_digests.
"""
from logs.set_logger import logger


# Каждый оператор отдельно: без многострочных пакетов, чтобы работало
# и через пул, и через одиночное соединение.
SCHEMA_STATEMENTS = (
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


async def ensure_schema(db) -> bool:
    """Идемпотентно создаёт таблицу дайджестов. Рабочие таблицы не трогает."""
    try:
        for statement in SCHEMA_STATEMENTS:
            await db.execute(statement)
        logger.info("[Cleaner] Schema ready: chat_digests")
        return True
    except Exception as e:
        logger.error("[Cleaner] Не удалось создать chat_digests: %s", e, exc_info=True)
        return False
