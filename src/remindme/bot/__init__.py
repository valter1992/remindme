"""Клетка bot — слой Telegram-интерфейса RemindMe.

Фасадный модуль клетки экспонирует собственные сущности: форматтеры ответов и
уведомлений (``to_local_string``, ``format_*``) и билдеры inline-клавиатур
списков (``notes_keyboard``, ``todos_keyboard``, ``completed_keyboard``,
``reminders_keyboard``). Фильтр приватных чатов, обработчики команд и сообщений
(aiogram) и единый callback-роутер inline-кнопок (``handle_callback``).
"""

from .callbacks import handle_callback
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
from .handlers import (
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
    register_handlers,
)
from .keyboards import (
    completed_keyboard,
    notes_keyboard,
    reminders_keyboard,
    todos_keyboard,
)

__all__ = [
    "PrivateOnly",
    "cmd_cancel",
    "cmd_completed",
    "cmd_help",
    "cmd_note",
    "cmd_notes",
    "cmd_remind",
    "cmd_reminders",
    "cmd_start",
    "cmd_timezone",
    "cmd_todo",
    "cmd_todos",
    "completed_keyboard",
    "ensure_user",
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
    "handle_callback",
    "handle_db_error",
    "handle_group",
    "handle_remind_phrase",
    "handle_text",
    "handle_unknown_command",
    "notes_keyboard",
    "register_handlers",
    "reminders_keyboard",
    "to_local_string",
    "todos_keyboard",
]
