"""Тесты клетки services: парсер срока и сценарий задачи (``todos.py``).

Контракт-тесты проверяют форму фасада (``TodoError``, ``ParsedTodo``,
``parse_todo_input``, ``create_todo_scenario``) и варианты ``TodoError.kind``;
logic-тесты — терпимое правило разделителя: ``|`` выделяет срок только при
валидной дате «ГГГГ-ММ-ДД ЧЧ:ММ» слева, иначе весь текст — задача без срока.
Срок в прошлом допускается; без DST/горизонта. ``parse_todo_input`` — чистая
функция (``raw``, ``timezone``); ``now`` фиксирован в сценарии. Все операции с
БД идут через фикстуру ``session`` на ``tmp_path``; при ошибке валидации запись
не создаётся.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from remindme.db import Todo, User, list_todos
from remindme.services import (
    ParsedTodo,
    TodoError,
    create_todo_scenario,
    parse_todo_input,
)

TIMEZONE = "Europe/Moscow"

# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_todo_scenario() -> None:
    """Фасад экспортирует ``TodoError``, ``ParsedTodo``, парсер и сценарий."""
    assert isinstance(TodoError, type)
    assert isinstance(ParsedTodo, type)
    assert callable(parse_todo_input)
    assert callable(create_todo_scenario)


def test_todo_error_is_kw_only_with_kind() -> None:
    """``TodoError`` — pydantic kw_only с единственным полем ``kind: str``."""
    assert set(TodoError.model_fields) == {"kind"}

    err = TodoError(kind="empty_text")
    assert err.kind == "empty_text"

    # kw_only: позиционный аргумент не разрешён
    with pytest.raises(TypeError):
        TodoError("empty_text")  # type: ignore[misc]


@pytest.mark.parametrize("kind", ["empty_text", "text_too_long"])
def test_todo_error_kinds(kind: str) -> None:
    """Допустимые варианты ``TodoError.kind`` по контракту."""
    assert TodoError(kind=kind).kind == kind


def test_parsed_todo_is_kw_only_with_optional_due() -> None:
    """``ParsedTodo`` — kw_only с ``due_at_utc: datetime | None`` и ``text``."""
    assert set(ParsedTodo.model_fields) == {"due_at_utc", "text"}

    without_due = ParsedTodo(due_at_utc=None, text="Купить хлеб")
    assert without_due.due_at_utc is None
    assert without_due.text == "Купить хлеб"

    aware = datetime(2026, 6, 25, 15, 0, tzinfo=UTC)
    with_due = ParsedTodo(due_at_utc=aware, text="Отчёт")
    assert with_due.due_at_utc == aware

    # kw_only: позиционные аргументы не разрешены
    with pytest.raises(TypeError):
        ParsedTodo(None, "x")  # type: ignore[misc]


def test_parse_todo_input_signature() -> None:
    """``parse_todo_input(raw, timezone)`` — чистая функция без ``now``."""
    params = parse_todo_input.__code__.co_varnames[
        : parse_todo_input.__code__.co_argcount
    ]
    assert params == ("raw", "timezone")


def test_create_todo_scenario_signature() -> None:
    """``create_todo_scenario(session, telegram_user_id, raw, now, timezone)``."""
    params = create_todo_scenario.__code__.co_varnames[
        : create_todo_scenario.__code__.co_argcount
    ]
    assert params == ("session", "telegram_user_id", "raw", "now", "timezone")


async def test_create_todo_scenario_return_type(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Возвращает ``Todo | TodoError`` (не третьего типа)."""
    await _make_user(session, telegram_user_id=42)

    result = await create_todo_scenario(
        session=session,
        telegram_user_id=42,
        raw="Купить хлеб",
        now=fixed_now,
        timezone=TIMEZONE,
    )
    assert isinstance(result, (Todo, TodoError))


# --- Logic-тесты: парсер ---


def test_parse_todo_input_nondate_after_pipe() -> None:
    """Левая часть — не дата → весь текст сохраняется без срока (не ошибка)."""
    result = parse_todo_input("не дата | Купить хлеб", TIMEZONE)

    assert isinstance(result, ParsedTodo)
    assert result.due_at_utc is None
    assert result.text == "не дата | Купить хлеб"


def test_parse_todo_input_valid_date_due() -> None:
    """Валидная дата слева → aware-UTC срок (Москва +3 → 15:00 UTC); текст справа."""
    result = parse_todo_input("2026-06-25 18:00 | Отчёт", TIMEZONE)

    assert isinstance(result, ParsedTodo)
    assert result.due_at_utc == datetime(2026, 6, 25, 15, 0, tzinfo=UTC)
    assert result.text == "Отчёт"


def test_parse_todo_input_valid_date_other_timezone() -> None:
    """Локализация срока идёт через переданную ``timezone`` (UTC+5 → 13:00 UTC)."""
    result = parse_todo_input("2026-06-25 18:00 | Отчёт", "Asia/Yekaterinburg")

    assert isinstance(result, ParsedTodo)
    assert result.due_at_utc == datetime(2026, 6, 25, 13, 0, tzinfo=UTC)


def test_parse_todo_input_no_pipe_no_due() -> None:
    """Текст без ``|`` → задача без срока; текст сохраняется дословно (после strip)."""
    result = parse_todo_input("Просто задача без срока", TIMEZONE)

    assert isinstance(result, ParsedTodo)
    assert result.due_at_utc is None
    assert result.text == "Просто задача без срока"


def test_parse_todo_input_pipe_but_empty_left_is_text() -> None:
    """``|`` есть, но левая часть пуста/не дата → весь текст без срока."""
    result = parse_todo_input("| Купить хлеб", TIMEZONE)

    assert isinstance(result, ParsedTodo)
    assert result.due_at_utc is None
    assert result.text == "| Купить хлеб"


def test_parse_todo_input_past_due_allowed() -> None:
    """Срок в прошлом допускается (не ошибка) — отличается от parse_remind_time."""
    result = parse_todo_input("2020-01-01 09:00 | Старое", TIMEZONE)

    assert isinstance(result, ParsedTodo)
    assert result.due_at_utc == datetime(2020, 1, 1, 6, 0, tzinfo=UTC)
    assert result.text == "Старое"


def test_parse_todo_input_malformed_date_is_text() -> None:
    """Календарно неверная дата слева → НЕ ошибка, весь текст без срока."""
    result = parse_todo_input("2026-02-31 18:00 | Невозможная дата", TIMEZONE)

    assert isinstance(result, ParsedTodo)
    assert result.due_at_utc is None
    assert result.text == "2026-02-31 18:00 | Невозможная дата"


def test_parse_todo_input_empty_text() -> None:
    """Пустой текст (после strip) → ``TodoError(empty_text)``."""
    result = parse_todo_input("   ", TIMEZONE)

    assert isinstance(result, TodoError)
    assert result.kind == "empty_text"


def test_parse_todo_input_too_long() -> None:
    """Текст длиннее 500 символов → ``TodoError(text_too_long)``."""
    result = parse_todo_input("а" * 501, TIMEZONE)

    assert isinstance(result, TodoError)
    assert result.kind == "text_too_long"


@pytest.mark.parametrize("length", [1, 500])
def test_parse_todo_input_length_boundaries(length: int) -> None:
    """Границы длины 1 и 500 допускаются (не ``text_too_long``)."""
    result = parse_todo_input("а" * length, TIMEZONE)

    assert isinstance(result, ParsedTodo)
    assert result.text == "а" * length
    assert result.due_at_utc is None


def test_parse_todo_input_strips_whitespace() -> None:
    """``strip`` убирает граничные пробелы и у текста со сроком."""
    result = parse_todo_input("2026-06-25 18:00 |   Отчёт   ", TIMEZONE)

    assert isinstance(result, ParsedTodo)
    assert result.text == "Отчёт"


def test_parse_todo_input_only_second_pipe_part_ignored_as_due() -> None:
    """Длинный текст с литералом ``|`` и не-датой слева сохраняется целиком."""
    raw = "купить | молоко | хлеб"
    result = parse_todo_input(raw, TIMEZONE)

    assert isinstance(result, ParsedTodo)
    assert result.due_at_utc is None
    assert result.text == raw


# --- Logic-тесты: сценарий ---


async def _make_user(session: AsyncSession, telegram_user_id: int = 42) -> None:
    """Создаёт пользователя с заданным ``telegram_user_id`` (родитель FK)."""
    session.add(
        User(
            telegram_user_id=telegram_user_id,
            timezone=TIMEZONE,
            created_at_utc="2026-06-24T12:00:00+00:00",
            updated_at_utc="2026-06-24T12:00:00+00:00",
        )
    )
    await session.commit()


async def test_create_todo_scenario_success_without_due(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Текст без срока → ``create_todo`` с ``due_at_utc=None``, ``status='active'``."""
    await _make_user(session, telegram_user_id=42)

    result = await create_todo_scenario(
        session=session,
        telegram_user_id=42,
        raw="Купить хлеб",
        now=fixed_now,
        timezone=TIMEZONE,
    )

    assert isinstance(result, Todo)
    assert result.user_id == 42
    assert result.text == "Купить хлеб"
    assert result.status == "active"
    assert result.due_at_utc is None
    assert result.created_at_utc == fixed_now.isoformat()

    todos = await list_todos(session=session, user_id=42)
    assert len(todos) == 1
    assert todos[0].text == "Купить хлеб"
    assert todos[0].due_at_utc is None


async def test_create_todo_scenario_success_with_due(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Текст со сроком → ``create_todo`` с aware-UTC ``due_at_utc`` (ISO в БД)."""
    await _make_user(session, telegram_user_id=42)

    result = await create_todo_scenario(
        session=session,
        telegram_user_id=42,
        raw="2026-06-25 18:00 | Отчёт",
        now=fixed_now,
        timezone=TIMEZONE,
    )

    assert isinstance(result, Todo)
    assert result.user_id == 42
    assert result.text == "Отчёт"
    assert result.status == "active"
    assert result.due_at_utc == datetime(2026, 6, 25, 15, 0, tzinfo=UTC).isoformat()

    todos = await list_todos(session=session, user_id=42)
    assert len(todos) == 1
    assert todos[0].due_at_utc == "2026-06-25T15:00:00+00:00"


async def test_create_todo_scenario_nondate_after_pipe_saved_as_text(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Левая часть — не дата → задача без срока с полным текстом (включая ``|``)."""
    await _make_user(session, telegram_user_id=42)

    result = await create_todo_scenario(
        session=session,
        telegram_user_id=42,
        raw="не дата | Купить хлеб",
        now=fixed_now,
        timezone=TIMEZONE,
    )

    assert isinstance(result, Todo)
    assert result.text == "не дата | Купить хлеб"
    assert result.due_at_utc is None


async def test_create_todo_scenario_empty_no_save(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Пустой текст → ``TodoError(empty_text)`` без обращения к БД."""
    result = await create_todo_scenario(
        session=session,
        telegram_user_id=42,
        raw="   ",
        now=fixed_now,
        timezone=TIMEZONE,
    )

    assert isinstance(result, TodoError)
    assert result.kind == "empty_text"

    todos = await list_todos(session=session, user_id=42)
    assert todos == []


async def test_create_todo_scenario_too_long_no_save(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Текст длиннее 500 → ``TodoError(text_too_long)`` без сохранения."""
    result = await create_todo_scenario(
        session=session,
        telegram_user_id=42,
        raw="а" * 501,
        now=fixed_now,
        timezone=TIMEZONE,
    )

    assert isinstance(result, TodoError)
    assert result.kind == "text_too_long"

    todos = await list_todos(session=session, user_id=42)
    assert todos == []


async def test_create_todo_scenario_past_due_allowed(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Срок в прошлом допускается — задача создаётся в ``active``."""
    await _make_user(session, telegram_user_id=42)

    result = await create_todo_scenario(
        session=session,
        telegram_user_id=42,
        raw="2020-01-01 09:00 | Старое",
        now=fixed_now,
        timezone=TIMEZONE,
    )

    assert isinstance(result, Todo)
    assert result.status == "active"
    assert result.due_at_utc is not None
