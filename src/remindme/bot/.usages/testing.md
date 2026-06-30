# Handlers и formatter — вызов в тестах

Домен: проверка обработчиков и форматеров прямым вызовом с mock Telegram API.
Аудитория: тесты `tests/unit` и `tests/integration`.

Обработчики вызываются как async-функции; `Message` подменяется объектом с нужными атрибутами, отправка ответа — mock. Текущий момент и часовой пояс передаются явно; реальный `sleep` не используется.

## /remind и фраза «напомни»

```python
from unittest.mock import AsyncMock
from .bot.handlers import cmd_remind, handle_remind_phrase

message = AsyncMock()
message.chat.type = "private"
message.from_user.id = 123
message.text = "/remind 2026-06-24 18:00 | Купить продукты"
message.answer = AsyncMock()

await cmd_remind(message)
assert message.answer.await_count == 1
```

## Formatter (прямой вызов)

```python
from .bot import format_notification, to_local_string

assert format_notification(reminder) == "Напоминание\nКупить продукты"
assert to_local_string("2026-06-24T15:00:00+00:00", "Europe/Moscow")  # локальная строка
```

## Предусловия и побочные эффекты

- Telegram Bot API (`message.answer`, `bot.send_message`) подменяется mock; проверяются аргументы вызовов.
- Побочные эффекты `/remind`/`handle_remind_phrase` (создание `Reminder`) проверяются через временную SQLite-сессию.
- `now` и часовой пояс передаются явно; `datetime.now()` внутри логики не вызывается.