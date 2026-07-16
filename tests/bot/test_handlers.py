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
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from remindme import bot as bot_facade
from remindme.bot import (
    PrivateOnly,
    cmd_cancel,
    cmd_completed,
    cmd_help,
    cmd_note,
    cmd_notes,
    cmd_remind,
    cmd_reminders,
    cmd_start,
    cmd_timezone,
    cmd_todo,
    cmd_todos,
    ensure_user,
    handle_db_error,
    handle_group,
    handle_remind_phrase,
    handle_text,
    handle_unknown_command,
)
from remindme.db import (
    Note,
    Reminder,
    Todo,
    User,
    claim_for_sending,
    complete_todo,
    create_reminder,
)

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

_PHASE3_EXPECTED = {
    "cmd_timezone",
    "cmd_cancel",
    "cmd_note",
    "cmd_notes",
    "cmd_todo",
    "cmd_todos",
    "cmd_completed",
    "handle_db_error",
}


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


# ===========================================================================
# Task 20: phase 3/4 commands + handle_db_error
# ===========================================================================


# --- Контракт-тесты: форма фасада и API этапа 3/4 ---


def test_facade_exports_phase3_handlers() -> None:
    """Фасад экспортирует 7 обработчиков этапа 3/4 через ``__all__``."""
    assert _PHASE3_EXPECTED.issubset(set(bot_facade.__all__))
    for name in _PHASE3_EXPECTED:
        assert getattr(bot_facade, name) is not None


@pytest.mark.parametrize(
    "fn",
    [cmd_timezone, cmd_cancel, cmd_note, cmd_notes, cmd_todo, cmd_todos, cmd_completed],
)
def test_phase3_handler_is_coroutine_taking_message(fn) -> None:
    """Командные обработчики этапа 3/4 — async-функции, принимающие ``message``."""
    assert inspect.iscoroutinefunction(fn)
    code = fn.__code__
    assert code.co_varnames[: code.co_argcount] == ("message",)


def test_handle_db_error_signature() -> None:
    """``handle_db_error(event: ErrorEvent)`` принимает один аргумент ``event``."""
    assert inspect.iscoroutinefunction(handle_db_error)
    code = handle_db_error.__code__
    assert code.co_varnames[: code.co_argcount] == ("event",)


# --- Logic-тесты: смена часового пояса ---


async def test_cmd_timezone_ok_and_unknown(session, session_factory):  # noqa: ANN001
    """``/timezone Europe/Moscow`` обновляет зону; неизвестная IANA — отказ."""
    ok = _private_message("/timezone Europe/Kirov")
    await cmd_timezone(ok)

    assert ok.answer.await_count == 1
    assert "изменён" in ok.answer.await_args.args[0]
    assert "Europe/Kirov" in ok.answer.await_args.args[0]
    user = (
        await session.execute(select(User).where(User.telegram_user_id == 123))
    ).scalar_one()
    assert user.timezone == "Europe/Kirov"

    bad = _private_message("/timezone Not/AZone")
    await cmd_timezone(bad)
    assert "Неизвестный пояс" in bad.answer.await_args.args[0]


async def test_cmd_timezone_empty_answers_hint(session_factory):  # noqa: ANN001
    """``/timezone`` без аргумента → подсказка формата (без обращения к сценариям)."""
    message = _private_message("/timezone")
    await cmd_timezone(message)

    assert message.answer.await_count == 1
    assert "IANA" in message.answer.await_args.args[0]


# --- Logic-тесты: отмена напоминания (три исхода) ---


async def test_cmd_cancel_outcomes(session, fixed_now, session_factory):  # noqa: ANN001
    """``/cancel`` различает ``cancelled``, ``already_sending`` и ``not_found``."""
    await ensure_user(
        session=session,
        telegram_user_id=123,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    due = fixed_now + timedelta(hours=1)
    scheduled = await create_reminder(
        session=session,
        telegram_user_id=123,
        text="Сработает",
        remind_at_utc=due,
        now=fixed_now,
    )
    sending = await create_reminder(
        session=session,
        telegram_user_id=123,
        text="Уходит",
        remind_at_utc=due,
        now=fixed_now,
    )
    await claim_for_sending(
        session=session,
        reminder_id=sending.id,
        now=fixed_now,
    )

    cancelled_msg = _private_message(f"/cancel {scheduled.id}")
    await cmd_cancel(cancelled_msg)
    assert "отменено" in cancelled_msg.answer.await_args.args[0].lower()
    # в БД статус перешёл в cancelled
    cancelled_db = (
        await session.execute(select(Reminder).where(Reminder.id == scheduled.id))
    ).scalar_one()
    assert cancelled_db.status == "cancelled"

    sending_msg = _private_message(f"/cancel {sending.id}")
    await cmd_cancel(sending_msg)
    assert "отправляется" in sending_msg.answer.await_args.args[0]

    not_found_msg = _private_message("/cancel 999999")
    await cmd_cancel(not_found_msg)
    assert "не найдено" in not_found_msg.answer.await_args.args[0].lower()


async def test_cmd_cancel_non_numeric_answers_hint(session_factory):  # noqa: ANN001
    """Нечисловой id → подсказка, без обращения к сценарию."""
    message = _private_message("/cancel abc")
    await cmd_cancel(message)

    assert message.answer.await_count == 1
    assert "id" in message.answer.await_args.args[0]


# --- Logic-тесты: заметки ---


async def test_cmd_note_success_and_error(session, session_factory):  # noqa: ANN001
    """``/note`` создаёт заметку; пустой текст → ошибка валидации без сохранения."""
    ok = _private_message("/note Купить молоко")
    await cmd_note(ok)
    assert ok.answer.await_count == 1
    assert "Заметка создана" in ok.answer.await_args.args[0]
    assert "Купить молоко" in ok.answer.await_args.args[0]
    notes = (
        (await session.execute(select(Note).where(Note.user_id == 123))).scalars().all()
    )
    assert len(notes) == 1
    assert notes[0].text == "Купить молоко"

    empty = _private_message("/note    ")
    await cmd_note(empty)
    assert "пуст" in empty.answer.await_args.args[0]
    notes_after = (
        (await session.execute(select(Note).where(Note.user_id == 123))).scalars().all()
    )
    assert len(notes_after) == 1  # вторая запись не создана


async def test_cmd_notes_list_and_keyboard(session_factory):  # noqa: ANN001
    """``/notes`` отвечает списком и прикрепляет клавиатуру удаления."""
    create = _private_message("/note Первая заметка")
    await cmd_note(create)

    message = _private_message("/notes")
    await cmd_notes(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "Первая заметка" in answered

    reply_markup = message.answer.await_args.kwargs.get("reply_markup")
    assert reply_markup is not None
    rows = reply_markup.inline_keyboard
    assert len(rows) == 1
    assert rows[0][0].callback_data.startswith("delete:note:")


async def test_cmd_notes_empty_answers_no_keyboard(session_factory):  # noqa: ANN001
    """``/notes`` без заметок отвечает текстом без клавиатуры."""
    message = _private_message("/notes")
    await cmd_notes(message)

    assert message.answer.await_count == 1
    assert "Нет заметок" in message.answer.await_args.args[0]
    assert message.answer.await_args.kwargs.get("reply_markup") is None


# --- Logic-тесты: задачи ---


async def test_cmd_todo_success_and_error(session, session_factory):  # noqa: ANN001
    """``/todo`` создаёт задачу со сроком и без; пустой текст → ошибка валидации."""
    with_due = _private_message("/todo 2026-06-25 09:00 | Полить цветы")
    await cmd_todo(with_due)
    assert with_due.answer.await_count == 1
    assert "Задача создана" in with_due.answer.await_args.args[0]
    assert "Полить цветы" in with_due.answer.await_args.args[0]
    todos = (
        (await session.execute(select(Todo).where(Todo.user_id == 123))).scalars().all()
    )
    assert len(todos) == 1
    assert todos[0].due_at_utc is not None

    without_due = _private_message("/todo Просто задача")
    await cmd_todo(without_due)
    assert "без срока" in without_due.answer.await_args.args[0]

    empty = _private_message("/todo   ")
    await cmd_todo(empty)
    assert "пуст" in empty.answer.await_args.args[0]
    todos_after = (
        (await session.execute(select(Todo).where(Todo.user_id == 123))).scalars().all()
    )
    assert len(todos_after) == 2  # ошибочная запись не создана


async def test_cmd_todos_nulls_last(session_factory):  # noqa: ANN001
    """``/todos`` выводит задачи со сроком раньше задач без срока (NULLS LAST)."""
    # Сначала создаём задачу без срока, затем со сроком — порядок вывода
    # должен остаться «со сроком, затем без срока» (NULLS LAST в репозитории).
    await cmd_todo(_private_message("/todo Без срока задача"))
    await cmd_todo(_private_message("/todo 2026-06-25 09:00 | Со сроком задача"))

    message = _private_message("/todos")
    await cmd_todos(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert answered.index("Со сроком задача") < answered.index("Без срока задача")

    reply_markup = message.answer.await_args.kwargs.get("reply_markup")
    assert reply_markup is not None
    rows = reply_markup.inline_keyboard
    # У активной задачи ряд из двух кнопок: complete + delete.
    assert len(rows[0]) == 2
    actions = {btn.callback_data.split(":")[0] for btn in rows[0]}
    assert actions == {"complete", "delete"}


# --- Logic-тесты: выполненные задачи ---


async def test_cmd_completed_list(session, session_factory, fixed_now):  # noqa: ANN001
    """``/completed`` выводит выполненные задачи с клавиатурой удаления."""
    await cmd_todo(_private_message("/todo Готовая задача"))
    todos = (
        (await session.execute(select(Todo).where(Todo.user_id == 123))).scalars().all()
    )
    todo_id = todos[0].id
    # Переводим задачу в completed через callback-ветку репозитория.
    await complete_todo(
        session=session,
        todo_id=todo_id,
        user_id=123,
        now=fixed_now,
    )

    message = _private_message("/completed")
    await cmd_completed(message)

    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "Готовая задача" in answered

    reply_markup = message.answer.await_args.kwargs.get("reply_markup")
    assert reply_markup is not None
    rows = reply_markup.inline_keyboard
    assert rows[0][0].callback_data.startswith("delete:todo:")


async def test_cmd_completed_empty_answers_no_keyboard(session_factory):  # noqa: ANN001
    """``/completed`` без выполненных задач отвечает без клавиатуры."""
    message = _private_message("/completed")
    await cmd_completed(message)

    assert message.answer.await_count == 1
    assert "Нет выполненных" in message.answer.await_args.args[0]
    assert message.answer.await_args.kwargs.get("reply_markup") is None


# --- Logic-тесты: централизованный errors-handler ---


def _error_event(exception: BaseException, *, text: str = "текст") -> SimpleNamespace:
    """Собирает mock ``ErrorEvent`` с приватным сообщением, содержащим секрет.

    Args:
        exception: перехваченное исключение (``event.exception``).
        text: текст пользовательского сообщения (содержит «секрет» для проверки
            отсутствия утечки в лог).

    Returns:
        ``SimpleNamespace`` с атрибутами ``exception`` и ``update``.
    """
    message = AsyncMock()
    message.from_user = SimpleNamespace(id=123)
    message.text = text
    message.answer = AsyncMock()
    return SimpleNamespace(
        exception=exception,
        update=SimpleNamespace(message=message, callback_query=None),
    )


async def test_handle_db_error_sqlalchemy_answers_unified_text() -> None:
    """``SQLAlchemyError`` → единый текст-ответ пользователю."""
    event = _error_event(SQLAlchemyError("INSERT INTO reminders ..."))
    await handle_db_error(event)

    message = event.update.message
    assert message.answer.await_count == 1
    answered = message.answer.await_args.args[0]
    assert "Не удалось выполнить действие" in answered


async def test_handle_db_error_non_sqlalchemy_passthrough() -> None:
    """Иное исключение пропускается: ответ не отправляется."""
    event = _error_event(ValueError("не БД"))
    result = await handle_db_error(event)

    assert result is None
    assert event.update.message.answer.await_count == 0


async def test_handle_db_error_no_token_in_log(caplog) -> None:  # noqa: ANN001
    """Лог errors-handler не содержит токена и текста записи/сообщения.

    В исключение и текст пользовательского сообщения встроен «секрет» — он не
    должен попасть в лог: логируется только user_id и имя класса исключения.
    """
    secret = "SUPERSECRET-TOKEN-VALUE"
    event = _error_event(
        SQLAlchemyError(f"UPDATE ... text='{secret}'"),
        text=secret,
    )

    with caplog.at_level("ERROR", logger="remindme.bot.handlers"):
        await handle_db_error(event)

    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert secret not in logged
    for rec in caplog.records:
        assert secret not in str(rec.__dict__)
    # user_id и код ошибки всё же присутствуют (как extra), без текста записи.
    rec = caplog.records[-1]
    assert rec.__dict__.get("user_id") == 123
    assert rec.__dict__.get("error_code") == "SQLAlchemyError"
