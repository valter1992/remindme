"""Клетка services — чистые парсеры и сценарии RemindMe.

Фасадный модуль клетки будет экспонировать собственные сущности:
детерминированный парсер напоминаний (``parse_remind_time``,
``ParsedReminder``, ``ParseError``), сценарии напоминаний/заметок/задач
(``create_reminder_scenario``, ``set_timezone_scenario``,
``cancel_reminder_scenario``, ``create_note_scenario``,
``create_todo_scenario``, ``parse_todo_input``) и типы ошибок
(``NoteError``, ``TodoError``, ``ParsedTodo``). Наполняется в последующих
задачах клетки; ``__all__`` пока пуст.
"""

__all__: list[str] = []
