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
    Note,
    Reminder,
    Todo,
    User,
    cancel_reminder,
    complete_todo,
    create_note,
    create_reminder,
    create_todo,
    delete_note,
    delete_todo,
    list_completed,
    list_notes,
    list_reminders,
    list_todos,
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


# --- Контракт-тесты: notes/todos (Task 8) ---


def test_facade_exports_note_todo_repositories() -> None:
    """Фасад экспортирует восемь сущностей группы notes+todos."""
    assert callable(create_note)
    assert callable(list_notes)
    assert callable(delete_note)
    assert callable(create_todo)
    assert callable(list_todos)
    assert callable(list_completed)
    assert callable(complete_todo)
    assert callable(delete_todo)


def test_create_note_signature() -> None:
    """``create_note(session, telegram_user_id, text, now)``."""
    params = create_note.__code__.co_varnames[: create_note.__code__.co_argcount]
    assert params == ("session", "telegram_user_id", "text", "now")


def test_list_notes_signature() -> None:
    """``list_notes(session, user_id)``."""
    params = list_notes.__code__.co_varnames[: list_notes.__code__.co_argcount]
    assert params == ("session", "user_id")


def test_delete_note_signature() -> None:
    """``delete_note(session, note_id, user_id)``."""
    params = delete_note.__code__.co_varnames[: delete_note.__code__.co_argcount]
    assert params == ("session", "note_id", "user_id")


def test_create_todo_signature() -> None:
    """``create_todo(session, telegram_user_id, text, due_at_utc, now)``."""
    params = create_todo.__code__.co_varnames[: create_todo.__code__.co_argcount]
    assert params == (
        "session",
        "telegram_user_id",
        "text",
        "due_at_utc",
        "now",
    )


def test_list_todos_signature() -> None:
    """``list_todos(session, user_id)``."""
    params = list_todos.__code__.co_varnames[: list_todos.__code__.co_argcount]
    assert params == ("session", "user_id")


def test_list_completed_signature() -> None:
    """``list_completed(session, user_id)``."""
    params = list_completed.__code__.co_varnames[: list_completed.__code__.co_argcount]
    assert params == ("session", "user_id")


def test_complete_todo_signature() -> None:
    """``complete_todo(session, todo_id, user_id, now)``."""
    params = complete_todo.__code__.co_varnames[: complete_todo.__code__.co_argcount]
    assert params == ("session", "todo_id", "user_id", "now")


def test_delete_todo_signature() -> None:
    """``delete_todo(session, todo_id, user_id) -> bool | None``."""
    params = delete_todo.__code__.co_varnames[: delete_todo.__code__.co_argcount]
    assert params == ("session", "todo_id", "user_id")


# --- Logic-тесты: notes/todos ---


async def test_create_note_and_list_desc(session: AsyncSession, fixed_now) -> None:
    """create_note + list_notes: заметки возвращаются новые сверху (desc)."""
    await _make_user(session, telegram_user_id=42)

    first = await create_note(
        session=session, telegram_user_id=42, text="Первая", now=fixed_now
    )
    second = await create_note(
        session=session,
        telegram_user_id=42,
        text="Вторая",
        now=fixed_now + timedelta(minutes=1),
    )

    notes = await list_notes(session=session, user_id=42)

    assert [n.id for n in notes] == [second.id, first.id]


async def test_list_notes_isolates_by_user(session: AsyncSession, fixed_now) -> None:
    """list_notes возвращает только заметки текущего пользователя."""
    await _make_user(session, telegram_user_id=42)
    await _make_user(session, telegram_user_id=43)

    await create_note(session=session, telegram_user_id=42, text="42", now=fixed_now)
    await create_note(session=session, telegram_user_id=43, text="43", now=fixed_now)

    notes = await list_notes(session=session, user_id=42)

    assert len(notes) == 1
    assert notes[0].text == "42"


async def test_delete_note_foreign_returns_false(
    session: AsyncSession, fixed_now
) -> None:
    """delete_note: чужая запись → False (изоляция владельца); своя → True."""
    await _make_user(session, telegram_user_id=42)

    note = await create_note(
        session=session, telegram_user_id=42, text="Заметка", now=fixed_now
    )

    assert await delete_note(session=session, note_id=note.id, user_id=999) is False
    assert await delete_note(session=session, note_id=note.id, user_id=42) is True
    assert await session.get(Note, note.id) is None

    # несуществующая запись → False
    assert await delete_note(session=session, note_id=12345, user_id=42) is False


async def test_create_todo_active_with_and_without_due(
    session: AsyncSession, fixed_now
) -> None:
    """create_todo: status='active'; due ISO | None; past допускается."""
    await _make_user(session, telegram_user_id=42)

    due = fixed_now - timedelta(days=1)  # past допускается по контракту
    with_due = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Со сроком в прошлом",
        due_at_utc=due,
        now=fixed_now,
    )
    without_due = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Без срока",
        due_at_utc=None,
        now=fixed_now,
    )

    assert with_due.id is not None
    assert with_due.user_id == 42
    assert with_due.status == "active"
    assert with_due.due_at_utc == due.isoformat()
    assert with_due.completed_at_utc is None
    assert with_due.created_at_utc == fixed_now.isoformat()

    assert without_due.status == "active"
    assert without_due.due_at_utc is None
    assert without_due.completed_at_utc is None


async def test_list_todos_nulls_last(session: AsyncSession, fixed_now) -> None:
    """list_todos: сначала со сроком (asc), затем без срока (NULLS LAST)."""
    await _make_user(session, telegram_user_id=42)

    # без срока — создаётся раньше остальных, чтобы убедиться, что не всплывает наверх
    no_due_first = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Без срока (раньше)",
        due_at_utc=None,
        now=fixed_now,
    )
    later = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Срок позже",
        due_at_utc=fixed_now + timedelta(hours=10),
        now=fixed_now,
    )
    sooner = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Срок раньше",
        due_at_utc=fixed_now + timedelta(hours=2),
        now=fixed_now,
    )
    # ещё один без срока, созданный позже — при равенстве срока (NULL) порядок по
    # created_at_utc desc: «позже» перед «раньше»
    no_due_second = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Без срока (позже)",
        due_at_utc=None,
        now=fixed_now + timedelta(minutes=1),
    )

    todos = await list_todos(session=session, user_id=42)

    assert len(todos) == 4
    # со сроком asc идут первыми
    assert todos[0].id == sooner.id
    assert todos[1].id == later.id
    # затем без срока (NULLS LAST), внутри — created_at_utc desc
    assert todos[2].id == no_due_second.id
    assert todos[3].id == no_due_first.id


async def test_list_todos_isolates_by_user(session: AsyncSession, fixed_now) -> None:
    """list_todos возвращает только активные задачи текущего пользователя."""
    await _make_user(session, telegram_user_id=42)
    await _make_user(session, telegram_user_id=43)

    await create_todo(
        session=session,
        telegram_user_id=42,
        text="42",
        due_at_utc=None,
        now=fixed_now,
    )
    await create_todo(
        session=session,
        telegram_user_id=43,
        text="43",
        due_at_utc=None,
        now=fixed_now,
    )

    todos = await list_todos(session=session, user_id=42)

    assert len(todos) == 1
    assert todos[0].text == "42"


async def test_list_todos_excludes_completed(session: AsyncSession, fixed_now) -> None:
    """list_todos не возвращает completed-задачи."""
    await _make_user(session, telegram_user_id=42)

    active = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Активная",
        due_at_utc=None,
        now=fixed_now,
    )
    done = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Готовая",
        due_at_utc=None,
        now=fixed_now,
    )
    done.status = "completed"
    done.completed_at_utc = fixed_now.isoformat()
    await session.commit()

    todos = await list_todos(session=session, user_id=42)

    assert {t.id for t in todos} == {active.id}


async def test_list_completed_desc(session: AsyncSession, fixed_now) -> None:
    """list_completed: status='completed', order completed_at_utc desc."""
    await _make_user(session, telegram_user_id=42)

    t1 = await create_todo(
        session=session,
        telegram_user_id=42,
        text="1",
        due_at_utc=None,
        now=fixed_now,
    )
    t2 = await create_todo(
        session=session,
        telegram_user_id=42,
        text="2",
        due_at_utc=None,
        now=fixed_now,
    )

    await complete_todo(
        session=session,
        todo_id=t1.id,
        user_id=42,
        now=fixed_now + timedelta(minutes=1),
    )
    await complete_todo(
        session=session,
        todo_id=t2.id,
        user_id=42,
        now=fixed_now + timedelta(minutes=2),
    )

    completed = await list_completed(session=session, user_id=42)

    assert [t.id for t in completed] == [t2.id, t1.id]
    assert all(t.status == "completed" for t in completed)
    assert all(t.completed_at_utc is not None for t in completed)


async def test_complete_todo_active_to_completed_and_repeat_returns_false(
    session: AsyncSession,
    fixed_now,
) -> None:
    """complete_todo: active→completed (+completed_at); повтор → False."""
    await _make_user(session, telegram_user_id=42)

    todo = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Сделать",
        due_at_utc=None,
        now=fixed_now,
    )

    first = await complete_todo(
        session=session, todo_id=todo.id, user_id=42, now=fixed_now
    )
    assert first is True

    # Bulk-UPDATE синхронизирует identity-map не по всем колонкам — перечитываем
    # запись из БД, чтобы проверить записанные значения.
    reloaded = await session.get(Todo, todo.id)
    assert reloaded is not None
    await session.refresh(reloaded)
    assert reloaded.status == "completed"
    assert reloaded.completed_at_utc == fixed_now.isoformat()

    # повторный клик по уже completed → False (rowcount == 0)
    second = await complete_todo(
        session=session, todo_id=todo.id, user_id=42, now=fixed_now
    )
    assert second is False


async def test_complete_todo_foreign_returns_false(
    session: AsyncSession, fixed_now
) -> None:
    """complete_todo: чужая запись → False (изоляция владельца)."""
    await _make_user(session, telegram_user_id=42)

    todo = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Чужая",
        due_at_utc=None,
        now=fixed_now,
    )

    assert (
        await complete_todo(
            session=session, todo_id=todo.id, user_id=999, now=fixed_now
        )
        is False
    )
    reloaded = await session.get(Todo, todo.id)
    assert reloaded is not None
    assert reloaded.status == "active"


@pytest.mark.parametrize(
    ("owner", "status_before", "expected", "row_left_after"),
    [
        pytest.param(42, "active", False, False, id="own-active"),
        pytest.param(42, "completed", True, False, id="own-completed"),
        pytest.param(999, "active", None, True, id="foreign"),
    ],
)
async def test_delete_todo_was_completed_none_true_false(
    session: AsyncSession,
    fixed_now,
    owner: int,
    status_before: str,
    expected,
    row_left_after: bool,
) -> None:
    """delete_todo: чужая→None, completed→True, active→False (статус до удаления)."""
    await _make_user(session, telegram_user_id=42)

    todo = await create_todo(
        session=session,
        telegram_user_id=42,
        text="Задача",
        due_at_utc=None,
        now=fixed_now,
    )
    if status_before == "completed":
        todo.status = "completed"
        todo.completed_at_utc = fixed_now.isoformat()
        await session.commit()

    result = await delete_todo(session=session, todo_id=todo.id, user_id=owner)

    assert result is expected

    reloaded = await session.get(Todo, todo.id)
    if row_left_after:
        assert reloaded is not None  # чужая запись осталась нетронутой
    else:
        assert reloaded is None  # своя запись удалена


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
