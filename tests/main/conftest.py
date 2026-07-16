"""Локальные фикстуры тестов клетки main.

Изолируют конфигурационные переменные окружения (настройки не утекают из реального
окружения тестовой машины в ``get_settings``) и сбрасывают кэш синглтона настроек
перед каждым тестом, чтобы тесты миграций могли направить ``DATABASE_URL`` на
свежий ``tmp_path``.
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
def clean_main_env(monkeypatch):
    """Удаляет конфигурационные env и сбрасывает синглтон ``get_settings``."""
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    from remindme.config import config as config_module

    config_module._settings = None


def reset_settings_singleton() -> None:
    """Сбрасывает кэш ``get_settings`` (после установки тестовых env)."""
    from remindme.config import config as config_module

    config_module._settings = None
