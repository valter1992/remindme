# pydantic-settings — конфигурация приложения

Домен: загрузка и валидация настроек RemindMe из файла `.env` и переменных окружения.
Аудитория: авторы `src/config.py`.

Приложение читает `.env` по умолчанию. При отсутствии обязательной переменной, неверном часовом поясе или неверном формате времени запуск завершается с понятной ошибкой. Значения токенов в ошибку и логи не попадают.

## Базовый класс настроек

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    TELEGRAM_BOT_TOKEN: str
    DATABASE_URL: str = "sqlite+aiosqlite:///data/remindme.db"
    DEFAULT_TIMEZONE: str = "Europe/Moscow"
    DEFAULT_REMINDER_TIME: str = "09:00"
    REMINDER_POLL_INTERVAL_SECONDS: int = 5
    LOG_LEVEL: str = "INFO"
```

## Сокрытие токена

Токен хранится как `SecretStr`, поэтому он не отображается в repr объекта настроек и в логах.

```python
from pydantic import SecretStr

TELEGRAM_BOT_TOKEN: SecretStr
```

Доступ к значению — явно через `.get_secret_value()`, что исключает случайную печать.

## Валидация часового пояса

Имя зоны проверяется через стандартный модуль `zoneinfo`; несуществующая зона отклоняется при старте.

```python
from zoneinfo import ZoneInfo, available_timezones
from pydantic import field_validator

@field_validator("DEFAULT_TIMEZONE")
@classmethod
def _valid_timezone(cls, value: str) -> str:
    if value not in available_timezones():
        raise ValueError(f"unknown IANA timezone: {value}")
    ZoneInfo(value)
    return value
```

## Валидация времени по умолчанию

`DEFAULT_REMINDER_TIME` должен быть строкой `ЧЧ:ММ`.

```python
@field_validator("DEFAULT_REMINDER_TIME")
@classmethod
def _valid_time(cls, value: str) -> str:
    hours, _, minutes = value.partition(":")
    if not (len(hours) == 2 and hours.isdigit() and minutes.isdigit()):
        raise ValueError("DEFAULT_REMINDER_TIME must be HH:MM")
    h, m = int(hours), int(minutes)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("DEFAULT_REMINDER_TIME must be HH:MM")
    return value
```

## Точка доступа к настройкам

Один экземпляр настроек создаётся один раз при запуске; переопределение переменных окружения имеет приоритет над `.env`.

```python
settings = Settings()
```

## Интервал опроса worker

`REMINDER_POLL_INTERVAL_SECONDS` ограничивается положительным целым; ноль и отрицательные значения отклоняются, чтобы цикл worker не уходил в плотный поллинг.
