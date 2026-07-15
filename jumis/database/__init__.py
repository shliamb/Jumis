#! database/__init__.py
from database.common import Database

db = Database()

__all__ = ['db']