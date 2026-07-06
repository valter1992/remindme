"""Клетка bot — слой Telegram-интерфейса RemindMe.

Фасадный модуль клетки экспонирует собственные сущности: форматтеры ответов и
уведомлений (``to_local_string``, ``format_*``) и билдеры inline-клавиатур
списков (``notes_keyboard``, ``todos_keyboard``, ``completed_keyboard``,
``reminders_keyboard``). Фильтр приватных чатов, обработчики команд и
сообщений (aiogram) и callback-роутер наполняются в последующих задачах клетки.
"""

from .formatter import (
    format_cancel_outcome,
    format_completed_list,
    format_note_confirmation,
    format_note_error,
    format_note_list,
    format_notification,
    format_parse_error,
    format_reminder_confirmation,
    format_reminder_list,
    format_todo_confirmation,
    format_todo_error,
    format_todo_list,
    to_local_string,
)
from .keyboards import (
    completed_keyboard,
    notes_keyboard,
    reminders_keyboard,
    todos_keyboard,
)

__all__ = [
    "completed_keyboard",
    "format_cancel_outcome",
    "format_completed_list",
    "format_notification",
    "format_note_confirmation",
    "format_note_error",
    "format_note_list",
    "format_parse_error",
    "format_reminder_confirmation",
    "format_reminder_list",
    "format_todo_confirmation",
    "format_todo_error",
    "format_todo_list",
    "notes_keyboard",
    "reminders_keyboard",
    "to_local_string",
    "todos_keyboard",
]
