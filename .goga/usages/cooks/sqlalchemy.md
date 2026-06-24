# SQLAlchemy 2.x (async) — работа с SQLite

Домен: асинхронный доступ к данным через SQLAlchemy 2.x с драйвером aiosqlite.
Аудитория: авторы моделей `src/db/models.py` и репозиториев `src/db/repositories.py`.

Все операции с данными асинхронны. Даты хранятся в UTC в формате ISO 8601. Каждая операция с пользовательской сущностью фильтруется одновременно по `id` записи и `user_id` текущего Telegram-пользователя; поиск только по `id` запрещён.

## Движок и сессии

```python
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

engine: AsyncEngine = create_async_engine(
    "sqlite+aiosqlite:///data/remindme.db",
    echo=False,
)
Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

## Включение foreign_keys и WAL

SQLite требует явного включения внешних ключей на каждом соединении. Режим журнала `WAL` задаётся один раз. Оба PRAGMA выполняются через синхронный слушатель события DBAPI-соединения.

```python
from sqlalchemy import event

@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
```

## Декларативные модели

Современный стиль 2.x: `DeclarativeBase`, `Mapped`, `mapped_column`.

```python
from datetime import datetime
from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    telegram_user_id: Mapped[int] = mapped_column(primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
```

Строковые timestamp-поля хранятся как `Text` (ISO 8601), чтобы не зависеть от собственной поддержки типов SQLite под драйвером.

## Репозиторий: безопасное изменение по владельцу

Изменение и удаление всегда ограничены парой `(id, user_id)`.

```python
from sqlalchemy import delete, select, update

async def delete_note(session: AsyncSession, note_id: int, user_id: int) -> bool:
    result = await session.execute(
        delete(Note).where(Note.id == note_id, Note.user_id == user_id)
    )
    await session.commit()
    return result.rowcount == 1
```

Возврат `rowcount` позволяет отличить «не найдено/не владельцу» от успешного удаления без раскрытия чужих данных.

## Списки с лимитом и сортировкой

Каждый список возвращает не более 20 записей.

```python
async def list_reminders(session: AsyncSession, user_id: int) -> list[Reminder]:
    result = await session.execute(
        select(Reminder)
        .where(Reminder.user_id == user_id, Reminder.status == "scheduled")
        .order_by(Reminder.remind_at_utc.asc())
        .limit(20)
    )
    return list(result.scalars())
```

## Транзакции и откаты

Ошибки SQLite приводят к откату транзакции; бот сообщает пользователю, что запись не сохранена.

```python
try:
    session.add(reminder)
    await session.commit()
except SQLAlchemyError:
    await session.rollback()
    raise
```

## Перевод статуса в одной транзакции

Worker переводит напоминание из `scheduled` в `sending` атомарно, чтобы один и тот же запись не забрали две итерации.

```python
result = await session.execute(
    update(Reminder)
    .where(
        Reminder.id == reminder_id,
        Reminder.status == "scheduled",
    )
    .values(status="sending", locked_at_utc=now_iso)
)
await session.commit()
return result.rowcount == 1
```

Проверка `status == "scheduled"` в `where` обеспечивает захват ровно одной записью без отдельной блокировки.
