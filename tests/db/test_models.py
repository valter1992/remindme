"""Тесты клетки db: ORM-модели ``Base``, ``User``, ``Reminder``, ``Note``, ``Todo``.

Контракт-тесты проверяют форму фасада и схему (имена классов, наличие
``Mapped``-колонок, состав таблиц в ``Base.metadata``, FK); logic-тесты —
поведение ограничений (UNIQUE, FK, дефолты) через фикстуру ``session``.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped

from remindme.db import Base, Note, Reminder, Todo, User

# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_models() -> None:
    """Фасад экспортирует ``Base`` и четыре модели."""
    assert issubclass(Base, DeclarativeBase)
    assert issubclass(User, Base)
    assert issubclass(Reminder, Base)
    assert issubclass(Note, Base)
    assert issubclass(Todo, Base)


def test_metadata_has_four_tables() -> None:
    """``Base.metadata`` содержит ровно четыре таблицы моделей."""
    table_names = set(Base.metadata.tables)
    assert table_names == {"users", "reminders", "notes", "todos"}


def test_user_mapped_columns() -> None:
    """``User`` имеет колонки контракта.

    PK+UNIQUE telegram_user_id, timezone, created_at_utc, updated_at_utc.
    """
    columns = User.__table__.columns

    assert isinstance(User.__dict__["telegram_user_id"], Mapped)
    assert set(columns.keys()) == {
        "telegram_user_id",
        "timezone",
        "created_at_utc",
        "updated_at_utc",
    }
    assert columns["telegram_user_id"].primary_key
    assert columns["telegram_user_id"].unique


def test_reminder_mapped_columns_and_fk() -> None:
    """``Reminder`` имеет все колонки контракта и FK на users.telegram_user_id."""
    columns = Reminder.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "user_id",
        "text",
        "remind_at_utc",
        "status",
        "attempt_count",
        "next_attempt_at_utc",
        "locked_at_utc",
        "created_at_utc",
        "sent_at_utc",
    }
    assert columns["id"].primary_key
    assert columns["id"].autoincrement is True

    user_id_fks = list(columns["user_id"].foreign_keys)
    assert len(user_id_fks) == 1
    assert user_id_fks[0].column.table.name == "users"
    assert user_id_fks[0].column.name == "telegram_user_id"

    assert columns["locked_at_utc"].nullable is True
    assert columns["sent_at_utc"].nullable is True
    assert columns["remind_at_utc"].nullable is False
    assert columns["status"].nullable is False


def test_note_mapped_columns_and_fk() -> None:
    """``Note`` имеет колонки контракта и FK на users."""
    columns = Note.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "user_id",
        "text",
        "created_at_utc",
    }
    assert columns["id"].primary_key

    user_id_fks = list(columns["user_id"].foreign_keys)
    assert len(user_id_fks) == 1
    assert user_id_fks[0].column.table.name == "users"


def test_todo_mapped_columns_and_fk() -> None:
    """``Todo`` имеет колонки контракта, nullable due/completed, FK на users."""
    columns = Todo.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "user_id",
        "text",
        "due_at_utc",
        "status",
        "created_at_utc",
        "completed_at_utc",
    }
    assert columns["id"].primary_key
    assert columns["due_at_utc"].nullable is True
    assert columns["completed_at_utc"].nullable is True
    assert columns["status"].nullable is False

    user_id_fks = list(columns["user_id"].foreign_keys)
    assert len(user_id_fks) == 1
    assert user_id_fks[0].column.table.name == "users"


# --- Logic-тесты: поведение ограничений через фикстуру session ---


_NOW_ISO = "2026-06-24T12:00:00+00:00"


async def _make_user(session, telegram_user_id: int = 42) -> None:  # noqa: ANN001
    """Создаёт пользователя с заданным ``telegram_user_id``."""
    session.add(
        User(
            telegram_user_id=telegram_user_id,
            timezone="Europe/Moscow",
            created_at_utc=_NOW_ISO,
            updated_at_utc=_NOW_ISO,
        )
    )
    await session.commit()


async def test_user_pk_unique_integrity_error(session) -> None:  # noqa: ANN001
    """Дубликат ``telegram_user_id`` поднимает ``IntegrityError`` (UNIQUE)."""
    await _make_user(session, telegram_user_id=42)

    session.add(
        User(
            telegram_user_id=42,
            timezone="Europe/Berlin",
            created_at_utc=_NOW_ISO,
            updated_at_utc=_NOW_ISO,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_reminder_fk_missing_user_raises(session) -> None:  # noqa: ANN001
    """FK без существующего ``user_id`` поднимает ``IntegrityError``."""
    session.add(
        Reminder(
            user_id=999,
            text="Без владельца",
            remind_at_utc=_NOW_ISO,
            status="scheduled",
            attempt_count=0,
            next_attempt_at_utc=_NOW_ISO,
            created_at_utc=_NOW_ISO,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_reminder_status_scheduled_default(session) -> None:  # noqa: ANN001
    """Создание Reminder со статусом scheduled/attempt_count=0 сохраняется корректно."""
    await _make_user(session, telegram_user_id=42)

    reminder = Reminder(
        user_id=42,
        text="Позвонить",
        remind_at_utc="2026-06-24T15:00:00+00:00",
        status="scheduled",
        attempt_count=0,
        next_attempt_at_utc="2026-06-24T15:00:00+00:00",
        created_at_utc=_NOW_ISO,
    )
    session.add(reminder)
    await session.commit()

    assert reminder.id is not None
    assert reminder.status == "scheduled"
    assert reminder.attempt_count == 0
    assert reminder.next_attempt_at_utc == reminder.remind_at_utc
    assert reminder.locked_at_utc is None
    assert reminder.sent_at_utc is None


async def test_todo_nullable_due_and_completed(session) -> None:  # noqa: ANN001
    """Todo без срока и completed создаётся с nullable-полями равными None."""
    await _make_user(session, telegram_user_id=42)

    todo = Todo(
        user_id=42,
        text="Без срока",
        due_at_utc=None,
        status="active",
        created_at_utc=_NOW_ISO,
        completed_at_utc=None,
    )
    session.add(todo)
    await session.commit()

    assert todo.id is not None
    assert todo.due_at_utc is None
    assert todo.completed_at_utc is None
    assert todo.status == "active"
