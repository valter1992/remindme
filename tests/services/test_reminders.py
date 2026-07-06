"""Тесты клетки services: сценарии напоминаний (``reminders.py``).

Контракт-тесты проверяют форму фасада и сигнатуры (``create_reminder_scenario``,
``set_timezone_scenario``, ``cancel_reminder_scenario``) и типы возврата;
logic-тесты — поток parse→repo (успех, ParseError без сохранения), валидацию
IANA-зоны до БД, обновление часового пояса и делегирование отмены для всех трёх
``CancelOutcome.kind``. Все операции идут через фикстуру ``session`` на
``tmp_path``; ``now`` фиксируется явно (aware UTC).
"""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from remindme.db import (
    CancelOutcome,
    Reminder,
    User,
    create_reminder,
    list_reminders,
)
from remindme.services import (
    ParseError,
    cancel_reminder_scenario,
    create_reminder_scenario,
    set_timezone_scenario,
)

# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_reminder_scenarios() -> None:
    """Фасад экспортирует три сценария напоминаний."""
    assert callable(create_reminder_scenario)
    assert callable(set_timezone_scenario)
    assert callable(cancel_reminder_scenario)


def test_create_reminder_scenario_signature() -> None:
    """``create_reminder_scenario(session, telegram_user_id, raw, now, ...)``."""
    params = create_reminder_scenario.__code__.co_varnames[
        : create_reminder_scenario.__code__.co_argcount
    ]
    assert params == (
        "session",
        "telegram_user_id",
        "raw",
        "now",
        "timezone",
        "default_time",
    )


def test_set_timezone_scenario_signature() -> None:
    """``set_timezone_scenario(session, telegram_user_id, timezone, now)``."""
    params = set_timezone_scenario.__code__.co_varnames[
        : set_timezone_scenario.__code__.co_argcount
    ]
    assert params == ("session", "telegram_user_id", "timezone", "now")


def test_cancel_reminder_scenario_signature() -> None:
    """``cancel_reminder_scenario(session, telegram_user_id, reminder_id)``."""
    params = cancel_reminder_scenario.__code__.co_varnames[
        : cancel_reminder_scenario.__code__.co_argcount
    ]
    assert params == ("session", "telegram_user_id", "reminder_id")


async def test_create_reminder_scenario_return_types(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Возвращает ``Reminder | ParseError`` (не третьего типа)."""
    await _make_user(session, telegram_user_id=42)

    ok = await create_reminder_scenario(
        session=session,
        telegram_user_id=42,
        raw="напомни через 1 минуту дело",
        now=fixed_now,
        timezone="Europe/Moscow",
        default_time="09:00",
    )
    assert isinstance(ok, (Reminder, ParseError))

    err = await create_reminder_scenario(
        session=session,
        telegram_user_id=42,
        raw="напомни в пятницу вечером дело",
        now=fixed_now,
        timezone="Europe/Moscow",
        default_time="09:00",
    )
    assert isinstance(err, (Reminder, ParseError))


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


async def test_create_reminder_scenario_success(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Успешный разбор → ``create_reminder`` → запись scheduled в БД."""
    await _make_user(session, telegram_user_id=42)

    result = await create_reminder_scenario(
        session=session,
        telegram_user_id=42,
        raw="напомни через 15 минут позвонить",
        now=fixed_now,
        timezone="Europe/Moscow",
        default_time="09:00",
    )

    assert isinstance(result, Reminder)
    assert result.user_id == 42
    assert result.text == "позвонить"
    assert result.status == "scheduled"
    assert result.attempt_count == 0

    reminders = await list_reminders(session=session, user_id=42)
    assert len(reminders) == 1
    assert reminders[0].text == "позвонить"
    assert reminders[0].status == "scheduled"


async def test_create_reminder_scenario_parse_error_no_save(
    session: AsyncSession,
    fixed_now,
) -> None:
    """``ParseError`` возвращается без сохранения — БД остаётся пустой."""
    result = await create_reminder_scenario(
        session=session,
        telegram_user_id=42,
        raw="напомни в пятницу вечером позвонить",  # invalid_format
        now=fixed_now,
        timezone="Europe/Moscow",
        default_time="09:00",
    )

    assert isinstance(result, ParseError)
    assert result.kind == "invalid_format"

    reminders = await list_reminders(session=session, user_id=42)
    assert reminders == []


async def test_set_timezone_scenario_unknown_returns_false(fixed_now) -> None:
    """Неизвестная IANA-зона → ``False`` без обращения к БД."""
    session = AsyncMock()

    result = await set_timezone_scenario(
        session=session,
        telegram_user_id=42,
        timezone="Not/AZone",
        now=fixed_now,
    )

    assert result is False
    session.execute.assert_not_called()


async def test_set_timezone_scenario_updates(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Валидная зона → ``set_user_timezone`` → пользователь обновлён."""
    await _make_user(session, telegram_user_id=42)

    result = await set_timezone_scenario(
        session=session,
        telegram_user_id=42,
        timezone="Europe/Berlin",
        now=fixed_now,
    )

    assert result is True

    session.expire_all()
    refreshed = (
        await session.execute(select(User).where(User.telegram_user_id == 42))
    ).scalar_one()
    assert refreshed.timezone == "Europe/Berlin"
    assert refreshed.updated_at_utc == fixed_now.isoformat()


async def test_set_timezone_scenario_missing_user_returns_false(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Валидная зона, но пользователя нет → ``rowcount=0`` → ``False``."""
    result = await set_timezone_scenario(
        session=session,
        telegram_user_id=42,
        timezone="Europe/Berlin",
        now=fixed_now,
    )

    assert result is False


@pytest.mark.parametrize(
    ("status", "expected_kind"),
    [
        ("scheduled", "cancelled"),
        ("sending", "already_sending"),
        ("cancelled", "not_found"),
    ],
)
async def test_cancel_reminder_scenario_delegates(
    session: AsyncSession,
    fixed_now,
    status: str,
    expected_kind: str,
) -> None:
    """Делегирует ``cancel_reminder`` для всех трёх ``CancelOutcome.kind``."""
    await _make_user(session, telegram_user_id=42)

    reminder = await create_reminder(
        session=session,
        telegram_user_id=42,
        text="Дело",
        remind_at_utc=fixed_now + timedelta(hours=1),
        now=fixed_now,
    )
    reminder.status = status
    await session.commit()

    result = await cancel_reminder_scenario(
        session=session,
        telegram_user_id=42,
        reminder_id=reminder.id,
    )

    assert isinstance(result, CancelOutcome)
    assert result.kind == expected_kind


async def test_cancel_reminder_scenario_foreign_user_not_found(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Чужое напоминание не отменяется — ``not_found`` (изоляция по владельцу)."""
    await _make_user(session, telegram_user_id=42)
    await _make_user(session, telegram_user_id=43)

    reminder = await create_reminder(
        session=session,
        telegram_user_id=43,
        text="Чужое",
        remind_at_utc=fixed_now + timedelta(hours=1),
        now=fixed_now,
    )

    result = await cancel_reminder_scenario(
        session=session,
        telegram_user_id=42,
        reminder_id=reminder.id,
    )

    assert isinstance(result, CancelOutcome)
    assert result.kind == "not_found"
