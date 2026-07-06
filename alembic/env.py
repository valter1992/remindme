"""Alembic окружение миграций схемы RemindMe (async).

DATABASE_URL берётся из настроек приложения (:func:`get_settings`): значение
``sqlalchemy.url`` из ``alembic.ini`` (там оно пусто) переопределяется здесь и не
хранит путь/секрет в ini-файле. DDL применяется через синхронный адаптер внутри
``Connection.run_sync(do_run_migrations)`` на async-движке; внешние ключи включаются
``PRAGMA foreign_keys=ON`` на connection перед настройкой контекста миграций.
``target_metadata`` привязан к ``Base.metadata`` для автогенерации ревизий.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from remindme.config import get_settings
from remindme.db import Base

# Объект конфигурации Alembic (читает [alembic] из alembic.ini).
config = context.config

# Настройка Python-логирования по секциям alembic.ini (если запущено через CLI).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Метаданные моделей — цель автогенерации ревизий (src/remindme/db/models.py).
target_metadata = Base.metadata

# URL базы данных из настроек приложения; ini-значение пусто и не хранит секрет.
config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)


def run_migrations_offline() -> None:
    """Применить миграции в offline-режиме (генерация SQL без подключения к БД).

    URL и метаданные передаются в ``context.configure`` напрямую; DDL не выполняется.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Применить миграции на синхронном connection, переданном из ``run_sync``.

    Включает внешние ключи SQLite (``PRAGMA foreign_keys=ON``) на connection перед
    настройкой контекста, затем выполняет миграции в транзакции alembic.
    """
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Применить миграции в online-режиме через async-движок (sqlalchemy.ext.asyncio)."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        # DDL и PRAGMA выполняются на синхронном адаптере через run_sync.
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Применить миграции в online-режиме — точка входа alembic (один event loop)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
