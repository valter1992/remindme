"""Сценарий заметки — валидация текста поверх репозитория.

:func:`create_note_scenario` соединяет простую валидацию (``strip`` + границы
длины 1–500) с репозиторием :func:`create_note`. Текущий момент фиксируется
снаружи (параметр ``now``); ``datetime.now()`` внутри модуля не вызывается. При
ошибке валидации запись в БД не создаётся — :class:`NoteError` возвращается как
типизированный результат без сохранения.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import Note, create_note

__all__ = ["NoteError", "create_note_scenario"]

logger = logging.getLogger(__name__)


class NoteError(BaseModel):
    """Типизированная ошибка валидации заметки.

    Attributes:
        kind: вариант ошибки — ``empty_text`` (пустой текст после ``strip``)
            или ``text_too_long`` (длиннее 500 символов).
    """

    model_config = ConfigDict(kw_only=True)

    kind: str


async def create_note_scenario(
    session: AsyncSession,
    telegram_user_id: int,
    raw: str,
    now: datetime,
) -> Note | NoteError:
    """Создаёт заметку из текста или возвращает ошибку валидации.

    Текст обрезается от граничных пробелов (``strip``). Пустой результат даёт
    ``empty_text``, длиннее 500 символов — ``text_too_long``; в обоих случаях
    функция возвращает :class:`NoteError` без обращения к БД. При валидном тексте
    делегирует :func:`create_note`.

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: владелец (FK на users.telegram_user_id).
        raw: исходный текст заметки.
        now: текущий aware-UTC момент (фиксируется handler'ом).

    Returns:
        Сохранённая :class:`Note` при успехе либо :class:`NoteError` (без
        обращения к БД).
    """
    text = raw.strip()

    if not text:
        logger.info(
            "note rejected",
            extra={"user_id": telegram_user_id, "kind": "empty_text"},
        )
        return NoteError(kind="empty_text")

    if len(text) > 500:
        logger.info(
            "note rejected",
            extra={"user_id": telegram_user_id, "kind": "text_too_long"},
        )
        return NoteError(kind="text_too_long")

    return await create_note(
        session=session,
        telegram_user_id=telegram_user_id,
        text=text,
        now=now,
    )
