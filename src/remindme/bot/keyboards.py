"""Inline-клавиатуры списков напоминаний, заметок и задач.

Чистые билдеры без обращения к БД: строят aiogram :class:`InlineKeyboardMarkup`
из ряда inline-кнопок на каждую запись. ``callback_data`` имеет единый формат
``<action>:<entity>:<id>`` (``complete:todo:42``, ``delete:note:7`` и т.п.) и не
превосходит 64 байт — ограничение Telegram. Пустой список записей возвращает
``None``: сообщение-список рисуется без клавиатуры (см. ``callbacks.md``).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

__all__ = [
    "completed_keyboard",
    "notes_keyboard",
    "reminders_keyboard",
    "todos_keyboard",
]

# Тексты inline-кнопок.
_DELETE = "Удалить"
_COMPLETE = "Выполнено"


def notes_keyboard(note_ids: list[int]) -> InlineKeyboardMarkup | None:
    """Inline-клавиатура списка заметок: ряд «Удалить» на каждую заметку.

    Args:
        note_ids: идентификаторы заметок текущего пользователя.

    Returns:
        Готовая inline-клавиатура либо ``None`` для пустого списка.
    """
    if not note_ids:
        return None

    rows = [[_delete_button("note", note_id)] for note_id in note_ids]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def todos_keyboard(todo_ids: list[int]) -> InlineKeyboardMarkup | None:
    """Inline-клавиатура активных задач: ряд «Выполнено» + «Удалить» на каждую.

    Args:
        todo_ids: идентификаторы активных задач текущего пользователя.

    Returns:
        Готовая inline-клавиатура либо ``None`` для пустого списка.
    """
    if not todo_ids:
        return None

    rows = [
        [_complete_button(todo_id), _delete_button("todo", todo_id)]
        for todo_id in todo_ids
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def completed_keyboard(todo_ids: list[int]) -> InlineKeyboardMarkup | None:
    """Inline-клавиатура выполненных задач: ряд «Удалить» на каждую.

    Args:
        todo_ids: идентификаторы выполненных задач текущего пользователя.

    Returns:
        Готовая inline-клавиатура либо ``None`` для пустого списка.
    """
    if not todo_ids:
        return None

    rows = [[_delete_button("todo", todo_id)] for todo_id in todo_ids]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reminders_keyboard(reminder_ids: list[int]) -> InlineKeyboardMarkup | None:
    """Inline-клавиатура списка напоминаний: ряд «Удалить» на каждое.

    Args:
        reminder_ids: идентификаторы напоминаний текущего пользователя.

    Returns:
        Готовая inline-клавиатура либо ``None`` для пустого списка.
    """
    if not reminder_ids:
        return None

    rows = [[_delete_button("reminder", reminder_id)] for reminder_id in reminder_ids]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delete_button(entity: str, record_id: int) -> InlineKeyboardButton:
    """Кнопка «Удалить» с callback_data ``delete:<entity>:<record_id>``."""
    return InlineKeyboardButton(
        text=_DELETE,
        callback_data=f"delete:{entity}:{record_id}",
    )


def _complete_button(record_id: int) -> InlineKeyboardButton:
    """Кнопка «Выполнено» с callback_data ``complete:todo:<record_id>``."""
    return InlineKeyboardButton(
        text=_COMPLETE,
        callback_data=f"complete:todo:{record_id}",
    )
