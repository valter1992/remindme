# Сценарий задачи — создание со сроком

Домен: бизнес-сценарий задачи: парсер срока и сохранение.
Аудитория: клетка `bot` (`cmd_todo`) и тесты.

Сценарий оркестрирует parse_todo_input → create_todo. Принимает AsyncSession, now (aware UTC), timezone (IANA). Не знает о Telegram.

## Создание задачи

Возвращает созданный Todo либо TodoError (без сохранения при ошибке).

```python
from .services import create_todo_scenario, TodoError

outcome = await create_todo_scenario(
    session=session,
    telegram_user_id=user_id,
    raw=message.text,        # "2026-06-25 18:00 | Отчёт" или "Купить хлеб"
    now=fixed_now,
    timezone=user.timezone,
)
if isinstance(outcome, TodoError):
    # kind: invalid_format / empty_text / text_too_long
    ...
else:
    todo = outcome  # Todo ORM (status="active")
```

## Формат срока

- «ГГГГ-ММ-ДД ЧЧ:ММ | текст» → задача со сроком (aware UTC после локализации через `timezone`).
- Текст без «|» → задача без срока (due_at_utc=None).
- Срок в прошлом допускается (просроченная активная); доставки нет (у задач нет worker).
- Без DST-валидации и горизонта (отличается от parse_remind_time).

## Предусловия и побочные эффекты

- session открыта; фиксация — в `create_todo` (репозиторий).
- now передаётся явно (aware UTC).
- При TodoError запись не создаётся.
