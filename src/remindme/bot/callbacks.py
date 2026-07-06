"""Единый callback-роутер inline-кнопок клетки bot.

Разбор ``callback_data`` формата ``<action>:<entity>:<id>`` (``complete:todo:42``,
``delete:note:7`` и т.п.), проверка владельца на стороне репозиториев/сценариев по
``(record_id, user_id)`` и перерисовка сообщения-списка через ``editMessageText``
после действия. Некорректный payload (не 3 части / нечисловой id) и устаревшие либо
чужие кнопки обрабатываются единым ответом «Запись уже изменена или удалена.» без
обращения к БД; чужие данные не раскрываются. ``now`` (aware UTC) фиксируется один
раз на вход — для ``complete_todo`` (``completed_at_utc``). Фабрика сессий
переиспользуется из клетки (``handlers._session_factory``), устанавливаемая фасадом
:func:`register_handlers` при запуске и подменяемая в тестах.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import CallbackQuery
from sqlalchemy import select

from ..config import get_settings
from ..db import (
    User,
    complete_todo,
    delete_note,
    delete_todo,
    list_completed,
    list_notes,
    list_reminders,
    list_todos,
)
from ..services import cancel_reminder_scenario
from . import handlers as _handlers
from .formatter import (
    format_completed_list,
    format_note_list,
    format_reminder_list,
    format_todo_list,
)
from .keyboards import (
    completed_keyboard,
    notes_keyboard,
    reminders_keyboard,
    todos_keyboard,
)

__all__ = ["handle_callback"]

# Единый ответ для некорректного/устаревшего/чужого callback.
_STALE = "Запись уже изменена или удалена."
# Ответ о невозможности отмены уходящего напоминания (этап 3).
_ALREADY_SENDING = "Напоминание уже отправляется и не может быть отменено."


async def handle_callback(callback: CallbackQuery) -> None:
    """Единый callback-роутер: разбор action:entity:id, действие, перерисовка.

    Шаги строго по контракту: зафиксировать aware-UTC ``now``; разобрать
    ``callback.data`` по ``:`` — некорректный формат (не 3 части или нечисловой id)
    завершается ``callback.answer`` без обращения к БД; открыть сессию из замкнутой
    фабрики; выполнить действие с фильтром владельца ``(record_id, user_id)`` и
    перерисовать целевой список по статусу записи через ``callback.message.edit_text``;
    на успех ответить ``callback.answer()``. Устаревшая/чужая кнопка и отправляемое
    напоминание не перерисовывают список — только отвечают.

    Args:
        callback: aiogram ``CallbackQuery`` от inline-кнопки; ``callback.data`` —
            payload ``<action>:<entity>:<id>``, ``callback.from_user.id`` — владелец.
    """
    now = datetime.now(UTC)

    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer(_STALE)
        return
    action, entity, raw_id = parts
    record_id = int(raw_id)
    user_id = callback.from_user.id
    branch = f"{action}:{entity}"

    async with _handlers._session_factory() as session:  # type: ignore[misc]
        if branch == "complete:todo":
            done = await complete_todo(
                session=session,
                todo_id=record_id,
                user_id=user_id,
                now=now,
            )
            if not done:
                await callback.answer(_STALE)
                return
            await _redraw_todos(callback, session, user_id)

        elif branch == "delete:note":
            deleted = await delete_note(
                session=session,
                note_id=record_id,
                user_id=user_id,
            )
            if not deleted:
                await callback.answer(_STALE)
                return
            await _redraw_notes(callback, session, user_id)

        elif branch == "delete:todo":
            was = await delete_todo(
                session=session,
                todo_id=record_id,
                user_id=user_id,
            )
            if was is None:
                await callback.answer(_STALE)
                return
            if was:
                await _redraw_completed(callback, session, user_id)
            else:
                await _redraw_todos(callback, session, user_id)

        elif branch == "delete:reminder":
            outcome = await cancel_reminder_scenario(
                session=session,
                telegram_user_id=user_id,
                reminder_id=record_id,
            )
            if outcome.kind == "already_sending":
                await callback.answer(_ALREADY_SENDING)
                return
            if outcome.kind == "not_found":
                await callback.answer(_STALE)
                return
            await _redraw_reminders(callback, session, user_id)

        else:
            await callback.answer(_STALE)
            return

    await callback.answer()


async def _user_timezone(session, user_id: int) -> str:  # noqa: ANN001
    """Возвращает IANA-зону пользователя для перерисовки списков локальным временем.

    Читается лениво — только в ветвях перерисовки, т.е. после успешного действия над
    существующей записью. Существование записи гарантирует существование владельца
    (FK), поэтому пользователь здесь всегда найден; зона по умолчанию из настроек —
    лишь защитный fallback.

    Args:
        session: открытая ``AsyncSession``.
        user_id: Telegram id кликнувшего пользователя.

    Returns:
        IANA-зона пользователя либо ``DEFAULT_TIMEZONE``.
    """
    user = (
        await session.execute(select(User).where(User.telegram_user_id == user_id))
    ).scalar_one_or_none()
    if user is None:
        return get_settings().DEFAULT_TIMEZONE
    return user.timezone


async def _redraw_todos(callback: CallbackQuery, session, user_id: int) -> None:  # noqa: ANN001
    """Перерисовывает сообщение списком активных задач с клавиатурой.

    Args:
        callback: callback с сообщением-списком (``callback.message.edit_text``).
        session: открытая ``AsyncSession``.
        user_id: владелец задач.
    """
    if callback.message is None:  # недоступное сообщение не редактировать
        return
    timezone = await _user_timezone(session, user_id)
    todos = await list_todos(session=session, user_id=user_id)
    keyboard = todos_keyboard([todo.id for todo in todos])
    await callback.message.edit_text(
        format_todo_list(todos, timezone),
        reply_markup=keyboard,
    )


async def _redraw_notes(callback: CallbackQuery, session, user_id: int) -> None:  # noqa: ANN001
    """Перерисовывает сообщение списком заметок с клавиатурой удаления.

    Args:
        callback: callback с сообщением-списком (``callback.message.edit_text``).
        session: открытая ``AsyncSession``.
        user_id: владелец заметок.
    """
    if callback.message is None:  # недоступное сообщение не редактировать
        return
    timezone = await _user_timezone(session, user_id)
    notes = await list_notes(session=session, user_id=user_id)
    keyboard = notes_keyboard([note.id for note in notes])
    await callback.message.edit_text(
        format_note_list(notes, timezone),
        reply_markup=keyboard,
    )


async def _redraw_completed(callback: CallbackQuery, session, user_id: int) -> None:  # noqa: ANN001
    """Перерисовывает сообщение списком выполненных задач с клавиатурой удаления.

    Args:
        callback: callback с сообщением-списком (``callback.message.edit_text``).
        session: открытая ``AsyncSession``.
        user_id: владелец задач.
    """
    if callback.message is None:  # недоступное сообщение не редактировать
        return
    timezone = await _user_timezone(session, user_id)
    todos = await list_completed(session=session, user_id=user_id)
    keyboard = completed_keyboard([todo.id for todo in todos])
    await callback.message.edit_text(
        format_completed_list(todos, timezone),
        reply_markup=keyboard,
    )


async def _redraw_reminders(callback: CallbackQuery, session, user_id: int) -> None:  # noqa: ANN001
    """Перерисовывает сообщение списком активных напоминаний с клавиатурой.

    Args:
        callback: callback с сообщением-списком (``callback.message.edit_text``).
        session: открытая ``AsyncSession``.
        user_id: владелец напоминаний.
    """
    if callback.message is None:  # недоступное сообщение не редактировать
        return
    timezone = await _user_timezone(session, user_id)
    reminders = await list_reminders(session=session, user_id=user_id)
    keyboard = reminders_keyboard([reminder.id for reminder in reminders])
    await callback.message.edit_text(
        format_reminder_list(reminders, timezone),
        reply_markup=keyboard,
    )
