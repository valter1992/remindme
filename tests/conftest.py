"""Общие фикстуры RemindMe.

Содержит базовые shared-фикстуры, доступные всем тестам:
- ``fixed_now`` — детерминированный aware-UTC момент времени (явный ``now``).
- ``bot`` — mock aiogram ``Bot`` для тестов handlers и worker.
- ``session`` — чистая async-сессия SQLite на ``tmp_path`` (создаёт схему из
  ``Base.metadata``); используется logic-тестами моделей и всеми последующими
  ``db``/``services``/``bot`` тестами.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from remindme.db import Base


@pytest.fixture
def fixed_now() -> datetime:
    """Возвращает детерминированный aware-UTC момент (12:00 UTC 2026-06-24).

    Returns:
        Зафиксированный aware ``datetime`` в UTC — используется как
        явный ``now`` в парсерах, сценариях и репозиториях.
    """
    return datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def bot() -> AsyncMock:
    """Возвращает mock aiogram ``Bot`` для тестов handlers и worker.

    Returns:
        ``AsyncMock``, имитирующий интерфейс aiogram ``Bot`` (``send_message``,
        ``edit_message_text`` и пр.).
    """
    return AsyncMock()


@pytest_asyncio.fixture
async def session(tmp_path) -> AsyncIterator[AsyncSession]:
    """Возвращает чистую async-сессию SQLite на ``tmp_path``.

    Создаёт временную БД ``sqlite+aiosqlite`` в ``tmp_path``, включает PRAGMA
    ``foreign_keys=ON`` на каждом соединении, создаёт схему из ``Base.metadata``
    перед yield'ом сессии и дропает её после теста. ``expire_on_commit=False``,
    чтобы объекты оставались доступны после commit.

    Args:
        tmp_path: pytest-фикстура с уникальной временной директорией.

    Yields:
        Чистую ``AsyncSession`` с настроенной схемой.
    """
    db_file = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as async_session:
        yield async_session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
