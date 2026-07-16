"""Smoke-тесты каркаса проекта.

Проверяют, что корневой пакет импортируется, а общие фикстуры ``fixed_now``
и ``bot`` работают. Это гарантирует, что инфраструктура Task 1
(virtualenv, pyproject.toml, conftest) функциональна, и ``pytest tests/ -x``
завершается зелёным.
"""


def test_root_package_imports() -> None:
    """Корневой пакет ``remindme`` импортируется без побочных эффектов."""
    import remindme

    assert remindme.__all__ == []


def test_fixed_now_is_aware_utc(fixed_now: object) -> None:
    """Фикстура ``fixed_now`` возвращает aware datetime в UTC."""
    from datetime import datetime

    assert isinstance(fixed_now, datetime)
    assert fixed_now.utcoffset() is not None
    assert fixed_now.year == 2026


def test_bot_fixture_is_async_mock(bot: object) -> None:
    """Фикстура ``bot`` возвращает mock с async-методами aiogram Bot."""
    from unittest.mock import AsyncMock

    assert isinstance(bot, AsyncMock)
