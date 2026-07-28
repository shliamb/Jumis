#! master/handlers/__init__.py
# Центральное место управления роутерами
from handlers.get_message import router as handle_message
from handlers.admin import router as admin_menu
from handlers.start import router as start



# Порядок имеет значение! Роутеры проверяются сверху вниз
ALL_ROUTERS = [
    start,
    admin_menu,
    handle_message
]