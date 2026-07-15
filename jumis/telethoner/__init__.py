#! telethoner/__init__.py
from telethoner.worker_telethon import myTelethon


mytelethon = myTelethon()
__all__ = ['mytelethon']