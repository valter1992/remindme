"""Тесты клетки bot: inline-клавиатуры (``keyboards.py``).

Контракт-тесты проверяют форму фасада (4 билдера доступны из ``remindme.bot``)
и их сигнатуры; logic-тесты — возврат ``None`` для пустого списка, формат
``callback_data`` вида ``<action>:<entity>:<id>``, наличие кнопок «Выполнено» и
«Удалить» у ``todos_keyboard`` и ограничение длины callback_data в 64 байта.
Билдеры чистые — без обращения к БД и Telegram.
"""

import pytest
from aiogram.types import InlineKeyboardMarkup

from remindme import bot as bot_facade
from remindme.bot import (
    completed_keyboard,
    notes_keyboard,
    reminders_keyboard,
    todos_keyboard,
)

_EXPECTED = {
    "notes_keyboard",
    "todos_keyboard",
    "completed_keyboard",
    "reminders_keyboard",
}


def _buttons(kb: InlineKeyboardMarkup):
    """Все кнопки клавиатуры плоским списком (в порядке рядов)."""
    return [btn for row in kb.inline_keyboard for btn in row]


# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_all_keyboards() -> None:
    """Фасад экспортирует все 4 билдера клавиатур через ``__all__``."""
    assert _EXPECTED.issubset(set(bot_facade.__all__))
    for name in _EXPECTED:
        assert callable(getattr(bot_facade, name))


@pytest.mark.parametrize(
    ("builder", "param"),
    [
        (notes_keyboard, "note_ids"),
        (todos_keyboard, "todo_ids"),
        (completed_keyboard, "todo_ids"),
        (reminders_keyboard, "reminder_ids"),
    ],
)
def test_builder_signature(builder, param: str) -> None:
    """Каждый билдер принимает единственный позиционный параметр — список id."""
    params = builder.__code__.co_varnames[: builder.__code__.co_argcount]
    assert params == (param,)


@pytest.mark.parametrize(
    ("builder", "param"),
    [
        (notes_keyboard, "note_ids"),
        (todos_keyboard, "todo_ids"),
        (completed_keyboard, "todo_ids"),
        (reminders_keyboard, "reminder_ids"),
    ],
)
def test_builder_returns_inline_markup_or_none(builder, param: str) -> None:
    """Непустой список возвращает ``InlineKeyboardMarkup``."""
    kb = builder([1])
    assert kb is not None
    assert isinstance(kb, InlineKeyboardMarkup)


# --- Logic-тесты: пустой список → None ---


@pytest.mark.parametrize(
    "builder",
    [notes_keyboard, todos_keyboard, completed_keyboard, reminders_keyboard],
)
def test_empty_list_returns_none(builder) -> None:
    """Пустой список записей → ``None`` (сообщение рисуется без клавиатуры)."""
    assert builder([]) is None


# --- Logic-тесты: формат callback_data ---


def test_notes_keyboard_callback_data() -> None:
    """Каждая кнопка — «Удалить» с callback_data ``delete:note:<id>``."""
    kb = notes_keyboard([1, 2, 3])
    assert kb is not None
    assert len(kb.inline_keyboard) == 3
    datas = [btn.callback_data for btn in _buttons(kb)]
    assert datas == ["delete:note:1", "delete:note:2", "delete:note:3"]
    assert all(btn.text == "Удалить" for btn in _buttons(kb))


def test_reminders_keyboard_callback_data() -> None:
    """Каждая кнопка — «Удалить» с callback_data ``delete:reminder:<id>``."""
    kb = reminders_keyboard([15, 7])
    assert kb is not None
    datas = [btn.callback_data for btn in _buttons(kb)]
    assert datas == ["delete:reminder:15", "delete:reminder:7"]


def test_completed_keyboard_callback_data() -> None:
    """Каждая кнопка — «Удалить» с callback_data ``delete:todo:<id>``."""
    kb = completed_keyboard([42])
    assert kb is not None
    datas = [btn.callback_data for btn in _buttons(kb)]
    assert datas == ["delete:todo:42"]


@pytest.mark.parametrize(
    ("builder", "ids", "action_entity_pairs"),
    [
        (notes_keyboard, [1, 2], [("delete", "note"), ("delete", "note")]),
        (reminders_keyboard, [3], [("delete", "reminder")]),
        (completed_keyboard, [4], [("delete", "todo")]),
    ],
)
def test_callback_data_format(builder, ids, action_entity_pairs) -> None:
    """callback_data имеет формат ``<action>:<entity>:<id>`` с совпадающим id."""
    kb = builder(ids)
    assert kb is not None
    buttons = _buttons(kb)
    assert len(buttons) == len(ids)
    for btn, (action, entity), record_id in zip(
        buttons, action_entity_pairs, ids, strict=True
    ):
        assert btn.callback_data == f"{action}:{entity}:{record_id}"


def test_todos_keyboard_has_complete_and_delete() -> None:
    """На каждую активную задачу — ряд «Выполнено» + «Удалить»."""
    kb = todos_keyboard([10, 11])
    assert kb is not None
    assert len(kb.inline_keyboard) == 2

    first_row = kb.inline_keyboard[0]
    assert [btn.text for btn in first_row] == ["Выполнено", "Удалить"]
    assert [btn.callback_data for btn in first_row] == [
        "complete:todo:10",
        "delete:todo:10",
    ]

    second_row = kb.inline_keyboard[1]
    assert [btn.callback_data for btn in second_row] == [
        "complete:todo:11",
        "delete:todo:11",
    ]


def test_each_id_is_own_row() -> None:
    """Одна запись — один ряд (заметки), id сохраняют порядок входного списка."""
    kb = notes_keyboard([5, 9, 2])
    assert kb is not None
    assert len(kb.inline_keyboard) == 3
    assert [len(row) for row in kb.inline_keyboard] == [1, 1, 1]


# --- Logic-тесты: ограничение длины callback_data ---


@pytest.mark.parametrize(
    "builder",
    [notes_keyboard, todos_keyboard, completed_keyboard, reminders_keyboard],
)
def test_callback_data_under_64_bytes(builder) -> None:
    """Каждый callback_data не длиннее 64 байт (лимит Telegram callback_data)."""
    kb = builder([1, 999_999_999, 2**31 - 1])
    assert kb is not None
    for btn in _buttons(kb):
        assert len(btn.callback_data.encode("utf-8")) <= 64
