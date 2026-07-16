"""Тесты клетки bot: фасад регистрации обработчиков (``register_handlers``).

Контракт-тесты проверяют форму фасада (``register_handlers``) и сигнатуру
``register_handlers(dp, session_factory)``. Logic-тесты через mock
``Dispatcher`` инспектируют порядок регистрации (командные раньше текстовых,
``handle_remind_phrase`` раньше ``handle_text``, ``handle_group`` последним
без ``PrivateOnly``), подключение ``handle_callback`` через
``dp.callback_query`` и ``handle_db_error`` как errors-handler с фильтром
``SQLAlchemyError``, а также замыкание фабрики сессий в пишущих обработчиках.
"""

import inspect
from unittest.mock import MagicMock

import pytest
from aiogram.filters import Command, ExceptionTypeFilter
from sqlalchemy.exc import SQLAlchemyError

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
    handle_callback,
    handle_db_error,
    handle_group,
    handle_remind_phrase,
    handle_text,
    handle_unknown_command,
    register_handlers,
)
from remindme.bot import handlers as handlers_module
from remindme.bot.handlers import (
    register_handlers as handlers_register_handlers,
)

# Все message-обработчики, регистрируемые фасадом (кроме callback/errors).
_MESSAGE_HANDLERS = [
    cmd_start,
    cmd_help,
    cmd_remind,
    cmd_reminders,
    cmd_timezone,
    cmd_cancel,
    cmd_note,
    cmd_notes,
    cmd_todo,
    cmd_todos,
    cmd_completed,
    handle_unknown_command,
    handle_remind_phrase,
    handle_text,
    handle_group,
]

# Командные обработчики с конкретными ``Command``-фильтрами.
_COMMAND_HANDLERS = [
    cmd_start,
    cmd_help,
    cmd_remind,
    cmd_reminders,
    cmd_timezone,
    cmd_cancel,
    cmd_note,
    cmd_notes,
    cmd_todo,
    cmd_todos,
    cmd_completed,
]


def _message_handler_order(dp: MagicMock) -> list:
    """Возвращает список message-обработчиков в порядке регистрации.

    Args:
        dp: mock диспетчера с записанными вызовами ``dp.message.register``.

    Returns:
        Список обработчиков (первый позиционный аргумент каждого вызова).
    """
    return [call.args[0] for call in dp.message.register.call_args_list]


# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_register_handlers() -> None:
    """Фасад экспортирует ``register_handlers`` через ``__all__``."""
    assert "register_handlers" in bot_facade.__all__
    assert bot_facade.register_handlers is not None


def test_register_handlers_signature() -> None:
    """Сигнатура ``register_handlers(dp, session_factory)``."""
    code = register_handlers.__code__
    params = code.co_varnames[: code.co_argcount]
    assert params == ("dp", "session_factory")


def test_register_handlers_is_plain_function() -> None:
    """``register_handlers`` — обычная (не async) функция."""
    assert callable(register_handlers)
    assert not inspect.iscoroutinefunction(register_handlers)


def test_register_handlers_defined_in_handlers_module() -> None:
    """Реализация живёт в ``handlers.py``."""
    assert register_handlers is handlers_register_handlers
    assert register_handlers.__module__ == "remindme.bot.handlers"


# --- Фикстура: mock Dispatcher и восстановление фабрики сессий ---


@pytest.fixture
def dp_and_factory():
    """Возвращает ``(dp, session_factory)`` и сбрасывает замкнутую фабрицию.

     ``register_handlers`` устанавливает модульную ``_session_factory``; чтобы
    _side-effect не протёк в другие тесты клетки, восстанавливаем ``None``.
    """
    dp = MagicMock()
    session_factory = MagicMock(name="session_factory")
    saved = handlers_module._session_factory
    yield dp, session_factory
    handlers_module._session_factory = saved


# --- Logic-тесты: порядок регистрации ---


def test_register_handlers_orders_group_last(dp_and_factory) -> None:  # noqa: ANN001
    """``handle_group`` регистрируется последним среди message-обработчиков."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    order = _message_handler_order(dp)
    assert order[-1] is handle_group


def test_register_handlers_remind_phrase_before_text_before_group(
    dp_and_factory,
):  # noqa: ANN001
    """Фраза раньше общего текста, общий текст раньше группы."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    order = _message_handler_order(dp)
    assert order.index(handle_remind_phrase) < order.index(handle_text)
    assert order.index(handle_text) < order.index(handle_group)


def test_register_handlers_commands_before_text(dp_and_factory) -> None:  # noqa: ANN001
    """Все командные обработчики раньше ``handle_text`` и ``handle_group``."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    order = _message_handler_order(dp)
    text_idx = order.index(handle_text)
    for handler in _COMMAND_HANDLERS + [handle_unknown_command]:
        assert order.index(handler) < text_idx


def test_register_handlers_all_message_handlers_registered(
    dp_and_factory,
):  # noqa: ANN001
    """Все 15 message-обработчиков зарегистрированы ровно по одному разу."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    order = _message_handler_order(dp)
    assert set(order) == set(_MESSAGE_HANDLERS)
    assert len(order) == len(_MESSAGE_HANDLERS)


# --- Logic-тесты: фильтры PrivateOnly и Command ---


def test_register_handlers_attaches_privateonly(dp_and_factory) -> None:  # noqa: ANN001
    """Каждый message-обработчик имеет ``PrivateOnly``, кроме ``handle_group``."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    for call in dp.message.register.call_args_list:
        handler = call.args[0]
        filters = call.args[1:]
        if handler is handle_group:
            assert not any(isinstance(f, PrivateOnly) for f in filters)
        else:
            assert any(isinstance(f, PrivateOnly) for f in filters), str(handler)


def test_register_handlers_group_has_no_filter(dp_and_factory) -> None:  # noqa: ANN001
    """``handle_group`` регистрируется без фильтров (последний, всеохватный)."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    group_calls = [
        c for c in dp.message.register.call_args_list if c.args[0] is handle_group
    ]
    assert len(group_calls) == 1
    # Только сам обработчик, без фильтров.
    assert len(group_calls[0].args) == 1


def test_register_handlers_command_handlers_have_command_filter(
    dp_and_factory,
):  # noqa: ANN001
    """Каждый конкретный командный обработчик зарегистрирован с ``Command``."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    for call in dp.message.register.call_args_list:
        handler = call.args[0]
        filters = call.args[1:]
        if handler in _COMMAND_HANDLERS:
            assert any(isinstance(f, Command) for f in filters), str(handler)


def test_register_handlers_text_has_only_privateonly(dp_and_factory) -> None:  # noqa: ANN001
    """``handle_text`` зарегистрирован с единственным фильтром ``PrivateOnly``."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    text_calls = [
        c for c in dp.message.register.call_args_list if c.args[0] is handle_text
    ]
    assert len(text_calls) == 1
    filters = text_calls[0].args[1:]
    assert len(filters) == 1
    assert isinstance(filters[0], PrivateOnly)


# --- Logic-тесты: callback и errors-handler ---


def test_register_handlers_registers_callback(dp_and_factory) -> None:  # noqa: ANN001
    """``handle_callback`` зарегистрирован через ``dp.callback_query``."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    cb_handlers = [c.args[0] for c in dp.callback_query.register.call_args_list]
    assert handle_callback in cb_handlers


def test_register_handlers_registers_error_handler(dp_and_factory) -> None:  # noqa: ANN001
    """``handle_db_error`` зарегистрирован как errors-handler с фильтром БД."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    err_calls = list(dp.errors.register.call_args_list)
    assert len(err_calls) == 1
    call = err_calls[0]
    assert call.args[0] is handle_db_error

    filters = call.args[1:]
    exc_filters = [f for f in filters if isinstance(f, ExceptionTypeFilter)]
    assert len(exc_filters) == 1
    assert SQLAlchemyError in exc_filters[0].exceptions


# --- Logic-тесты: замыкание фабрики сессий ---


def test_register_handlers_closes_session_factory(dp_and_factory) -> None:  # noqa: ANN001
    """Фабрика сессий замыкается в ``handlers._session_factory``."""
    dp, session_factory = dp_and_factory
    register_handlers(dp, session_factory)

    assert handlers_module._session_factory is session_factory
