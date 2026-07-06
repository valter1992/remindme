"""Тесты клетки config: ``Settings`` и ``get_settings``.

Контракт-тесты проверяют форму фасада и API (имена, типы, дефолты, наличие
валидаторов); logic-тесты — поведение валидаторов и синглтона. Переменные
окружения изолированы фикстурой ``clean_config_env`` (tests/config/conftest.py),
поэтому каждый тест hermetic и не зависит от окружения тестовой машины.
"""

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings

from remindme.config import Settings, get_settings


def _reset_singleton() -> None:
    """Сбрасывает кэш ``get_settings`` между тестами."""
    from remindme.config import config as config_module

    config_module._settings = None


# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_settings_and_get_settings() -> None:
    """Фасад экспонирует ``Settings`` и ``get_settings`` как вызываемые объекты."""
    assert callable(Settings)
    assert callable(get_settings)


def test_settings_is_basessettings_subclass() -> None:
    """``Settings`` наследуется от ``BaseSettings``."""
    assert issubclass(Settings, BaseSettings)


def test_settings_fields_and_defaults() -> None:
    """Поля с дефолтами принимают значения по контракту."""
    settings = Settings(TELEGRAM_BOT_TOKEN="abc")

    assert settings.DATABASE_URL == "sqlite+aiosqlite:///data/remindme.db"
    assert settings.DEFAULT_TIMEZONE == "Europe/Moscow"
    assert settings.DEFAULT_REMINDER_TIME == "09:00"
    assert settings.REMINDER_POLL_INTERVAL_SECONDS == 5
    assert settings.LOG_LEVEL == "INFO"


def test_settings_token_is_secretstr() -> None:
    """Токен хранится как ``SecretStr``; значение доступно только явно."""
    settings = Settings(TELEGRAM_BOT_TOKEN="super-secret")

    assert isinstance(settings.TELEGRAM_BOT_TOKEN, SecretStr)
    assert settings.TELEGRAM_BOT_TOKEN.get_secret_value() == "super-secret"


def test_settings_token_absent_from_repr() -> None:
    """Значение токена не попадает в repr/str объекта настроек."""
    settings = Settings(TELEGRAM_BOT_TOKEN="super-secret")

    assert "super-secret" not in repr(settings)
    assert "super-secret" not in str(settings)


def test_settings_validators_exist() -> None:
    """Все три валидатора присутствуют как методы класса."""
    assert callable(Settings.validate_timezone)
    assert callable(Settings.validate_reminder_time)
    assert callable(Settings.validate_poll_interval)


def test_get_settings_returns_settings(monkeypatch) -> None:
    """``get_settings`` возвращает экземпляр ``Settings``."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "facade-token")
    _reset_singleton()

    settings = get_settings()

    assert isinstance(settings, Settings)


# --- Logic-тесты: валидаторы и синглтон ---


@pytest.mark.parametrize("bad_interval", [0, -1, -5])
def test_settings_poll_interval_validation(bad_interval, monkeypatch) -> None:
    """Неположительный интервал опроса отклоняется; токен не в тексте ошибки."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")

    with pytest.raises(ValidationError) as exc_info:
        Settings(REMINDER_POLL_INTERVAL_SECONDS=bad_interval)

    assert "abc" not in str(exc_info.value)


@pytest.mark.parametrize("bad_zone", ["Not/A/Zone", "Foo/Bar", "Mars/Olympus"])
def test_settings_unknown_timezone_rejected(bad_zone, monkeypatch) -> None:
    """Неизвестная IANA-зона отклоняется при валидации."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")

    with pytest.raises(ValidationError):
        Settings(DEFAULT_TIMEZONE=bad_zone)


@pytest.mark.parametrize(
    "bad_time", ["25:00", "9:00", "09:60", "abc:def", "12", "09:5", "-1:00"]
)
def test_settings_bad_reminder_time_rejected(bad_time, monkeypatch) -> None:
    """Некорректное ``DEFAULT_REMINDER_TIME`` отклоняется при валидации."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")

    with pytest.raises(ValidationError):
        Settings(DEFAULT_REMINDER_TIME=bad_time)


def test_settings_token_is_secretstr_and_absent_from_error(monkeypatch) -> None:
    """При ``ValidationError`` токен не попадает в текст ошибки."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "super-secret-token-xyz")

    with pytest.raises(ValidationError) as exc_info:
        Settings(DEFAULT_TIMEZONE="Not/A/Zone")

    assert "super-secret-token-xyz" not in str(exc_info.value)


def test_settings_missing_token_raises(monkeypatch) -> None:
    """Запуск без токена завершается понятной ``ValidationError``."""
    # clean_config_env уже удалил TELEGRAM_BOT_TOKEN; kw_only — обязателен.

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert "TELEGRAM_BOT_TOKEN" in str(exc_info.value)


def test_settings_env_overrides_dotenv(monkeypatch, tmp_path) -> None:
    """Переменная окружения имеет приоритет над значением из ``.env``."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=token-from-dotenv\nDEFAULT_TIMEZONE=Europe/Berlin\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-from-env")

    settings = Settings()

    assert settings.TELEGRAM_BOT_TOKEN.get_secret_value() == "token-from-env"
    assert settings.DEFAULT_TIMEZONE == "Europe/Berlin"


def test_get_settings_singleton(monkeypatch) -> None:
    """Повторный вызов ``get_settings`` возвращает тот же экземпляр."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "singleton-token")
    _reset_singleton()

    first = get_settings()
    second = get_settings()

    assert first is second
