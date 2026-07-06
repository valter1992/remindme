"""Тесты клетки bot: обработчики этапа 1 (``handlers.py``).

Контракт-тесты проверяют форму фасада (``PrivateOnly``, ``ensure_user``,
``cmd_start``, ``cmd_help``, ``handle_unknown_command``, ``handle_text``,
``handle_group``), наследование ``PrivateOnly`` от ``BaseFilter`` и сигнатуру
``ensure_user(session, telegram_user_id, timezone, now)``. Logic-тесты — фильтр
приватных чатов, идемпотентность ``ensure_user`` (``created_at_utc`` неизменен,
``updated_at_utc`` обновлён; одна запись), восстановление из конкурентной
``IntegrityError`` повторным ``SELECT``, приветствие ``/start`` и отказ в группе.
Пишущие обработчики идут через замкнутую фабрику сессий (фикстура
``session_factory``).
"""

import inspect
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import BaseFilter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from remindme import bot as bot_facade
from remindme.bot import (
    PrivateOnly,
    cmd_help,
    cmd_remind,
    cmd_reminders,
    cmd_start,
    ensure_user,
    handle_group,
    handle_remind_phrase,
    handle_text,
    handle_unknown_command,
)
from remindme.db import Reminder, User

_MOSCOW = "Europe/Moscow"

_EXPECTED = {
    "PrivateOnly",
    "ensure_user",
    "cmd_start",
    "cmd_help",
    "handle_unknown_command",
    "handle_text",
    "handle_group",
}

_REMIND_EXPECTED = {"cmd_remind", "cmd_reminders", "handle_remind_phrase"}


# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_phase1_handlers() -> None:
    """Фасад экспортирует все 7 сущностей этапа 1 через ``__all__``."""
    assert _EXPECTED.issubset(set(bot_facade.__all__))
    for name in _EXPECTED:
        assert getattr(bot_facade, name) is not None


def test_private_only_is_basefilter() -> None:
    """``PrivateOnly`` — подкласс aiogram ``BaseFilter``."""
    assert issubclass(PrivateOnly, BaseFilter)


def test_ensure_user_signature() -> None:
    """``ensure_user(session, telegram_user_id, timezone, now)``."""
    code = ensure_user.__code__
    params = code.co_varnames[: code.co_argcount]
    assert params == ("session", "telegram_user_id", "timezone", "now")


@pytest.mark.parametrize(
    "fn",
    [cmd_start, cmd_help, handle_unknown_command, handle_text, handle_group],
)
def test_handler_is_coroutine_function(fn) -> None:
    """Все обработчики этапа 1 — async-функции, принимающие ``message``."""
    assert inspect.iscoroutinefunction(fn)
    code = fn.__code__
    assert code.co_varnames[: code.co_argcount] == ("message",)


# --- Logic-тесты: фильтр приватных чатов ---


async def test_private_only_private_true_group_false() -> None:
    """``PrivateOnly`` пропускает личные чаты и отклоняет группы/каналы."""
    filt = PrivateOnly()
    private = SimpleNamespace(chat=SimpleNamespace(type="private"))
    group = SimpleNamespace(chat=SimpleNamespace(type="group"))
    channel = SimpleNamespace(chat=SimpleNamespace(type="channel"))

    assert await filt(private) is True
    assert await filt(group) is False
    assert await filt(channel) is False


# --- Logic-тесты: идемпотентная регистрация пользователя ---


async def test_settings_idempotent_start(session, fixed_now):  # noqa: ANN001
    """Повторный ``ensure_user`` не создаёт дубль: обновлён только ``updated_at``."""
    later = fixed_now + timedelta(hours=1)

    await ensure_user(
        session=session,
        telegram_user_id=42,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    await ensure_user(
        session=session,
        telegram_user_id=42,
        timezone=_MOSCOW,
        now=later,
    )

    users = (
        (await session.execute(select(User).where(User.telegram_user_id == 42)))
        .scalars()
        .all()
    )
    assert len(users) == 1
    assert users[0].timezone == _MOSCOW
    assert users[0].created_at_utc == fixed_now.isoformat()
    assert users[0].updated_at_utc == later.isoformat()


async def test_ensure_user_concurrent_integrity_error_recovery(
    fixed_now,
):  # noqa: ANN001
    """Конкурентная регистрация: ``IntegrityError`` → rollback + повторный SELECT."""
    session = AsyncMock()
    existing = User(
        telegram_user_id=42,
        timezone=_MOSCOW,
        created_at_utc=fixed_now.isoformat(),
        updated_at_utc=fixed_now.isoformat(),
    )

    none_result = MagicMock()
    none_result.scalar_one_or_none.return_value = None
    found_result = MagicMock()
    found_result.scalar_one.return_value = existing

    session.execute = AsyncMock(side_effect=[none_result, found_result])
    session.commit = AsyncMock(
        side_effect=IntegrityError(
            "INSERT INTO users", {}, Exception("UNIQUE constraint failed")
        )
    )
    session.rollback = AsyncMock()
    session.add = MagicMock()

    user = await ensure_user(
        session=session,
        telegram_user_id=42,
        timezone=_MOSCOW,
        now=fixed_now,
    )

    assert user is existing
    session.add.assert_called_once()
    session.rollback.assert_awaited_once()
    assert session.execute.await_count == 2


# --- Logic-тесты: обработчики ---


def _private_message(text: str = "/start") -> AsyncMock:
    """Mock приватного сообщения aiogram."""
    message = AsyncMock()
    message.chat.type = "private"
    message.from_user.id = 123
    message.text = text
    message.answer = AsyncMock()
    return message


async def test_cmd_start_answers_greeting(session_factory) -> None:  # noqa: ANN001
    """``/start`` регистрирует пользователя и отвечает приветствием."""
    message = _private_message("/start")
    await cmd_start(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert isinstance(answered, str)
    assert "Привет" in answered


async def test_cmd_start_ensures_user(session, session_factory) -> None:  # noqa: ANN001
    """``/start`` создаёт запись ``User`` с зоной по умолчанию из настроек."""
    message = _private_message("/start")
    await cmd_start(message)

    user = (
        await session.execute(select(User).where(User.telegram_user_id == 123))
    ).scalar_one_or_none()
    assert user is not None
    assert user.telegram_user_id == 123


async def test_cmd_help_answers_help(session_factory) -> None:  # noqa: ANN001
    """``/help`` отвечает списком команд."""
    message = _private_message("/help")
    await cmd_help(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "/remind" in answered


async def test_handle_text_answers_hint(session_factory) -> None:  # noqa: ANN001
    """Свободный текст в личке → подсказка ``/help`` после регистрации."""
    message = _private_message("привет бот")
    await handle_text(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "/help" in answered


async def test_handle_unknown_command_answers_hint() -> None:
    """Неизвестная команда → подсказка ``/help`` без обращения к БД."""
    message = _private_message("/foobar")
    await handle_unknown_command(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "/help" in answered


async def test_handle_group_answers_unsupported() -> None:
    """Сообщение из группы → отказ без обращения к БД."""
    message = AsyncMock()
    message.chat.type = "group"
    message.from_user.id = 999
    message.text = "напомни мне"
    message.answer = AsyncMock()

    await handle_group(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "личных сообщениях" in answered


# ===========================================================================
# Task 19: remind flow — cmd_remind, cmd_reminders, handle_remind_phrase
# ===========================================================================


# --- Контракт-тесты: форма фасада и API remind-потока ---


def test_facade_exports_remind_handlers() -> None:
    """Фасад экспортирует 3 обработчика remind-потока через ``__all__``."""
    assert _REMIND_EXPECTED.issubset(set(bot_facade.__all__))
    for name in _REMIND_EXPECTED:
        assert getattr(bot_facade, name) is not None


@pytest.mark.parametrize("fn", [cmd_remind, cmd_reminders, handle_remind_phrase])
def test_remind_handler_is_coroutine_taking_message(fn) -> None:
    """Обработчики remind-потока — async-функции, принимающие ``message``."""
    assert inspect.iscoroutinefunction(fn)
    code = fn.__code__
    assert code.co_varnames[: code.co_argcount] == ("message",)


# --- Logic-тесты: создание напоминания ---


async def test_cmd_remind_creates_reminder_and_confirms(session, session_factory):  # noqa: ANN001
    """``/remind`` создаёт напоминание и подтверждает его локальным временем."""
    message = _private_message("/remind через 10 минут | Купить продукты")
    await cmd_remind(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "Напоминание создано" in answered
    assert "Купить продукты" in answered
    assert _MOSCOW in answered  # суффикс IANA в подтверждении

    reminders = (
        (await session.execute(select(Reminder).where(Reminder.user_id == 123)))
        .scalars()
        .all()
    )
    assert len(reminders) == 1
    assert reminders[0].status == "scheduled"
    assert reminders[0].text == "Купить продукты"


async def test_cmd_remind_parse_error_path(session, session_factory):  # noqa: ANN001
    """Нераспознанное ``/remind`` → текст ошибки парсера, запись не создаётся."""
    message = _private_message("/remind абракадабра | текст")
    await cmd_remind(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "Не удалось разобрать время" in answered

    reminders = (
        (await session.execute(select(Reminder).where(Reminder.user_id == 123)))
        .scalars()
        .all()
    )
    assert len(reminders) == 0


async def test_handle_remind_phrase_creates_reminder(session, session_factory):  # noqa: ANN001
    """Фраза «напомни ...» создаёт напоминание и подтверждает."""
    message = _private_message("напомни через 10 минут купить хлеб")
    await handle_remind_phrase(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "Напоминание создано" in answered
    assert "купить хлеб" in answered

    reminders = (
        (await session.execute(select(Reminder).where(Reminder.user_id == 123)))
        .scalars()
        .all()
    )
    assert len(reminders) == 1
    assert reminders[0].text == "купить хлеб"
    assert reminders[0].status == "scheduled"


# --- Logic-тесты: ограничение длины сообщения (1000/1001) ---


@pytest.mark.parametrize(("total", "blocked"), [(1000, False), (1001, True)])
async def test_message_length_boundary(total, blocked, session, session_factory):  # noqa: ANN001
    """1000 символов доходит до сценария; 1001 — блокируется лимитом сообщения.

    Сообщение ``/remind <время> | <длинный текст>``: при 1000 символах длина
    проходит гард сообщения, и сценарий разбирает текст (длиннее 500 →
    ``text_too_long`` парсера); при 1001 — гард сообщения срабатывает раньше
    сценария. В обоих случаях запись в БД не создаётся.
    """
    prefix = "/remind через 10 минут | "
    padding = "X" * (total - len(prefix))
    message = _private_message(prefix + padding)
    assert len(message.text) == total

    await cmd_remind(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    reminders = (
        (await session.execute(select(Reminder).where(Reminder.user_id == 123)))
        .scalars()
        .all()
    )
    assert len(reminders) == 0

    if blocked:
        # Гард сообщения: ответ о лимите 1000 символов.
        assert "1000 символов" in answered
        assert "500 символов" not in answered
    else:
        # Сценарий достигнут: парсер ответил своим лимитом 500 символов.
        assert "500 символов" in answered
        assert "1000 символов" not in answered


# --- Logic-тесты: список напоминаний с клавиатурой ---


async def test_cmd_reminders_lists_with_keyboard(session_factory):  # noqa: ANN001
    """``/reminders`` отвечает списком и прикрепляет inline-клавиатуру."""
    create = _private_message("/remind через 10 минут | Купить продукты")
    await cmd_remind(create)

    message = _private_message("/reminders")
    await cmd_reminders(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "Купить продукты" in answered

    reply_markup = message.answer.await_args.kwargs.get("reply_markup")
    assert reply_markup is not None
    rows = reply_markup.inline_keyboard
    assert len(rows) == 1
    callback_data = rows[0][0].callback_data
    assert callback_data.startswith("delete:reminder:")


async def test_cmd_reminders_empty_answers_no_keyboard(session_factory):  # noqa: ANN001
    """``/reminders`` без напоминаний отвечает текстом без клавиатуры."""
    message = _private_message("/reminders")
    await cmd_reminders(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "Нет напоминаний" in answered
    assert message.answer.await_args.kwargs.get("reply_markup") is None
