# Сценарий заметки — создание

Домен: бизнес-сценарий заметки поверх репозитория.
Аудитория: клетка `bot` (`cmd_note`) и тесты.

Сценарий валидирует текст и сохраняет. Принимает открытую AsyncSession и now (aware UTC). Не знает о Telegram.

## Создание заметки

Возвращает созданный Note либо NoteError (без сохранения при ошибке валидации).

```python
from .services import create_note_scenario, NoteError

outcome = await create_note_scenario(
    session=session,
    telegram_user_id=user_id,
    raw=message.text,
    now=fixed_now,
)
if isinstance(outcome, NoteError):
    # kind: empty_text / text_too_long — показать текст отказа; запись не создана
    ...
else:
    note = outcome  # Note ORM
```

## Предусловия и побочные эффекты

- session открыта; фиксация транзакции — в `create_note` (репозиторий), откат при SQLAlchemyError — там же.
- now передаётся явно (aware UTC); datetime.now() внутри не вызывается.
- При NoteError запись не создаётся.
