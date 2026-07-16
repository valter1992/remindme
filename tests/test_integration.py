"""Сквозные интеграционные тесты — кросс-клеточные end-to-end сценарии.

Покрывают несколько клеток в одном потоке: bot→services→db (полный ``/remind``
round-trip и callback complete→redraw), worker→db+bot (доставка, эскалация
повторов до ``failed``, восстановление зависшей ``sending`` при рестарте),
ensure_user (идемпотентная регистрация и восстановление из реальной
``IntegrityError`` при конкурентной вставке). Внешние зависимости (Telegram API)
под mock; БД — через временную SQLite-сессию фикстуры ``session``.

Шаг одной итерации worker выделен отдельно (``_run_iteration``/``_run_recovery``),
поэтому детерминированные тесты не используют реальный ``asyncio.sleep``.
Реальный Telegram-смoke помечен ``skipif`` по наличию ``TELEGRAM_BOT_TOKEN`` и
пропускается по умолчанию.
"""

from __future__ import annotations

import os
import re
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from remindme.bot import cmd_remind, cmd_todo, ensure_user, handle_callback
from remindme.bot import handlers as handlers_module
from remindme.db import Reminder, Todo, User, create_reminder
from remindme.worker.notifications import _run_iteration, _run_recovery

_MOSCOW = "Europe/Moscow"
_USER_ID = 42


# ---------------------------------------------------------------------------
# Общая инфраструктура: shared-сессия для обработчиков и заглушка настроек.
# ---------------------------------------------------------------------------


class _SharedSessionCtx:
    """Контекстный менеджер, отдающий общую тестовую сессию без её закрытия.

    Жизненным циклом сессии владеет фикстура ``session``; контекст лишь имитирует
    ``async with session_factory()`` для обработчиков и worker-функций.
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


@pytest.fixture(autouse=True)
def _stub_settings(monkeypatch) -> None:
    """Подменяет ``get_settings`` в обработчиках тестовыми настройками без токена.

    Обработчики читают ``DEFAULT_TIMEZONE`` (регистрация пользователя) и
    ``DEFAULT_REMINDER_TIME`` (разбор «через N» без явного времени); реальная
    валидация настроек (с обязательным токеном) в интеграционных тестах не нужна.
    """
    monkeypatch.setattr(
        handlers_module,
        "get_settings",
        lambda: SimpleNamespace(
            DEFAULT_TIMEZONE=_MOSCOW,
            DEFAULT_REMINDER_TIME="09:00",
        ),
    )


@pytest.fixture
def shared_factory(session: AsyncSession):
    """Подменяет ``handlers._session_factory`` общей тестовой сессией.

    Побочные эффекты обработчиков (создание ``User``/``Reminder``/...) проверяются
    через ту же ``session``. После теста модульная переменная сбрасывается.
    """
    handlers_module._session_factory = _factory(session)
    yield session
    handlers_module._session_factory = None


def _private_message(text: str, user_id: int = 123) -> AsyncMock:
    """Собирает mock приватного сообщения aiogram.

    Args:
        text: ``message.text`` (команда или фраза).
        user_id: Telegram id отправителя (``message.from_user.id``).

    Returns:
        ``AsyncMock`` с ``chat.type='private'``, ``from_user.id``, ``text`` и
        ``answer``.
    """
    message = AsyncMock()
    message.chat.type = "private"
    message.from_user.id = user_id
    message.text = text
    message.answer = AsyncMock()
    return message


def _callback(data: str, user_id: int = 123) -> AsyncMock:
    """Собирает mock aiogram ``CallbackQuery``.

    Args:
        data: payload ``callback_data`` (``action:entity:id``).
        user_id: Telegram id кликнувшего (``callback.from_user.id``).

    Returns:
        ``AsyncMock`` с ``data``, ``from_user``, ``answer`` и ``message.edit_text``.
    """
    callback = AsyncMock()
    callback.data = data
    callback.from_user = SimpleNamespace(id=user_id)
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()
    return callback


async def _make_user(session: AsyncSession, telegram_user_id: int = _USER_ID) -> None:
    """Создаёт пользователя с заданным ``telegram_user_id`` (для FK напоминаний)."""
    session.add(
        User(
            telegram_user_id=telegram_user_id,
            timezone=_MOSCOW,
            created_at_utc="2026-06-24T12:00:00+00:00",
            updated_at_utc="2026-06-24T12:00:00+00:00",
        )
    )
    await session.commit()


async def _refresh_reminder(session: AsyncSession, reminder_id: int) -> Reminder:
    """Перечитывает напоминание из БД по id (обходит staleness identity-map).

    Args:
        session: открытая ``AsyncSession``.
        reminder_id: идентификатор напоминания.

    Returns:
        Свежий ``Reminder`` с актуальными значениями после UPDATE.
    """
    reminder = await session.get(Reminder, reminder_id)
    await session.refresh(reminder)
    return reminder


# ---------------------------------------------------------------------------
# 1. Полный /remind round-trip: Message → cmd_remind → scenario → БД → ответ.
# ---------------------------------------------------------------------------


async def test_remind_round_trip_creates_and_confirms(
    session: AsyncSession,
    shared_factory,  # noqa: ANN001
) -> None:
    """``/remind`` end-to-end: запись ``status='scheduled'`` и локальное время в ответе.

    Поток: приватное сообщение → ``cmd_remind`` → ``ensure_user`` →
    ``create_reminder_scenario`` → ``create_reminder`` (БД) →
    ``format_reminder_confirmation`` → ``message.answer``. Подтверждение содержит
    текст напоминания, локальное время формата ``DD.MM.YYYY в HH:MM`` и суффикс
    часового пояса ``(IANA)``.
    """
    message = _private_message("/remind через 10 минут | Купить продукты")
    await cmd_remind(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert isinstance(answered, str)
    assert "Напоминание создано" in answered
    assert "Купить продукты" in answered
    assert _MOSCOW in answered  # суффикс IANA в подтверждении
    assert re.search(r"\d{2}\.\d{2}\.\d{4} в \d{2}:\d{2}", answered)  # локальное время

    reminders = (
        (await session.execute(select(Reminder).where(Reminder.user_id == 123)))
        .scalars()
        .all()
    )
    assert len(reminders) == 1
    assert reminders[0].status == "scheduled"
    assert reminders[0].text == "Купить продукты"
    assert reminders[0].attempt_count == 0


# ---------------------------------------------------------------------------
# 2. Callback complete→redraw: /todo создаёт задачу, кнопка завершает и перерисовывает.
# ---------------------------------------------------------------------------


async def test_callback_complete_redraw(
    session: AsyncSession,
    shared_factory,  # noqa: ANN001
    fixed_now,  # noqa: ANN001
) -> None:
    """``complete:todo`` завершает задачу и перерисовывает список через ``edit_text``.

    Поток: ``/todo`` (bot→services→db) создаёт активную задачу → callback
    ``complete:todo:<id>`` (callbacks→repositories→formatter→keyboards) переводит её
    в ``completed`` и перерисовывает сообщение списка. ``completed_at_utc`` заполнен.
    """
    create_message = _private_message("/todo Полить цветы")
    await cmd_todo(create_message)

    todo = (await session.execute(select(Todo).where(Todo.user_id == 123))).scalar_one()
    assert todo.status == "active"

    callback = _callback(f"complete:todo:{todo.id}", user_id=123)
    await handle_callback(callback)

    todo_db = (
        await session.execute(select(Todo).where(Todo.id == todo.id))
    ).scalar_one()
    assert todo_db.status == "completed"
    assert todo_db.completed_at_utc is not None

    assert callback.message.edit_text.await_count == 1
    assert callback.answer.await_count == 1


# ---------------------------------------------------------------------------
# 3. Worker e2e: доставка просроченного напоминания одним тиком цикла.
# ---------------------------------------------------------------------------


async def test_worker_e2e_delivers_due_reminder(
    bot: AsyncMock,
    session: AsyncSession,
    fixed_now,  # noqa: ANN001
) -> None:
    """Просроченное scheduled-напоминание: тик → ``send_message`` → ``status='sent'``.

    Поток: ``find_due_reminders`` → ``claim_for_sending`` →
    ``format_notification`` → ``bot.send_message(chat_id=user_id)`` → ``mark_sent``.
    """
    await _make_user(session)
    remind_at = fixed_now - timedelta(minutes=1)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=_USER_ID,
        text="Позвонить",
        remind_at_utc=remind_at,
        now=fixed_now,
    )

    await _run_iteration(bot, _factory(session), fixed_now)

    bot.send_message.assert_awaited_once_with(
        chat_id=_USER_ID,
        text="Напоминание\nПозвонить",
    )
    refreshed = await _refresh_reminder(session, reminder.id)
    assert refreshed.status == "sent"
    assert refreshed.sent_at_utc is not None


# ---------------------------------------------------------------------------
# 4. Worker e2e: эскалация преходящих неудач до failed (4-я неудача).
# ---------------------------------------------------------------------------


async def test_worker_e2e_transient_escalation_to_failed(
    bot: AsyncMock,
    session: AsyncSession,
    fixed_now,  # noqa: ANN001
) -> None:
    """Префодящая ``TelegramAPIError``: попытки 1–3 → ``scheduled`` с растущим
    ``attempt_count``, 4-я неудача → ``status='failed'``.

    Каждый тик цикла берёт одну сессию (``_run_iteration``); между тиками ``now``
    сдвигается за порог отсрочки (30/120/600 c), чтобы напоминание снова стало due.
    """
    await _make_user(session)
    remind_at = fixed_now - timedelta(minutes=1)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=_USER_ID,
        text="Повтор",
        remind_at_utc=remind_at,
        now=fixed_now,
    )
    bot.send_message.side_effect = TelegramAPIError(
        method="sendMessage",
        message="timeout",
    )

    factory = _factory(session)
    now = fixed_now

    # Попытка 1: attempt_count 0 → 1, отсрочка +30 c.
    await _run_iteration(bot, factory, now)
    after_one = await _refresh_reminder(session, reminder.id)
    assert after_one.status == "scheduled"
    assert after_one.attempt_count == 1

    # Попытка 2: сдвиг за +30 c, attempt_count → 2.
    now = now + timedelta(seconds=31)
    await _run_iteration(bot, factory, now)
    after_two = await _refresh_reminder(session, reminder.id)
    assert after_two.status == "scheduled"
    assert after_two.attempt_count == 2

    # Попытка 3: сдвиг за +120 c, attempt_count → 3.
    now = now + timedelta(seconds=121)
    await _run_iteration(bot, factory, now)
    after_three = await _refresh_reminder(session, reminder.id)
    assert after_three.status == "scheduled"
    assert after_three.attempt_count == 3

    # Попытка 4: сдвиг за +600 c, 4-я неудача → failed (без нового повтора).
    now = now + timedelta(seconds=601)
    await _run_iteration(bot, factory, now)
    after_four = await _refresh_reminder(session, reminder.id)
    assert after_four.status == "failed"


# ---------------------------------------------------------------------------
# 5. Worker restart recovery: зависшая sending восстанавливается и доставляется.
# ---------------------------------------------------------------------------


async def test_worker_restart_recovery_then_delivery(
    bot: AsyncMock,
    session: AsyncSession,
    fixed_now,  # noqa: ANN001
) -> None:
    """Зависшая ``sending`` (>60 c) → recovery → доставка следующим тиком.

    Поток: ``_run_recovery`` (``recover_stuck_sending``) возвращает запись в
    ``scheduled`` при старте, затем обычный тик цикла её захватывает и отправляет.
    """
    await _make_user(session)
    stuck = Reminder(
        user_id=_USER_ID,
        text="Зависло",
        remind_at_utc=(fixed_now - timedelta(hours=1)).isoformat(),
        status="sending",
        attempt_count=1,
        next_attempt_at_utc=(fixed_now - timedelta(hours=1)).isoformat(),
        locked_at_utc=(fixed_now - timedelta(seconds=120)).isoformat(),
        created_at_utc=(fixed_now - timedelta(hours=2)).isoformat(),
    )
    session.add(stuck)
    await session.commit()

    factory = _factory(session)

    recovered = await _run_recovery(factory, fixed_now)
    assert recovered == 1

    before = await _refresh_reminder(session, stuck.id)
    assert before.status == "scheduled"
    assert before.locked_at_utc is None

    # Следующий тик доставляет восстановленную запись.
    await _run_iteration(bot, factory, fixed_now)
    bot.send_message.assert_awaited_once_with(
        chat_id=_USER_ID,
        text="Напоминание\nЗависло",
    )
    after = await _refresh_reminder(session, stuck.id)
    assert after.status == "sent"


# ---------------------------------------------------------------------------
# 6. ensure_user: идемпотентная регистрация (одна запись на реальной БД).
# ---------------------------------------------------------------------------


async def test_ensure_user_idempotent_single_record(
    session: AsyncSession,
    fixed_now,  # noqa: ANN001
) -> None:
    """Двойная регистрация того же ``telegram_user_id`` → одна запись, ``created_at``
    неизменен, ``updated_at`` обновлён (реальная БД).
    """
    later = fixed_now + timedelta(hours=1)

    await ensure_user(
        session=session,
        telegram_user_id=_USER_ID,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    await ensure_user(
        session=session,
        telegram_user_id=_USER_ID,
        timezone=_MOSCOW,
        now=later,
    )

    users = (
        (await session.execute(select(User).where(User.telegram_user_id == _USER_ID)))
        .scalars()
        .all()
    )
    assert len(users) == 1
    assert users[0].created_at_utc == fixed_now.isoformat()
    assert users[0].updated_at_utc == later.isoformat()


async def test_ensure_user_concurrent_registration_race(
    session: AsyncSession,
    fixed_now,  # noqa: ANN001
    monkeypatch,  # noqa: ANN001
) -> None:
    """Конкурентная регистрация: ``IntegrityError`` → rollback + повторный SELECT.

    Эмулируется окно гонки (первый ``SELECT`` ensure_user не видит уже
    зафиксированную конкурентную запись), после чего собственный ``INSERT``
    сессии поднимает настоящую ``IntegrityError`` (UNIQUE/PK). ensure_user
    откатывает транзакцию и повторным ``SELECT`` возвращает существующую
    запись — дубль не создаётся.
    """
    # Предварительно фиксируем «победителя гонки» (запись уже в БД) и отсоединяем
    # экземпляр от сессии, чтобы собственный INSERT ensure_user не конфликтовал
    # с identity-map, а упал на уровне БД.
    session.add(
        User(
            telegram_user_id=_USER_ID,
            timezone=_MOSCOW,
            created_at_utc=fixed_now.isoformat(),
            updated_at_utc=fixed_now.isoformat(),
        )
    )
    await session.commit()
    session.expunge_all()

    real_execute = session.execute
    state = {"first_select": True}

    async def _racing_execute(stmt, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if state["first_select"]:
            state["first_select"] = False
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result
        return await real_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(session, "execute", _racing_execute)

    user = await ensure_user(
        session=session,
        telegram_user_id=_USER_ID,
        timezone=_MOSCOW,
        now=fixed_now,
    )

    assert user.telegram_user_id == _USER_ID
    users = (
        (await real_execute(select(User).where(User.telegram_user_id == _USER_ID)))
        .scalars()
        .all()
    )
    assert len(users) == 1


# ---------------------------------------------------------------------------
# 7. (Опц.) e2e smoke реального Telegram — пропускать по умолчанию.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("TELEGRAM_BOT_TOKEN"),
    reason="требуется TELEGRAM_BOT_TOKEN (реальный e2e smoke Telegram)",
)
async def test_real_telegram_get_me_smoke() -> None:
    """Опциональный smoke реального Telegram: ``Bot.get_me`` возвращает бота.

    Пропускается без ``TELEGRAM_BOT_TOKEN`` в окружении. При наличии токена проверяет
    базовую связность с Telegram API (что конфигурация и клиент корректны).
    """
    from aiogram import Bot

    from remindme.config import get_settings

    token = get_settings().TELEGRAM_BOT_TOKEN.get_secret_value()
    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        assert me.is_bot
    finally:
        await bot.session.close()
