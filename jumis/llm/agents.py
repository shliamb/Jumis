# jumis/lm/agent.py

""" В Агента тут уходят только важные настройки которые обязательно нужны всегда. Только основные."""

import os
from logs.set_logger import logger


# Текст промпта живёт отдельным Markdown-файлом: его правит человек, а не питон.
# Динамика (правила, стили, паспорт, карта памяти) добавляется из БД на каждый запрос.
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

FALLBACK_SYSTEM = (
    "You are Jumis, a personal secretary in Telegram. Persona: 27-year-old woman with dark humor. "
    "No fluff, no sycophancy. Answer in the owner's language. Use Markdown. "
    "Persist durable facts with tools; never invent facts; send messages to third parties only after approval."
)


def load_prompt(name: str) -> str:
    """Читает системный промпт из prompts/<name>.md.

    Файл может отсутствовать или быть пустым — тогда работаем на короткой
    заглушке, чтобы бот поднялся и не потерял способность отвечать.
    """
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            return text
        logger.error("Prompt file is empty: %s", path)
    except Exception as e:
        logger.error("Cannot read prompt file %s: %s", path, e)
    return FALLBACK_SYSTEM


AGENTS = {
    "jumis_agent": {
        "system": load_prompt("jumis_agent"),
        "tools": [
            # REASONING
            "think",

            # DATE
            "get_date",

            # CATEGORIES FACTS
            "add_category_facts",

            # FACTS
            "write_fact",
            "update_fact",
            "del_fact",
            "search_facts",

            # CATEGORIES USERS
            "add_category_users",

            # USERS
            "update_user",
            "search_users",

            # Messages
            "msg_search",
            "msg_range",

            # SENDING MESSAGES
            "send_mess_peer",
            "clear_inbox_notifs",

            # HISTORY MAINTENANCE (cleaner)
            "cleanup_scan",
            "cleanup_run",
            "digest_run",
            "digest_list",

            # DATABASE BACKUP (pgbackup)
            "backup_now",
            "backup_list",

            # WORLD NEWS (rss)
            "rss_digest",
            "rss_archive",
            "rss_feeds",
            "rss_feeds_edit",

            # TASKS
            "add_task",
            "update_task",
            "search_tasks",
            "del_task",

            # LLM models
            "change_model",

            # FINAL ANSWER
            "ask_options"
        ]
    }

}

TOOL_DESCRIPTIONS = {
    # Reasoning
    "think": "Thinking it over...",

    # System / Date & Time
    "get_date": "Checking current date and time...",

    # Facts & Knowledge Base
    "add_category_facts": "Adding new fact category...",
    "write_fact": "Saving fact to memory...",
    "update_fact": "Updating memory fact...",
    "del_fact": "Deleting fact from database...",
    "search_facts": "Searching facts database...",

    # User Categories & Management
    "add_category_users": "Adding new user category...",
    "update_user": "Updating user profile...",
    "search_users": "Searching users database...",

    # Messages & History
    "msg_search": "Searching message history...",
    "msg_range": "Fetching message log range...",

    # Communications & Notifications
    "send_mess_peer": "Sending message to user...",
    "clear_inbox_notifs": "Clearing inbox notifications...",

    # Scheduler tasks
    "add_task": "Creating new scheduled task...",
    "update_task": "Updating scheduled task...",
    "search_tasks": "Fetching scheduled tasks...",
    "del_task": "Deleting scheduled task...",

    # History maintenance
    "cleanup_scan": "Scanning history for junk...",
    "cleanup_run": "Cleaning history database...",
    "digest_run": "Compressing old history into digests...",
    "digest_list": "Reading chat digests...",

    # Database backup
    "backup_now": "Backing up the database...",
    "backup_list": "Listing database backups...",

    # World news
    "rss_digest": "Reading world news...",
    "rss_archive": "Reading saved digests...",
    "rss_feeds": "Checking news sources...",
    "rss_feeds_edit": "Updating news sources...",

    # Change model
    "change_model": "Switching the LLM model..",

    # Final answer
    "ask_options": "Preparing reply options..."
}
