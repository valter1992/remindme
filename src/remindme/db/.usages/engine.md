# Движок и фабрика сессий — bootstrap

Домен: создание async-движка SQLite и фабрики сессий на старте приложения.
Аудитория: клетка `main` (точка входа) и тестовые фикстуры `tests/conftest.py`.

Движок и фабрика создаются один раз при запуске и переиспользуются всеми обработчиками.

## Создание движка и фабрики

```python
from .config import get_settings
from .db.session import create_engine, create_session_factory

settings = get_settings()
engine = create_engine(settings)
session_factory = create_session_factory(engine)
```

`create_engine` включает `PRAGMA foreign_keys=ON` и `journal_mode=WAL` на каждом соединении и создаёт директорию `data/`. `create_session_factory` возвращает вызываемую фабрику с `expire_on_commit=False`.

## Использование фабрики

```python
async with session_factory() as session:
    ...
    await session.commit()
```

Каждый вызов `session_factory()` открывает новую `AsyncSession`; контекстный менеджер закрывает её автоматически.

## Предусловия и побочные эффекты

- `create_engine` читает `settings.DATABASE_URL`; родительская директория файла БД создаётся при необходимости.
- PRAGMA применяются через listener события `connect` — на каждом новом соединении.
- В тестах используйте временную БД (`sqlite+aiosqlite:///<tmp>/test.db`) вместо `data/remindme.db`.
