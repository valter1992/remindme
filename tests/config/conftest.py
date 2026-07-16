"""Локальные фикстуры тестов клетки config.

Изолируют конфигурационные переменные окружения, чтобы каждый тест клетки
config был hermetic: настройки не утекают из реального окружения тестовой машины
в ``Settings()`` и ``get_settings()``.
"""

import pytest

CONFIG_ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "DATABASE_URL",
    "DEFAULT_TIMEZONE",
    "DEFAULT_REMINDER_TIME",
    "REMINDER_POLL_INTERVAL_SECONDS",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch):
    """Удаляет все конфигурационные переменные окружения перед каждым тестом."""
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
