"""Клетка db — слой хранения RemindMe.

Фасадный модуль клетки экспонирует собственные сущности: ORM-модели
(``Base``, ``User``, ``Reminder``, ``Note``, ``Todo``), а также фабрику движка и
сессий (``create_engine``, ``create_session_factory``). Репозитории наполняются в
последующих задачах клетки.
"""

from .models import Base, Note, Reminder, Todo, User
from .repositories import (
    CancelOutcome,
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
from .session import create_engine, create_session_factory

__all__ = [
    "Base",
    "CancelOutcome",
    "Note",
    "Reminder",
    "Todo",
    "User",
    "cancel_reminder",
    "complete_todo",
    "create_engine",
    "create_note",
    "create_reminder",
    "create_session_factory",
    "create_todo",
    "delete_note",
    "delete_todo",
    "list_completed",
    "list_notes",
    "list_reminders",
    "list_todos",
    "set_user_timezone",
]
