"""Async-движок SQLite и фабрика сессий RemindMe.

Движок и фабрика создаются один раз на старте приложения (клетка ``main``) и
переиспользуются всеми обработчиками. На каждом новом DBAPI-соединении включаются
PRAGMA ``foreign_keys=ON`` (целостность внешних ключей) и ``journal_mode=WAL``
(устойчивость к перезапуску контейнера); родительская директория файла БД
(например ``data/``) создаётся при необходимости, чтобы первое подключение не
падало из-за отсутствующего каталога.
"""

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Создаёт async-движок SQLite из ``DATABASE_URL`` с PRAGMA и каталогом БД.

    Родительская директория файла БД (``data/``) создаётся при необходимости, а на
    каждом новом соединении включаются PRAGMA ``foreign_keys=ON`` и
    ``journal_mode=WAL``.

    Args:
        settings: конфигурация; читается поле ``DATABASE_URL``.

    Returns:
        ``AsyncEngine`` с настроенными PRAGMA на каждом соединении.
    """
    url = make_url(settings.DATABASE_URL)
    database = url.database
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(settings.DATABASE_URL)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001, ARG001
        """Включает foreign_keys и WAL на каждом новом DBAPI-соединении."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Возвращает фабрику async-сессий поверх движка.

    Args:
        engine: async-движок SQLAlchemy.

    Returns:
        ``async_sessionmaker`` с ``expire_on_commit=False`` — объекты остаются
        доступны после ``commit``.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


__all__ = ["create_engine", "create_session_factory"]
