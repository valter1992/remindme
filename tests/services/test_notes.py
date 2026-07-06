"""Тесты клетки services: сценарий заметки (``notes.py``).

Контракт-тесты проверяют форму фасада (``NoteError``, ``create_note_scenario``)
и варианты ``NoteError.kind``; logic-тесты — поток strip→валидация→репозиторий:
успех, пустой текст (``empty_text``), превышение длины 500 (``text_too_long``) и
обрезку граничных пробелов. Все операции идут через фикстуру ``session`` на
``tmp_path``; ``now`` фиксируется явно (aware UTC). При ошибке валидации запись в
БД не создаётся.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from remindme.db import Note, User, list_notes
from remindme.services import NoteError, create_note_scenario

# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_note_scenario() -> None:
    """Фасад экспортирует ``NoteError`` и ``create_note_scenario``."""
    assert isinstance(NoteError, type)
    assert callable(create_note_scenario)


def test_note_error_is_kw_only_with_kind() -> None:
    """``NoteError`` — pydantic kw_only с единственным полем ``kind: str``."""
    assert set(NoteError.model_fields) == {"kind"}

    err = NoteError(kind="empty_text")
    assert err.kind == "empty_text"

    # kw_only: позиционный аргумент не разрешён
    with pytest.raises(TypeError):
        NoteError("empty_text")  # type: ignore[misc]


@pytest.mark.parametrize("kind", ["empty_text", "text_too_long"])
def test_note_error_kinds(kind: str) -> None:
    """Допустимые варианты ``NoteError.kind`` по контракту."""
    assert NoteError(kind=kind).kind == kind


def test_create_note_scenario_signature() -> None:
    """``create_note_scenario(session, telegram_user_id, raw, now)``."""
    params = create_note_scenario.__code__.co_varnames[
        : create_note_scenario.__code__.co_argcount
    ]
    assert params == ("session", "telegram_user_id", "raw", "now")


async def test_create_note_scenario_return_type(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Возвращает ``Note | NoteError`` (не третьего типа)."""
    await _make_user(session, telegram_user_id=42)

    result = await create_note_scenario(
        session=session,
        telegram_user_id=42,
        raw="Купить молоко",
        now=fixed_now,
    )
    assert isinstance(result, (Note, NoteError))


# --- Logic-тесты ---


async def _make_user(session: AsyncSession, telegram_user_id: int = 42) -> None:
    """Создаёт пользователя с заданным ``telegram_user_id`` (родитель FK)."""
    session.add(
        User(
            telegram_user_id=telegram_user_id,
            timezone="Europe/Moscow",
            created_at_utc="2026-06-24T12:00:00+00:00",
            updated_at_utc="2026-06-24T12:00:00+00:00",
        )
    )
    await session.commit()


async def test_create_note_scenario_success(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Валидный текст → ``create_note`` → сохранённая ``Note`` в БД."""
    await _make_user(session, telegram_user_id=42)

    result = await create_note_scenario(
        session=session,
        telegram_user_id=42,
        raw="Позвонить маме",
        now=fixed_now,
    )

    assert isinstance(result, Note)
    assert result.user_id == 42
    assert result.text == "Позвонить маме"
    assert result.created_at_utc == fixed_now.isoformat()

    notes = await list_notes(session=session, user_id=42)
    assert len(notes) == 1
    assert notes[0].text == "Позвонить маме"
    assert notes[0].user_id == 42


async def test_create_note_scenario_empty(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Пустой текст (после strip) → ``NoteError(empty_text)`` без сохранения."""
    result = await create_note_scenario(
        session=session,
        telegram_user_id=42,
        raw="   ",
        now=fixed_now,
    )

    assert isinstance(result, NoteError)
    assert result.kind == "empty_text"

    notes = await list_notes(session=session, user_id=42)
    assert notes == []


async def test_create_note_scenario_too_long(
    session: AsyncSession,
    fixed_now,
) -> None:
    """Текст длиннее 500 символов → ``NoteError(text_too_long)`` без сохранения."""
    result = await create_note_scenario(
        session=session,
        telegram_user_id=42,
        raw="а" * 501,
        now=fixed_now,
    )

    assert isinstance(result, NoteError)
    assert result.kind == "text_too_long"

    notes = await list_notes(session=session, user_id=42)
    assert notes == []


@pytest.mark.parametrize("length", [1, 500])
async def test_create_note_scenario_length_boundaries(
    session: AsyncSession,
    fixed_now,
    length: int,
) -> None:
    """Границы длины 1 и 500 допускаются (не ``text_too_long``)."""
    await _make_user(session, telegram_user_id=42)

    result = await create_note_scenario(
        session=session,
        telegram_user_id=42,
        raw="а" * length,
        now=fixed_now,
    )

    assert isinstance(result, Note)
    assert result.text == "а" * length

    notes = await list_notes(session=session, user_id=42)
    assert len(notes) == 1


async def test_create_note_scenario_strips_whitespace(
    session: AsyncSession,
    fixed_now,
) -> None:
    """``strip`` убирает граничные пробелы; сохраняется очищенный текст."""
    await _make_user(session, telegram_user_id=42)

    result = await create_note_scenario(
        session=session,
        telegram_user_id=42,
        raw="\n\t  Идея дня  \n",
        now=fixed_now,
    )

    assert isinstance(result, Note)
    assert result.text == "Идея дня"

    notes = await list_notes(session=session, user_id=42)
    assert len(notes) == 1
    assert notes[0].text == "Идея дня"
