"""Тесты клетки db: репозитории напоминаний и часового пояса (``repositories.py``).

Контракт-тесты проверяют форму фасада и сигнатуры (``create_reminder``,
``list_reminders``, ``cancel_reminder``, ``set_user_timezone``, ``CancelOutcome``);
logic-тесты — defaults при создании, лимит/изоляцию списка, статусные переходы
отмены (``cancelled``/``already_sending``/``not_found``) и изоляцию по владельцу.
Все операции идут через фикстуру ``session`` на ``tmp_path``.
"""

from datetime import timedelta

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from remindme.db import (
    CancelOutcome,
    Reminder,
    User,
    cancel_reminder,
    create_reminder,
    list_reminders,
    set_user_timezone,
)

# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_reminder_repositories() -> None:
    """Фасад экспортирует пять сущностей группы reminders+TZ."""
    assert callable(create_reminder)
    assert callable(list_reminders)
    assert callable(cancel_reminder)
    assert callable(set_user_timezone)
    assert issubclass(CancelOutcome, BaseModel)


def test_create_reminder_signature() -> None:
    """``create_reminder(session, telegram_user_id, text, remind_at_utc, now)``."""
    params = create_reminder.__code__.co_varnames[
        : create_reminder.__code__.co_argcount
    ]
    assert params == (
        "session",
        "telegram_user_id",
        "text",
        "remind_at_utc",
        "now",
    )


def test_list_reminders_signature() -> None:
    """``list_reminders(session, user_id)``."""
    params = list_reminders.__code__.co_varnames[: list_reminders.__code__.co_argcount]
    assert params == ("session", "user_id")


def test_cancel_reminder_signature() -> None:
    """``cancel_reminder(session, reminder_id, user_id)``."""
    params = cancel_reminder.__code__.co_varnames[
        : cancel_reminder.__code__.co_argcount
    ]
    assert params == ("session", "reminder_id", "user_id")


def test_set_user_timezone_signature() -> None:
    """``set_user_timezone(session, telegram_user_id, timezone, now)``."""
    params = set_user_timezone.__code__.co_varnames[
        : set_user_timezone.__code__.co_argcount
    ]
    assert params == ("session", "telegram_user_id", "timezone", "now")


@pytest.mark.parametrize("kind", ["cancelled", "not_found", "already_sending"])
def test_cancel_outcome_kinds(kind: str) -> None:
    """``CancelOutcome.kind`` допускает ровно три варианта по контракту."""
    outcome = CancelOutcome(kind=kind)
    assert outcome.kind == kind


def test_cancel_outcome_is_kw_only() -> None:
    """``CancelOutcome`` создаётся только по ключевому имени ``kind``."""
    with pytest.raises(TypeError):
        CancelOutcome("cancelled")  # type: ignore[misc]


# --- helpers ---


async def _make_user(session: AsyncSession, telegram_user_id: int = 42) -> None:
    """Создаёт пользователя с заданным ``telegram_user_id``."""
    session.add(
        User(
            telegram_user_id=telegram_user_id,
            timezone="Europe/Moscow",
            created_at_utc="2026-06-24T12:00:00+00:00",
            updated_at_utc="2026-06-24T12:00:00+00:00",
        )
    )
    await session.commit()


# --- Logic-тесты ---


async def test_create_reminder_scheduled_defaults(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Создание: status='scheduled', attempt_count=0, next=remind_at, ISO-поля."""
    await _make_user(session, telegram_user_id=42)

    remind_at = fixed_now + timedelta(hours=3)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=42,
        text="Позвонить",
        remind_at_utc=remind_at,
        now=fixed_now,
    )

    assert reminder.id is not None
    assert reminder.user_id == 42
    assert reminder.text == "Позвонить"
    assert reminder.status == "scheduled"
    assert reminder.attempt_count == 0
    assert (
        reminder.next_attempt_at_utc == reminder.remind_at_utc == remind_at.isoformat()
    )
    assert reminder.created_at_utc == fixed_now.isoformat()
    assert reminder.locked_at_utc is None
    assert reminder.sent_at_utc is None


async def test_list_reminders_limit(session: AsyncSession, fixed_now) -> None:
    """21 запись → 20, только user_id=42, ближайшие по времени (дальняя отсечена)."""
    await _make_user(session, telegram_user_id=42)

    created = []
    for hour in range(1, 22):
        reminder = await create_reminder(
            session=session,
            telegram_user_id=42,
            text=f"R{hour}",
            remind_at_utc=fixed_now + timedelta(hours=hour),
            now=fixed_now,
        )
        created.append(reminder)

    farthest = max(created, key=lambda r: r.remind_at_utc)
    nearest = min(created, key=lambda r: r.remind_at_utc)

    reminders = await list_reminders(session=session, user_id=42)

    assert len(reminders) == 20
    assert farthest.id not in {r.id for r in reminders}
    assert reminders[0].id == nearest.id
    # Порядок asc по remind_at_utc
    times = [r.remind_at_utc for r in reminders]
    assert times == sorted(times)


async def test_list_reminders_isolates_by_user(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Список возвращает только напоминания текущего пользователя."""
    await _make_user(session, telegram_user_id=42)
    await _make_user(session, telegram_user_id=43)

    await create_reminder(
        session=session,
        telegram_user_id=42,
        text="Владелец 42",
        remind_at_utc=fixed_now + timedelta(hours=1),
        now=fixed_now,
    )
    await create_reminder(
        session=session,
        telegram_user_id=43,
        text="Владелец 43",
        remind_at_utc=fixed_now + timedelta(hours=1),
        now=fixed_now,
    )

    reminders = await list_reminders(session=session, user_id=42)

    assert len(reminders) == 1
    assert reminders[0].text == "Владелец 42"


async def test_list_reminders_only_scheduled(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Список не возвращает отменённые/отправленные напоминания."""
    await _make_user(session, telegram_user_id=42)

    scheduled = await create_reminder(
        session=session,
        telegram_user_id=42,
        text="Активное",
        remind_at_utc=fixed_now + timedelta(hours=1),
        now=fixed_now,
    )
    cancelled = await create_reminder(
        session=session,
        telegram_user_id=42,
        text="Отменённое",
        remind_at_utc=fixed_now + timedelta(hours=2),
        now=fixed_now,
    )
    cancelled.status = "cancelled"
    await session.commit()

    reminders = await list_reminders(session=session, user_id=42)

    assert {r.id for r in reminders} == {scheduled.id}


async def test_cancel_reminder_scheduled_to_cancelled(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Отмена scheduled → 'cancelled' и статус в БД 'cancelled'."""
    await _make_user(session, telegram_user_id=42)

    reminder = await create_reminder(
        session=session,
        telegram_user_id=42,
        text="Отмени меня",
        remind_at_utc=fixed_now + timedelta(hours=1),
        now=fixed_now,
    )

    outcome = await cancel_reminder(
        session=session, reminder_id=reminder.id, user_id=42
    )

    assert outcome.kind == "cancelled"

    reloaded = await session.get(Reminder, reminder.id)
    assert reloaded is not None
    assert reloaded.status == "cancelled"


async def test_cancel_reminder_already_sending(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Отмена уже захваченной worker записи (status='sending') → 'already_sending'."""
    await _make_user(session, telegram_user_id=42)

    reminder = await create_reminder(
        session=session,
        telegram_user_id=42,
        text="Захвачено",
        remind_at_utc=fixed_now + timedelta(hours=1),
        now=fixed_now,
    )
    reminder.status = "sending"
    await session.commit()

    outcome = await cancel_reminder(
        session=session, reminder_id=reminder.id, user_id=42
    )

    assert outcome.kind == "already_sending"
    assert (await session.get(Reminder, reminder.id)).status == "sending"


async def test_cancel_reminder_foreign_user_returns_not_found(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Отмена чужой записи по чужому user_id → 'not_found' (изоляция владельца)."""
    await _make_user(session, telegram_user_id=42)

    reminder = await create_reminder(
        session=session,
        telegram_user_id=42,
        text="Чужое",
        remind_at_utc=fixed_now + timedelta(hours=1),
        now=fixed_now,
    )

    outcome = await cancel_reminder(
        session=session, reminder_id=reminder.id, user_id=999
    )

    assert outcome.kind == "not_found"
    assert (await session.get(Reminder, reminder.id)).status == "scheduled"


async def test_cancel_reminder_missing_returns_not_found(
    session: AsyncSession,
) -> None:
    """Отмена несуществующей записи → 'not_found'."""
    outcome = await cancel_reminder(session=session, reminder_id=12345, user_id=42)

    assert outcome.kind == "not_found"


async def test_set_user_timezone_rowcount(session: AsyncSession, fixed_now) -> None:
    """Смена зоны существующего пользователя → True; отсутствующего → False."""
    await _make_user(session, telegram_user_id=42)

    assert (
        await set_user_timezone(
            session=session,
            telegram_user_id=42,
            timezone="Europe/Berlin",
            now=fixed_now + timedelta(minutes=5),
        )
        is True
    )

    reloaded = await session.get(User, 42)
    assert reloaded is not None
    assert reloaded.timezone == "Europe/Berlin"
    assert reloaded.updated_at_utc == (fixed_now + timedelta(minutes=5)).isoformat()

    assert (
        await set_user_timezone(
            session=session,
            telegram_user_id=999,
            timezone="Europe/Berlin",
            now=fixed_now,
        )
        is False
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
