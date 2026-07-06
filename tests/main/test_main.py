"""Тесты клетки main: точка входа (``main.py``) и ``__main__``.

Контракт-тесты проверяют форму фасада (``set_commands``, ``apply_migrations``,
``main`` в ``remindme.main``) и сигнатуры. Logic-тесты покрывают:
- ``set_commands`` регистрирует меню команд через ``set_my_commands``;
- ``apply_migrations`` создаёт полную схему (4 таблицы + stamp версии) и
  идемпотентен (повторный ``upgrade head`` — no-op);
- ``main`` собирает инфраструктуру в контрактном порядке (миграции → engine/
  factory → Bot/Dispatcher → register_handlers → set_commands → worker-task →
  ``delete_webhook(drop_pending_updates=True)`` → ``start_polling``);
- начальная ревизия совпадает с ``Base.metadata`` 1:1 (autogenerate-diff пуст).
"""

import asyncio
import importlib
import inspect
import sqlite3
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot
from aiogram.types import BotCommand

from remindme import main as main_facade
from remindme.config import config as config_module
from remindme.db import Base
from remindme.main import apply_migrations, main, set_commands

# Модуль ``main.py`` (имя ``main`` в пакете перекрыто одноимённой функцией в
# ``__init__``, поэтому разрешаем модуль через ``importlib``).
main_module = importlib.import_module("remindme.main.main")


# --- helpers ---


def _reset_singleton() -> None:
    """Сбрасывает кэш ``get_settings`` (после установки тестовых env)."""
    config_module._settings = None


# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_main_entities() -> None:
    """Фасад экспортирует ``set_commands``, ``apply_migrations``, ``main``."""
    for name in ("set_commands", "apply_migrations", "main"):
        assert name in main_facade.__all__
        assert hasattr(main_facade, name) and callable(getattr(main_facade, name))


def test_set_commands_signature() -> None:
    """``set_commands(bot: Bot) -> None`` по контракту."""
    sig = inspect.signature(set_commands)
    assert list(sig.parameters) == ["bot"]
    assert sig.parameters["bot"].annotation is Bot


def test_apply_migrations_signature() -> None:
    """``apply_migrations() -> None`` — без параметров, синхронная."""
    sig = inspect.signature(apply_migrations)
    assert list(sig.parameters) == []
    assert not inspect.iscoroutinefunction(apply_migrations)


def test_main_signature() -> None:
    """``main() -> None`` — корутина без параметров."""
    sig = inspect.signature(main)
    assert list(sig.parameters) == []
    assert inspect.iscoroutinefunction(main)


# --- Logic-тесты: set_commands ---


async def test_set_commands_calls_set_my_commands() -> None:
    """``set_commands`` регистрирует все команды через ``set_my_commands``."""
    bot = AsyncMock()

    await set_commands(bot)

    bot.set_my_commands.assert_awaited_once()
    commands = bot.set_my_commands.await_args.args[0]
    assert isinstance(commands, list)
    assert all(isinstance(c, BotCommand) for c in commands)
    assert [c.command for c in commands] == [
        "start",
        "help",
        "remind",
        "reminders",
        "timezone",
        "cancel",
        "note",
        "notes",
        "todo",
        "todos",
        "completed",
    ]


# --- Logic-тесты: apply_migrations ---


def test_apply_migrations_creates_schema_and_idempotent(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Полная схема создаётся одной ревизией; повторный upgrade — no-op."""
    db = tmp_path / "app.db"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    _reset_singleton()

    apply_migrations()
    apply_migrations()

    con = sqlite3.connect(db)
    try:
        tables = sorted(
            r[0]
            for r in con.execute("select name from sqlite_master where type='table'")
        )
        assert {"users", "reminders", "notes", "todos"} <= set(tables)
        version = list(con.execute("select version_num from alembic_version"))
        assert len(version) == 1
        assert version[0][0]
    finally:
        con.close()


def test_initial_revision_matches_metadata(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """После ``upgrade head`` autogenerate-diff против ``Base.metadata`` пуст."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    db = tmp_path / "app.db"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    _reset_singleton()

    apply_migrations()

    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert diff == []


# --- Logic-тесты: main ---


async def test_main_assembles_and_starts_polling(monkeypatch) -> None:  # noqa: ANN001
    """``main`` собирает инфраструктуру и стартует polling в контрактном порядке."""
    order: list[str] = []

    apply_mock = MagicMock()
    monkeypatch.setattr(main_module, "apply_migrations", apply_mock)

    settings_mock = MagicMock()
    settings_mock.TELEGRAM_BOT_TOKEN.get_secret_value.return_value = "test-token"
    settings_mock.REMINDER_POLL_INTERVAL_SECONDS = 5
    settings_mock.DATABASE_URL = "sqlite+aiosqlite:///ignored.db"
    get_settings_mock = MagicMock(return_value=settings_mock)
    monkeypatch.setattr(main_module, "get_settings", get_settings_mock)

    engine_mock = MagicMock()
    engine_mock.dispose = AsyncMock()
    monkeypatch.setattr(
        main_module, "create_engine", MagicMock(return_value=engine_mock)
    )

    factory_mock = MagicMock()
    monkeypatch.setattr(
        main_module, "create_session_factory", MagicMock(return_value=factory_mock)
    )

    bot_mock = MagicMock()
    bot_mock.set_my_commands = AsyncMock()

    async def _delete_webhook(*args, **kwargs):  # noqa: ANN001, ANN002
        # Уступаем управление loop'у, чтобы фоновая задача worker успела выполниться.
        await asyncio.sleep(0)
        return True

    bot_mock.delete_webhook = AsyncMock(side_effect=_delete_webhook)
    bot_mock.session.close = AsyncMock()
    bot_ctor = MagicMock(return_value=bot_mock)
    monkeypatch.setattr(main_module, "Bot", bot_ctor)

    dp_mock = MagicMock()

    async def _start_polling(*bots, **kwargs):  # noqa: ANN001, ANN002
        assert bot_mock.delete_webhook.await_count >= 1
        assert worker_mock.call_count >= 1
        order.append("start_polling")

    dp_mock.start_polling = AsyncMock(side_effect=_start_polling)
    monkeypatch.setattr(main_module, "Dispatcher", MagicMock(return_value=dp_mock))

    register_mock = MagicMock()
    monkeypatch.setattr(main_module, "register_handlers", register_mock)

    async def _worker(**kwargs):  # noqa: ANN001
        order.append("worker")

    worker_mock = AsyncMock(side_effect=_worker)
    monkeypatch.setattr(main_module, "run_reminder_worker", worker_mock)

    await main()

    apply_mock.assert_called_once_with()
    get_settings_mock.assert_called_once_with()
    assert bot_ctor.call_args.kwargs["token"] == "test-token"
    register_mock.assert_called_once_with(dp_mock, factory_mock)
    bot_mock.set_my_commands.assert_awaited_once()
    worker_mock.assert_awaited()
    bot_mock.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)
    dp_mock.start_polling.assert_awaited()
    assert order.index("worker") < order.index("start_polling")


def test_main_module_entry_imports_main() -> None:
    """``__main__`` импортирует ``main`` из ``.main`` (точка входа ``-m``)."""
    import inspect

    import remindme.main.__main__ as entry

    assert callable(entry.main)
    assert inspect.iscoroutinefunction(entry.main)
