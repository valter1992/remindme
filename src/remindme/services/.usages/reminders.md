# Сценарии напоминаний — создание и отмена

Домен: бизнес-сценарии напоминаний поверх парсера и репозитория.
Аудитория: клетка `bot` (`cmd_remind`, `handle_remind_phrase`) и тесты (`cancel_reminder_scenario`); UI отмены (кнопка «Удалить») — этап 4.

Сценарии оркестрируют парсер → репозиторий. Принимают открытую `AsyncSession` и `now` (aware UTC). Не знают о Telegram.

## Создание напоминания

Возвращает созданный `Reminder` либо `ParseError` (без сохранения записи при ошибке парсинга).

```python
from .services import create_reminder_scenario, ParseError

outcome = await create_reminder_scenario(
    session=session,
    telegram_user_id=user_id,
    raw=message.text,
    now=fixed_now,
    timezone=user.timezone,
    default_time="09:00",
)
if isinstance(outcome, ParseError):
    # показать формат и пример; запись не создана
    ...
else:
    reminder = outcome  # Reminder ORM (status="scheduled")
```

## Отмена напоминания

Делегирует репозиторию с проверкой владельца; возвращает различимый исход `CancelOutcome`.

```python
from .services import cancel_reminder_scenario

outcome = await cancel_reminder_scenario(
    session=session,
    telegram_user_id=user_id,
    reminder_id=reminder_id,
)
# outcome.kind ∈ {cancelled, not_found, already_sending}
```

## Предусловия и побочные эффекты

- `session` открыта; сценарий создания фиксирует транзакцию через репозиторий, откат при `SQLAlchemyError` — в репозитории.
- При `ParseError` запись не создаётся.
- `now` передаётся явно (aware UTC); `datetime.now()` внутри не вызывается.

## Смена часового пояса и исход отмены — этап 3

```python
from .services import set_timezone_scenario, cancel_reminder_scenario, CancelOutcome

# смена зоны: валидация IANA (ZoneInfo) + запись; True — зона изменена, False — неизвестный пояс
updated = await set_timezone_scenario(
    session=session, telegram_user_id=user_id, timezone="Europe/Moscow", now=now,
)

# отмена возвращает различимый исход
outcome = await cancel_reminder_scenario(
    session=session, telegram_user_id=user_id, reminder_id=reminder_id,
)
# outcome.kind ∈ {cancelled, not_found, already_sending}
```

- `set_timezone_scenario` проверяет зону через `ZoneInfo` до записи: неизвестная IANA → `False` (без обращения к БД);
  валидная → `set_user_timezone`. Уже созданные напоминания не сдвигаются — меняется только их отображение.
- `cancel_reminder_scenario` отличает отмену (`cancelled`), отсутствие/чужую запись (`not_found`) и запись, уже
  переведённую worker в `sending` (`already_sending`).