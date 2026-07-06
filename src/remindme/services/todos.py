"""Парсер срока и сценарий задачи — терпимый разбор над репозиторием.

:func:`parse_todo_input` — чистая функция: символ ``|`` разделяет срок только
когда левая часть точно соответствует формату «ГГГГ-ММ-ДД ЧЧ:ММ» (проверка
``strptime``); иначе весь аргумент становится текстом задачи без срока.
:func:`create_todo_scenario` оркестрирует парсер → репозиторий. Срок в прошлом
допускается; без DST-валидации и горизонта (отличается от
:func:`parse_remind_time`). Текущий момент фиксируется снаружи (параметр
``now``); ``datetime.now()`` внутри модуля не вызывается. При ошибке валидации
запись в БД не создаётся — :class:`TodoError` возвращается как типизированный
результат без сохранения.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import Todo, create_todo

__all__ = ["ParsedTodo", "TodoError", "create_todo_scenario", "parse_todo_input"]

logger = logging.getLogger(__name__)


class TodoError(BaseModel):
    """Типизированная ошибка валидации задачи.

    Attributes:
        kind: вариант ошибки — ``empty_text`` (пустой текст после ``strip``)
            или ``text_too_long`` (длиннее 500 символов).
    """

    model_config = ConfigDict(kw_only=True)

    kind: str


class ParsedTodo(BaseModel):
    """Результат парсинга входа задачи.

    Attributes:
        due_at_utc: aware-UTC срок или ``None`` (задача без срока). Срок в
            прошлом допускается (просроченная активная задача).
        text: очищенный текст задачи (после ``strip``).
    """

    model_config = ConfigDict(kw_only=True)

    due_at_utc: datetime | None
    text: str


def parse_todo_input(raw: str, timezone: str) -> ParsedTodo | TodoError:
    """Разбирает вход задачи, выделяя срок только при валидной дате слева.

    Чистая функция: ``|`` разделяет срок лишь тогда, когда левая часть (до
    первого ``|``) точно соответствует формату «ГГГГ-ММ-ДД ЧЧ:ММ» (проверка
    ``strptime``); тогда срок локализуется через ``timezone`` в aware UTC, а текст
    берётся из правой части. Иначе (нет ``|`` или левая часть — не дата) весь
    аргумент становится текстом задачи без срока — некорректная дата слева НЕ
    является ошибкой парсера. К тексту применяются ``strip`` и проверки длины
    (``empty_text`` / ``text_too_long``) в обеих ветках. Срок в прошлом
    допускается; DST-валидации и горизонта нет. Обращений к
    ``datetime.now()``/БД/Telegram нет.

    Args:
        raw: исходный текст задачи (возможно со сроком через ``|``).
        timezone: IANA-зона пользователя для локализации срока.

    Returns:
        :class:`ParsedTodo` при успехе либо :class:`TodoError` (без обращения к
        БД).
    """
    due_at_utc, raw_text = _split_due_and_text(raw, timezone)

    text = raw_text.strip()
    if not text:
        return TodoError(kind="empty_text")
    if len(text) > 500:
        return TodoError(kind="text_too_long")

    return ParsedTodo(due_at_utc=due_at_utc, text=text)


def _split_due_and_text(raw: str, timezone: str) -> tuple[datetime | None, str]:
    """Выделяет срок и текст задачи по терпимому правилу разделителя.

    ``|`` считается разделителем срока, только когда левая часть сводится к
    формату «ГГГГ-ММ-ДД ЧЧ:ММ» через ``strptime``; тогда срок локализуется через
    ``timezone``, а ``raw_text`` — правая часть (без strip). Иначе срок
    ``None``, а ``raw_text`` — весь ``raw`` (включая возможный литерал ``|``).

    Args:
        raw: исходный текст задачи.
        timezone: IANA-зона пользователя.

    Returns:
        Кортеж ``(due_at_utc, raw_text)``.
    """
    if "|" in raw:
        left, right = raw.split("|", 1)
        naive = _try_strptime(left.strip())
        if naive is not None:
            aware = naive.replace(tzinfo=ZoneInfo(timezone))
            return aware.astimezone(UTC), right

    return None, raw


def _try_strptime(value: str) -> datetime | None:
    """Пытается разобрать «ГГГГ-ММ-ДД ЧЧ:ММ» через ``strptime``.

    Args:
        value: кандидат срока.

    Returns:
        Разобранный naive ``datetime`` либо ``None``, если формат не подходит
        (некорректная дата слева от ``|`` — не ошибка, а сигнал «без срока»).
    """
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


async def create_todo_scenario(
    session: AsyncSession,
    telegram_user_id: int,
    raw: str,
    now: datetime,
    timezone: str,
) -> Todo | TodoError:
    """Создаёт задачу из текста (со сроком или без) или возвращает ошибку.

    Сначала выполняется чистый разбор ``raw`` через :func:`parse_todo_input`. При
    :class:`TodoError` функция возвращает её без обращения к БД — запись не
    создаётся. При успехе делегирует :func:`create_todo` с разобранными
    ``due_at_utc``/``text`` (срок может быть ``None`` или в прошлом).

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: владелец (FK на users.telegram_user_id).
        raw: исходный текст задачи (возможно со сроком через ``|``).
        now: текущий aware-UTC момент (фиксируется handler'ом).
        timezone: IANA-зона пользователя.

    Returns:
        Сохранённая :class:`Todo` при успехе либо :class:`TodoError` (без
        обращения к БД).
    """
    parsed = parse_todo_input(raw, timezone)
    if isinstance(parsed, TodoError):
        logger.info(
            "todo rejected",
            extra={"user_id": telegram_user_id, "kind": parsed.kind},
        )
        return parsed

    return await create_todo(
        session=session,
        telegram_user_id=telegram_user_id,
        text=parsed.text,
        due_at_utc=parsed.due_at_utc,
        now=now,
    )
