# Настройки приложения — потребление

Домен: чтение конфигурации приложения RemindMe (токен, подключение к БД, часовой пояс, параметры worker).
Аудитория: клетки `db`, `bot`, `main` — любой модуль, которому нужны значения настроек.

Все настройки предоставляет единый синглтон `Settings` через `get_settings()`.
Напрямую конструировать `Settings()` не нужно — используйте точку доступа.

## Чтение настроек

```python
from .config import get_settings

settings = get_settings()
database_url = settings.DATABASE_URL
timezone = settings.DEFAULT_TIMEZONE
poll_interval = settings.REMINDER_POLL_INTERVAL_SECONDS
```

`get_settings()` создаёт и валидирует `Settings` один раз; повторные вызовы возвращают тот же экземпляр.
Переменные окружения имеют приоритет над `.env`.

## Доступ к токену бота

`TELEGRAM_BOT_TOKEN` — `SecretStr`. Значение получают явно; оно не печатается в repr и логах.

```python
token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
```

## Предусловия и побочные эффекты

- Первый вызов `get_settings()` читает `.env` и выполняет валидаторы; при отсутствии токена, неизвестной зоне
  или неверном формате времени поднимается исключение — вызывайте до старта event loop.
- Токен не должен попадать в логи, тексты ошибок и исключения; передавайте его только в `Bot(token=...)`.
- Значения уже валидны: часовой пояс — корректная IANA-зона, `DEFAULT_REMINDER_TIME` — `ЧЧ:ММ`,
  `REMINDER_POLL_INTERVAL_SECONDS` — положительное целое.
