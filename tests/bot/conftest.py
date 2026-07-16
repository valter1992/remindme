"""Локальные фикстуры тестов клетки bot: инъекция фабрики сессий в обработчики.

Обработчики читают сессию из замыкаемой на уровне модуля переменной
``handlers._session_factory``. Для тестов она подменяется контекстным менеджером
над общей тестовой сессией фикстуры ``session`` (не закрывает её — жизненным
циклом владеет фикстура), чтобы побочные эффекты обработчиков (создание
``User``/``Reminder``/...) проверялись через ту же сессию.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from remindme.bot import handlers as handlers_module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


class _SharedSessionCtx:
    """Контекстный менеджер, отдающий общую тестовую сессию без её закрытия."""

    def __init__(self, session: "AsyncSession") -> None:
        self._session = session

    async def __aenter__(self) -> "AsyncSession":
        return self._session

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def _stub_settings(monkeypatch) -> None:
    """Подменяет ``get_settings`` в обработчиках тестовыми настройками без токена.

    Обработчики читают ``DEFAULT_TIMEZONE`` (регистрация пользователя) и
    ``DEFAULT_REMINDER_TIME`` (разбор «сегодня/завтра» без явного времени);
    реальная валидация настроек (с обязательным токеном) здесь не нужна.
    """
    monkeypatch.setattr(
        handlers_module,
        "get_settings",
        lambda: SimpleNamespace(
            DEFAULT_TIMEZONE="Europe/Moscow",
            DEFAULT_REMINDER_TIME="09:00",
        ),
    )


@pytest_asyncio.fixture
async def session_factory(session: "AsyncSession") -> "AsyncIterator[None]":
    """Подменяет ``handlers._session_factory`` общей тестовой сессией.

    Args:
        session: общая чистая ``AsyncSession`` из корневого ``conftest``.

    Yields:
        Ничего; побочный эффект — установка ``handlers._session_factory``.
    """
    handlers_module._session_factory = lambda: _SharedSessionCtx(session)
    yield
    handlers_module._session_factory = None
