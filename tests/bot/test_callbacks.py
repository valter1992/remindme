"""Тесты клетки bot: единый callback-роутер (``callbacks.py``).

Контракт-тесты проверяют форму фасада (``handle_callback``) и сигнатуру
``handle_callback(callback: CallbackQuery)``. Logic-тесты — разбор payload
``action:entity:id``, проверку владельца ``(record_id, user_id)`` на стороне
репозиториев/сценариев (чужие записи не раскрываются единым ответом), перерисовку
сообщения-списка через ``callback.message.edit_text`` по статусу записи и ветви
``complete:todo``/``delete:note``/``delete:todo``/``delete:reminder``. Побочные
эффекты проверяются через общую тестовую сессию фикстуры ``session``.
"""

import inspect
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from remindme import bot as bot_facade
from remindme.bot import ensure_user, handle_callback
from remindme.bot import handlers as handlers_module
from remindme.bot.callbacks import handle_callback as callbacks_handle_callback
from remindme.db import (
    Note,
    Reminder,
    Todo,
    claim_for_sending,
    complete_todo,
    create_note,
    create_reminder,
    create_todo,
)

_MOSCOW = "Europe/Moscow"


# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_handle_callback() -> None:
    """Фасад экспортирует ``handle_callback`` через ``__all__``."""
    assert "handle_callback" in bot_facade.__all__
    assert bot_facade.handle_callback is not None


def test_handle_callback_is_coroutine_taking_callback() -> None:
    """``handle_callback`` — async-функция, принимающая единственный ``callback``."""
    assert inspect.iscoroutinefunction(handle_callback)
    code = handle_callback.__code__
    assert code.co_varnames[: code.co_argcount] == ("callback",)


def test_handle_callback_defined_in_callbacks_module() -> None:
    """Реализация живёт в ``callbacks.py`` (не в ``handlers.py``)."""
    assert handle_callback is callbacks_handle_callback
    assert handle_callback.__module__ == "remindme.bot.callbacks"


# --- Helpers: mock CallbackQuery ---


def _callback(data: str, user_id: int = 123) -> AsyncMock:
    """Собирает mock aiogram ``CallbackQuery``.

    Args:
        data: payload ``callback_data`` (``action:entity:id``).
        user_id: Telegram id кликнувшего (``callback.from_user.id``).

    Returns:
        ``AsyncMock`` с ``data``, ``from_user``, ``answer`` и
        ``message.edit_text``.
    """
    callback = AsyncMock()
    callback.data = data
    callback.from_user = SimpleNamespace(id=user_id)
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()
    return callback


# --- Logic-тесты: complete:todo ---


async def test_complete_todo_callback(session, fixed_now, session_factory):  # noqa: ANN001
    """``complete:todo`` завершает задачу, отвечает и перерисовывает список."""
    await ensure_user(
        session=session,
        telegram_user_id=123,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    todo = await create_todo(
        session=session,
        telegram_user_id=123,
        text="Полить цветы",
        due_at_utc=None,
        now=fixed_now,
    )

    callback = _callback(f"complete:todo:{todo.id}", user_id=123)
    await handle_callback(callback)

    todo_db = (
        await session.execute(select(Todo).where(Todo.id == todo.id))
    ).scalar_one()
    assert todo_db.status == "completed"
    assert todo_db.completed_at_utc is not None

    assert callback.answer.await_count == 1
    assert callback.message.edit_text.await_count == 1
    redrawn = callback.message.edit_text.await_args.args[0]
    assert "Полить цветы" not in redrawn  # выполненная задача больше не в активных


async def test_complete_todo_foreign_returns_stale(session, fixed_now, session_factory):  # noqa: ANN001
    """Чужая задача при ``complete:todo`` → единый ответ, запись не изменена."""
    await ensure_user(
        session=session,
        telegram_user_id=123,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    todo = await create_todo(
        session=session,
        telegram_user_id=123,
        text="Чужая",
        due_at_utc=None,
        now=fixed_now,
    )

    callback = _callback(f"complete:todo:{todo.id}", user_id=999)
    await handle_callback(callback)

    assert callback.answer.await_count == 1
    assert "изменена или удалена" in callback.answer.await_args.args[0]
    assert callback.message.edit_text.await_count == 0
    todo_db = (
        await session.execute(select(Todo).where(Todo.id == todo.id))
    ).scalar_one()
    assert todo_db.status == "active"  # не выполнена чужим пользователем


# --- Logic-тесты: некорректный payload без обращения к БД ---


class _NeverOpened:
    """Контекстный менеджер сессии, падающий при открытии (БД не должна трогаться)."""

    async def __aenter__(self) -> None:
        raise AssertionError("session must not be opened for malformed payload")

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


@pytest.mark.parametrize(
    "data",
    [
        "delete:reminder",  # 2 части
        "delete:reminder:15:extra",  # 4 части
        "delete:reminder:abc",  # нечисловой id
        "complete:todo:-5",  # нечисловой id (минус)
        "",  # пустой payload
        "::",  # 3 пустые части, нечисловой id
    ],
)
async def test_callback_malformed_payload_no_db(data, monkeypatch) -> None:  # noqa: ANN001
    """Некорректный payload → ``callback.answer`` без открытия сессии (без БД)."""
    monkeypatch.setattr(handlers_module, "_session_factory", lambda: _NeverOpened())

    callback = _callback(data, user_id=123)
    await handle_callback(callback)

    assert callback.answer.await_count == 1
    assert callback.answer.await_args.args[0] == "Запись уже изменена или удалена."
    assert callback.message.edit_text.await_count == 0


# --- Logic-тесты: delete:note ---


async def test_delete_note_callback_redraw(session, fixed_now, session_factory):  # noqa: ANN001
    """``delete:note`` удаляет заметку и перерисовывает список заметок."""
    await ensure_user(
        session=session,
        telegram_user_id=123,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    note = await create_note(
        session=session,
        telegram_user_id=123,
        text="Заметка",
        now=fixed_now,
    )

    callback = _callback(f"delete:note:{note.id}", user_id=123)
    await handle_callback(callback)

    assert callback.answer.await_count == 1
    assert callback.message.edit_text.await_count == 1
    assert "Нет заметок" in callback.message.edit_text.await_args.args[0]

    notes = (
        (await session.execute(select(Note).where(Note.user_id == 123))).scalars().all()
    )
    assert len(notes) == 0


# --- Logic-тесты: delete:todo (was completed → список completed) ---


async def test_delete_todo_was_completed_redraws_completed(
    session,
    fixed_now,
    session_factory,
):  # noqa: ANN001
    """``delete:todo`` выполненной задачи перерисовывает список completed (was True)."""
    await ensure_user(
        session=session,
        telegram_user_id=123,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    todo = await create_todo(
        session=session,
        telegram_user_id=123,
        text="Готовая задача",
        due_at_utc=None,
        now=fixed_now,
    )
    await complete_todo(
        session=session,
        todo_id=todo.id,
        user_id=123,
        now=fixed_now,
    )

    callback = _callback(f"delete:todo:{todo.id}", user_id=123)
    await handle_callback(callback)

    assert callback.answer.await_count == 1
    assert callback.message.edit_text.await_count == 1
    # Перерисован список completed (теперь пуст после удаления) → «Нет выполненных».
    assert "Нет выполненных" in callback.message.edit_text.await_args.args[0]

    todos = (
        (await session.execute(select(Todo).where(Todo.user_id == 123))).scalars().all()
    )
    assert len(todos) == 0


async def test_delete_todo_was_active_redraws_todos(
    session,
    fixed_now,
    session_factory,
):  # noqa: ANN001
    """``delete:todo`` активной задачи перерисовывает список todos (was False)."""
    await ensure_user(
        session=session,
        telegram_user_id=123,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    todo = await create_todo(
        session=session,
        telegram_user_id=123,
        text="Активная задача",
        due_at_utc=None,
        now=fixed_now,
    )

    callback = _callback(f"delete:todo:{todo.id}", user_id=123)
    await handle_callback(callback)

    assert callback.message.edit_text.await_count == 1
    # Перерисован список активных (теперь пуст) → «Нет задач».
    assert "Нет задач" in callback.message.edit_text.await_args.args[0]


async def test_delete_todo_not_found_returns_stale(session, fixed_now, session_factory):  # noqa: ANN001
    """``delete:todo`` отсутствующей/чужой задачи → единый ответ, без перерисовки."""
    await ensure_user(
        session=session,
        telegram_user_id=123,
        timezone=_MOSCOW,
        now=fixed_now,
    )

    callback = _callback("delete:todo:999999", user_id=123)
    await handle_callback(callback)

    assert callback.answer.await_count == 1
    assert "изменена или удалена" in callback.answer.await_args.args[0]
    assert callback.message.edit_text.await_count == 0


# --- Logic-тесты: delete:reminder ---


async def test_delete_reminder_already_sending(session, fixed_now, session_factory):  # noqa: ANN001
    """``delete:reminder`` уходящего напоминания → ответ этапа 3, без перерисовки."""
    await ensure_user(
        session=session,
        telegram_user_id=123,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    due = fixed_now + timedelta(hours=1)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=123,
        text="Уходит",
        remind_at_utc=due,
        now=fixed_now,
    )
    await claim_for_sending(
        session=session,
        reminder_id=reminder.id,
        now=fixed_now,
    )

    callback = _callback(f"delete:reminder:{reminder.id}", user_id=123)
    await handle_callback(callback)

    assert callback.answer.await_count == 1
    assert "отправляется" in callback.answer.await_args.args[0]
    assert callback.message.edit_text.await_count == 0
    reminder_db = (
        await session.execute(select(Reminder).where(Reminder.id == reminder.id))
    ).scalar_one()
    assert reminder_db.status == "sending"  # не отменено


async def test_delete_reminder_cancelled_redraws(session, fixed_now, session_factory):  # noqa: ANN001
    """``delete:reminder`` scheduled-напоминания отменяет и перерисовывает список."""
    await ensure_user(
        session=session,
        telegram_user_id=123,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    due = fixed_now + timedelta(hours=1)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=123,
        text="Отменю",
        remind_at_utc=due,
        now=fixed_now,
    )

    callback = _callback(f"delete:reminder:{reminder.id}", user_id=123)
    await handle_callback(callback)

    assert callback.answer.await_count == 1
    assert callback.message.edit_text.await_count == 1
    assert "Нет напоминаний" in callback.message.edit_text.await_args.args[0]
    reminder_db = (
        await session.execute(select(Reminder).where(Reminder.id == reminder.id))
    ).scalar_one()
    assert reminder_db.status == "cancelled"


async def test_callback_foreign_record_isolation(
    session,
    fixed_now,
    session_factory,
):  # noqa: ANN001
    """Чужое напоминание (user_id=999) не отменяется, не раскрывается, не изменяется."""
    await ensure_user(
        session=session,
        telegram_user_id=123,
        timezone=_MOSCOW,
        now=fixed_now,
    )
    due = fixed_now + timedelta(hours=1)
    reminder = await create_reminder(
        session=session,
        telegram_user_id=123,
        text="Чужое",
        remind_at_utc=due,
        now=fixed_now,
    )

    callback = _callback(f"delete:reminder:{reminder.id}", user_id=999)
    await handle_callback(callback)

    assert callback.answer.await_count == 1
    assert callback.answer.await_args.args[0] == "Запись уже изменена или удалена."
    assert callback.message.edit_text.await_count == 0
    reminder_db = (
        await session.execute(select(Reminder).where(Reminder.id == reminder.id))
    ).scalar_one()
    assert reminder_db.status == "scheduled"  # чужой callback не изменил запись
