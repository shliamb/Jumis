#! master/handlers/__init__.py
# Центральное место управления роутерами
from handlers.get_message import router as handle_message
from handlers.admin import router as admin_menu



# Порядок имеет значение! Роутеры проверяются сверху вниз
ALL_ROUTERS = [
    admin_menu,
    handle_message
]