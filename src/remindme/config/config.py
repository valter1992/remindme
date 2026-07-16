"""Конфигурация приложения RemindMe.

Загрузка и валидация настроек из файла ``.env`` и переменных окружения через
``pydantic-settings``. Токен Telegram хранится как ``SecretStr`` и не попадает в
repr, логи и тексты ошибок. Переменные окружения имеют приоритет над ``.env``.
"""

from zoneinfo import ZoneInfo, available_timezones

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Конфигурация приложения RemindMe.

    Единственный источник настроек для всех клеток. Загружается из ``.env`` и
    переменных окружения через ``pydantic-settings``; переменные окружения имеют
    приоритет над ``.env``.

    Raises:
        pydantic.ValidationError: если отсутствует ``TELEGRAM_BOT_TOKEN``,
            ``DEFAULT_TIMEZONE`` не является корректной IANA-зоной,
            ``DEFAULT_REMINDER_TIME`` не в формате ``ЧЧ:ММ`` либо
            ``REMINDER_POLL_INTERVAL_SECONDS`` не положителен. Текст ошибки не
            содержит значения токена.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        kw_only=True,
        extra="ignore",
    )

    TELEGRAM_BOT_TOKEN: SecretStr
    DATABASE_URL: str = "sqlite+aiosqlite:///data/remindme.db"
    DEFAULT_TIMEZONE: str = "Europe/Moscow"
    DEFAULT_REMINDER_TIME: str = "09:00"
    REMINDER_POLL_INTERVAL_SECONDS: int = 5
    LOG_LEVEL: str = "INFO"

    @field_validator("DEFAULT_TIMEZONE")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Проверяет, что значение — корректное имя IANA-зоны.

        Args:
            value: исходное значение поля ``DEFAULT_TIMEZONE``.

        Returns:
            Провалидированное значение, если оно корректно.

        Raises:
            ValueError: если значение отсутствует в ``available_timezones()``
                или ``ZoneInfo`` не строится из него.
        """
        if value not in available_timezones():
            raise ValueError(f"unknown IANA timezone: {value}")

        ZoneInfo(value)
        return value

    @field_validator("DEFAULT_REMINDER_TIME")
    @classmethod
    def validate_reminder_time(cls, value: str) -> str:
        """Проверяет, что значение — строка ``ЧЧ:ММ`` с корректным диапазоном.

        Args:
            value: исходное значение поля ``DEFAULT_REMINDER_TIME``.

        Returns:
            Провалидированное значение, если оно корректно.

        Raises:
            ValueError: если часы не две цифры в диапазоне 0..23 или минуты не
                две цифры в диапазоне 0..59.
        """
        hours, _, minutes = value.partition(":")
        if not (
            len(hours) == 2
            and hours.isdigit()
            and len(minutes) == 2
            and minutes.isdigit()
        ):
            raise ValueError("DEFAULT_REMINDER_TIME must be HH:MM")

        hour, minute = int(hours), int(minutes)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("DEFAULT_REMINDER_TIME must be HH:MM")
        return value

    @field_validator("REMINDER_POLL_INTERVAL_SECONDS")
    @classmethod
    def validate_poll_interval(cls, value: int) -> int:
        """Запрещает ноль и отрицательные значения интервала опроса worker.

        Args:
            value: исходное значение поля ``REMINDER_POLL_INTERVAL_SECONDS``.

        Returns:
            Провалидированное положительное значение.

        Raises:
            ValueError: если значение не положительно.
        """
        if value <= 0:
            raise ValueError("REMINDER_POLL_INTERVAL_SECONDS must be positive")
        return value


_settings: Settings | None = None


def get_settings() -> Settings:
    """Возвращает синглтон настроек; создание и валидация выполняются один раз.

    При первом вызове создаёт и валидирует экземпляр ``Settings``, при
    последующих — возвращает закешированный экземпляр.

    Returns:
        Закешированный экземпляр ``Settings``.
    """
    global _settings

    if _settings is None:
        _settings = Settings()
    return _settings
