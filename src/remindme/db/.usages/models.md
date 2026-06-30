# Модели и сессия — ORM-операции

Домен: работа с моделями `User`, `Reminder`, `Note`, `Todo` через `AsyncSession`.
Аудитория: клетка `bot` (создание/поиск `User`) и тесты.

Все timestamp-поля — строки ISO 8601 (UTC). Внешние ключи включены, поэтому операции с несуществующим `user_id` завершаются `IntegrityError`.

## Создание записи пользователя

```python
from .db.models import User

user = User(
    telegram_user_id=telegram_id,
    timezone="Europe/Moscow",
    created_at_utc=now_iso,
    updated_at_utc=now_iso,
)
session.add(user)
await session.commit()
```

При нарушении `UNIQUE` по `telegram_user_id` (конкурентная регистрация) поднимается `IntegrityError` — используйте поиск существующей записи/upsert.

## Доступ к схеме

```python
from .db.models import Base

metadata = Base.metadata  # для миграций и create_all в тестах
```

## Предусловия и побочные эффекты

- Перед `commit` значения timestamp должны быть строками ISO 8601 UTC.
- `expire_on_commit=False`: объекты остаются доступны после `commit`.
