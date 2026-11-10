#! master/handlers/__init__.py
# Центральное место управления роутерами
from handlers.get_message import router as handle_message
from handlers.admin import router as admin_menu
from handlers.start import router as start
from handlers.options import router as options_menu
from handlers.inbox import router as inbox_menu
from handlers.cleaner import router as cleaner_menu
from handlers.backup import router as backup_menu
from handlers.rss_menu import router as rss_menu



# Порядок имеет значение! Роутеры проверяются сверху вниз
ALL_ROUTERS = [
    start,
    admin_menu,
    options_menu,
    inbox_menu,
    cleaner_menu,
    backup_menu,
    rss_menu,
    handle_message
]