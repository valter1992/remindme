"""Тесты клетки db: движок и фабрика сессий (``session.py``).

Контракт-тесты проверяют форму фасада и сигнатуры
(``create_engine(settings) -> AsyncEngine``,
``create_session_factory(engine) -> factory``); logic-тесты — создание каталога
БД, PRAGMA на соединении (``foreign_keys=ON``, ``journal_mode=WAL``) и
``expire_on_commit=False`` у фабрики. Все БД располагаются в ``tmp_path``.
"""

import inspect

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from remindme.config import Settings
from remindme.db import Base, User, create_engine, create_session_factory

_NOW = "2026-06-24T12:00:00+00:00"


def _settings(database_url: str) -> Settings:
    """Строит ``Settings`` с заданным ``DATABASE_URL`` (токен-заглушка)."""
    return Settings(TELEGRAM_BOT_TOKEN="dummy-token", DATABASE_URL=database_url)


def _url(db_path) -> str:  # noqa: ANN001
    """Формирует ``sqlite+aiosqlite`` URL для абсолютного пути файла БД."""
    return f"sqlite+aiosqlite:///{db_path}"


# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_session_helpers() -> None:
    """Фасад экспортирует ``create_engine`` и ``create_session_factory``."""
    assert callable(create_engine)
    assert callable(create_session_factory)


def test_create_engine_signature() -> None:
    """``create_engine(settings: Settings) -> AsyncEngine`` по контракту."""
    sig = inspect.signature(create_engine)
    assert list(sig.parameters) == ["settings"]
    assert sig.parameters["settings"].annotation is Settings
    assert sig.return_annotation is AsyncEngine


def test_create_session_factory_signature() -> None:
    """``create_session_factory(engine) -> factory`` по контракту."""
    sig = inspect.signature(create_session_factory)
    assert list(sig.parameters) == ["engine"]
    assert sig.return_annotation is not inspect.Parameter.empty


# --- Logic-тесты ---


async def test_create_engine_creates_data_dir(tmp_path) -> None:  # noqa: ANN001
    """Родительский каталог файла БД создаётся при создании движка."""
    data_dir = tmp_path / "data"
    db_url = _url(data_dir / "remindme.db")

    assert not data_dir.exists()

    engine = create_engine(_settings(db_url))
    try:
        assert data_dir.exists()
        assert data_dir.is_dir()
    finally:
        await engine.dispose()


async def test_create_engine_returns_async_engine(tmp_path) -> None:  # noqa: ANN001
    """``create_engine`` возвращает ``AsyncEngine``."""
    engine = create_engine(_settings(_url(tmp_path / "test.db")))
    try:
        assert isinstance(engine, AsyncEngine)
    finally:
        await engine.dispose()


async def test_pragma_foreign_keys_on(tmp_path) -> None:  # noqa: ANN001
    """На соединении активна PRAGMA ``foreign_keys=ON`` (значение 1)."""
    engine = create_engine(_settings(_url(tmp_path / "test.db")))
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA foreign_keys"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()


async def test_pragma_wal_mode(tmp_path) -> None:  # noqa: ANN001
    """На соединении установлен режим журнала ``WAL``."""
    engine = create_engine(_settings(_url(tmp_path / "test.db")))
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            assert result.scalar() == "wal"
    finally:
        await engine.dispose()


async def test_factory_expire_on_commit_false(tmp_path) -> None:  # noqa: ANN001
    """Объекты доступны после commit без async-рефреша (``expire_on_commit=False``)."""
    engine = create_engine(_settings(_url(tmp_path / "test.db")))
    factory = create_session_factory(engine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with factory() as session:
            assert isinstance(session, AsyncSession)
            user = User(
                telegram_user_id=42,
                timezone="Europe/Moscow",
                created_at_utc=_NOW,
                updated_at_utc=_NOW,
            )
            session.add(user)
            await session.commit()

            # При expire_on_commit=False атрибуты не помечаются истёкшими и
            # доступны синхронно (без async lazy-load, который невозможен в async).
            assert user.telegram_user_id == 42
            assert user.timezone == "Europe/Moscow"
    finally:
        await engine.dispose()


async def test_factory_produces_sessions_on_engine(tmp_path) -> None:  # noqa: ANN001
    """Фабрика возвращает ``async_sessionmaker``, дающий ``AsyncSession``."""
    engine = create_engine(_settings(_url(tmp_path / "test.db")))
    factory = create_session_factory(engine)
    try:
        assert isinstance(factory, async_sessionmaker)
        async with factory() as session:
            assert isinstance(session, AsyncSession)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
