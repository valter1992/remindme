"""Общие фикстуры RemindMe.

Содержит базовые shared-фикстуры, доступные всем тестам:
- ``fixed_now`` — детерминированный aware-UTC момент времени (явный ``now``).
- ``bot`` — mock aiogram ``Bot`` для тестов handlers и worker.

Фикстура ``session`` добавляется в ``tests/conftest.py`` в Task 5 после
появления ORM-моделей и ``Base``.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest


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
