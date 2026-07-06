"""Тесты клетки worker: цикл доставки напоминаний (``notifications.py``).

Контракт-тесты проверяют форму фасада (``run_reminder_worker`` в
``remindme.worker``) и сигнатуру ``run_reminder_worker(bot, session_factory,
poll_interval)``. Logic-тесты вызывают выделенные внутренние шаги напрямую
(``_deliver_due`` — доставка всех просроченных в одной сессии; ``_run_iteration``
— один тик = одна сессия; ``_run_recovery`` — startup-recovery зависших
``sending``) без реального ``asyncio.sleep``: успешная доставка → ``sent``,
блокировка бота → ``failed``, преходящая ошибка → повтор (``scheduled``),
recovery перед циклом, ровно одна сессия на итерацию и пропуск отменённых.
"""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from remindme import worker as worker_facade
from remindme.db import Reminder, User, create_reminder
from remindme.worker import run_reminder_worker
from remindme.worker.notifications import (
    _deliver_due,
    _run_iteration,
    _run_recovery,
)

_USER_ID = 42


# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_run_reminder_worker() -> None:
    """Фасад клетки экспортирует ``run_reminder_worker`` через ``__all__``."""
    assert "run_reminder_worker" in worker_facade.__all__
    assert callable(run_reminder_worker)


def test_run_reminder_worker_signature() -> None:
    """``run_reminder_worker(bot, session_factory, poll_interval)``."""
    params = run_reminder_worker.__code__.co_varnames[
        : run_reminder_worker.__code__.co_argcount
    ]
    assert params == ("bot", "session_factory", "poll_interval")


# --- helpers ---


async def _make_user(session: AsyncSession, telegram_user_id: int = _USER_ID) -> None:
    """Создаёт пользователя с заданным ``telegram_user_id`` (для FK)."""
    session.add(
        User(
            telegram_user_id=telegram_user_id,
            timezone="Europe/Moscow",
            created_at_utc="2026-06-24T12:00:00+00:00",
            updated_at_utc="2026-06-24T12:00:00+00:00",
        )
    )
    await session.commit()


async def _refresh_status(
    session: AsyncSession,
    reminder_id: int,
) -> Reminder:
    """Перечитывает напоминание из БД по id (обходит identity-map staleness)."""
    reminder = await session.get(Reminder, reminder_id)
    await session.refresh(reminder)
    return reminder


# --- Logic-тесты ---


async def test_worker_delivers_due_reminder(
    bot: AsyncMock,
    session: AsyncSession,
    fixed_now,
) -> None:
    """Просроченное scheduled-напоминание: claim → send_message → mark_sent."""
    await _make_user(session)

    remind_at = fixed_now - timedelta(minutes=1)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=_USER_ID,
        text="Позвонить",
        remind_at_utc=remind_at,
        now=fixed_now,
    )

    await _deliver_due(bot, session, fixed_now)

    bot.send_message.assert_awaited_once_with(
        chat_id=_USER_ID,
        text="Напоминание\nПозвонить",
    )

    refreshed = await _refresh_status(session, reminder.id)
    assert refreshed.status == "sent"
    assert refreshed.sent_at_utc is not None


async def test_worker_forbidden_marks_failed(
    bot: AsyncMock,
    session: AsyncSession,
    fixed_now,
) -> None:
    """TelegramForbiddenError → mark_failed без повторов (status='failed')."""
    await _make_user(session)

    remind_at = fixed_now - timedelta(minutes=1)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=_USER_ID,
        text="Позвонить",
        remind_at_utc=remind_at,
        now=fixed_now,
    )
    bot.send_message.side_effect = TelegramForbiddenError(
        method="sendMessage",
        message="bot was blocked by the user",
    )

    await _deliver_due(bot, session, fixed_now)

    refreshed = await _refresh_status(session, reminder.id)
    assert refreshed.status == "failed"
    # failed-запись не ретраится: попытка всего одна.
    assert refreshed.attempt_count == 0


async def test_worker_transient_failure_schedules_retry(
    bot: AsyncMock,
    session: AsyncSession,
    fixed_now,
) -> None:
    """Иная TelegramAPIError → record_send_failure → status='scheduled', +30 c."""
    await _make_user(session)

    remind_at = fixed_now - timedelta(minutes=1)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=_USER_ID,
        text="Позвонить",
        remind_at_utc=remind_at,
        now=fixed_now,
    )
    bot.send_message.side_effect = TelegramAPIError(
        method="sendMessage",
        message="rate limit exceeded",
    )

    await _deliver_due(bot, session, fixed_now)

    refreshed = await _refresh_status(session, reminder.id)
    assert refreshed.status == "scheduled"
    assert refreshed.attempt_count == 1
    # Первая неудача → отсрочка 30 секунд от now.
    expected_next = (fixed_now + timedelta(seconds=30)).isoformat()
    assert refreshed.next_attempt_at_utc == expected_next
    assert refreshed.locked_at_utc is None


async def test_worker_cancelled_not_sent(
    bot: AsyncMock,
    session: AsyncSession,
    fixed_now,
) -> None:
    """Отменённое напоминание не отправляется: find_due фильтрует по scheduled."""
    await _make_user(session)

    remind_at = fixed_now - timedelta(minutes=1)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=_USER_ID,
        text="Позвонить",
        remind_at_utc=remind_at,
        now=fixed_now,
    )
    reminder.status = "cancelled"
    await session.commit()

    await _deliver_due(bot, session, fixed_now)

    bot.send_message.assert_not_awaited()

    refreshed = await _refresh_status(session, reminder.id)
    assert refreshed.status == "cancelled"


async def test_worker_recovery_at_startup(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Зависшая sending (>60 c) возвращается в scheduled до основного цикла."""
    await _make_user(session)

    stuck = Reminder(
        user_id=_USER_ID,
        text="Позвонить",
        remind_at_utc=(fixed_now - timedelta(hours=1)).isoformat(),
        status="sending",
        attempt_count=1,
        next_attempt_at_utc=(fixed_now - timedelta(hours=1)).isoformat(),
        locked_at_utc=(fixed_now - timedelta(seconds=120)).isoformat(),
        created_at_utc=(fixed_now - timedelta(hours=2)).isoformat(),
    )
    session.add(stuck)
    await session.commit()

    recovered = await _run_recovery(_factory(session), fixed_now)

    assert recovered == 1
    refreshed = await _refresh_status(session, stuck.id)
    assert refreshed.status == "scheduled"
    assert refreshed.locked_at_utc is None
    # next_attempt не пересчитывается: запись остаётся due и доставится сразу.
    assert refreshed.next_attempt_at_utc == stuck.next_attempt_at_utc


async def test_worker_one_session_per_iteration(
    bot: AsyncMock,
    session: AsyncSession,
    fixed_now,
) -> None:
    """Один тик цикла открывает ровно одну сессию через session_factory."""
    factory = _counting_factory(session)

    await _run_iteration(bot, factory, fixed_now)

    assert factory.entries == 1
    # Пустая БД: due нет, отправки не было, но сессия открывалась именно один раз.
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "side_effect, expected_status",
    [
        (None, "sent"),
        (
            TelegramForbiddenError(
                method="sendMessage",
                message="bot was blocked by the user",
            ),
            "failed",
        ),
        (
            TelegramAPIError(method="sendMessage", message="timeout"),
            "scheduled",
        ),
    ],
    ids=["success", "forbidden", "transient"],
)
async def test_worker_delivery_outcomes_parametrized(
    bot: AsyncMock,
    session: AsyncSession,
    fixed_now,
    side_effect,
    expected_status,
) -> None:
    """Сводная параметризация трёх исходов доставки одной итерацией."""
    await _make_user(session)

    remind_at = fixed_now - timedelta(minutes=1)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=_USER_ID,
        text="Позвонить",
        remind_at_utc=remind_at,
        now=fixed_now,
    )
    if side_effect is not None:
        bot.send_message.side_effect = side_effect

    await _deliver_due(bot, session, fixed_now)

    refreshed = await _refresh_status(session, reminder.id)
    assert refreshed.status == expected_status


# --- fixture helpers ---


class _SharedSessionCtx:
    """Контекстный менеджер над общей сессией без её закрытия.

    Жизненным циклом сессии владеет тестовая фикстура ``session``; этот контекст
    лишь имитирует ``async with session_factory()`` для worker-функций.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _factory(session: AsyncSession):
    """Возвращает callable-фабрику сессий, отдающую общую тестовую сессию."""

    def _make() -> _SharedSessionCtx:
        return _SharedSessionCtx(session)

    return _make


class _CountingFactory:
    """Фабрика сессий, считающая число открытий итерации."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.entries = 0

    def __call__(self) -> _SharedSessionCtx:
        self.entries += 1
        return _SharedSessionCtx(self._session)


def _counting_factory(session: AsyncSession) -> _CountingFactory:
    return _CountingFactory(session)
