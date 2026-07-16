"""Клетка main — точка входа приложения RemindMe.

Фасадный модуль клетки экспонирует собственные сущности (наполняется в Task 26):
функцию применения миграций схемы (``apply_migrations``), регистрацию меню команд
(``set_commands``) и сборку/запуск приложения (``main``). Точка входа применяет
миграции до polling, собирает инфраструктуру данных (engine/factory), регистрирует
обработчики и запускает цикл доставки worker параллельно с long polling в одном
event loop. Схема мигрируется программно in-process через ``alembic`` (каркас в
``alembic/`` + ``alembic.ini``).
"""

from .main import apply_migrations, main, set_commands

__all__ = ["apply_migrations", "main", "set_commands"]
