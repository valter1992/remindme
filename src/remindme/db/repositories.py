"""Репозиторий напоминаний и пользователя — операции по владельцу и статусу.

Каждая функция принимает открытую ``AsyncSession`` и фильтруется одновременно по
владельцу (``user_id``) и/или статусу: поиск/изменение только по ``id`` запрещён.
Timestamp-поля — строки ISO 8601 UTC. Возврат ``rowcount`` отличает «успех» от
«не найдено/не владельцу», не раскрывая чужие данные. При ``SQLAlchemyError``
репозиторий откатывает транзакцию, логирует контекст (без текста записи и токена)
и повторно поднимает исключение.
"""

import logging
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, nullslast, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Note, Reminder, Todo, User

__all__ = [
    "CancelOutcome",
    "cancel_reminder",
    "claim_for_sending",
    "complete_todo",
    "create_note",
    "create_reminder",
    "create_todo",
    "delete_note",
    "delete_todo",
    "find_due_reminders",
    "list_completed",
    "list_notes",
    "list_reminders",
    "list_todos",
    "mark_failed",
    "mark_sent",
    "recover_stuck_sending",
    "record_send_failure",
    "set_user_timezone",
]

logger = logging.getLogger(__name__)


class CancelOutcome(BaseModel):
    """Различимый исход отмены напоминания.

    Не раскрывает существование чужих записей: ``not_found`` объединяет случаи
    «отсутствует» и «принадлежит другому пользователю».

    Attributes:
        kind: ``cancelled`` — ровно одна запись scheduled→cancelled;
            ``already_sending`` — запись захвачена worker (status='sending');
            ``not_found`` — не найдено или чужая.
    """

    model_config = ConfigDict(kw_only=True)

    kind: str


async def create_reminder(
    session: AsyncSession,
    telegram_user_id: int,
    text: str,
    remind_at_utc: datetime,
    now: datetime,
) -> Reminder:
    """Создаёт напоминание в статусе scheduled и фиксирует транзакцию.

    Начальные значения: ``status='scheduled'``, ``attempt_count=0``,
    ``next_attempt_at_utc=remind_at_utc``.

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: владелец (FK на users.telegram_user_id).
        text: текст напоминания.
        remind_at_utc: момент напоминания (aware UTC).
        now: момент создания (aware UTC).

    Returns:
        Сохранённый ``Reminder`` с присвоенным ``id``.

    Raises:
        SQLAlchemyError: при ошибке БД — транзакция откатывается, контекст
            залогирован, исключение поднимается повторно.
    """
    remind_iso = remind_at_utc.isoformat()

    reminder = Reminder(
        user_id=telegram_user_id,
        text=text,
        remind_at_utc=remind_iso,
        status="scheduled",
        attempt_count=0,
        next_attempt_at_utc=remind_iso,
        created_at_utc=now.isoformat(),
    )

    try:
        session.add(reminder)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "reminder create failed",
            extra={"user_id": telegram_user_id},
        )
        raise

    return reminder


async def list_reminders(session: AsyncSession, user_id: int) -> list[Reminder]:
    """Возвращает активные напоминания пользователя (status='scheduled').

    Args:
        session: открытая ``AsyncSession``.
        user_id: владелец.

    Returns:
        Список не более чем из 20 напоминаний, ближайших по ``remind_at_utc``
        (порядок asc). Только записи текущего пользователя.
    """
    stmt = (
        select(Reminder)
        .where(Reminder.user_id == user_id, Reminder.status == "scheduled")
        .order_by(Reminder.remind_at_utc.asc())
        .limit(20)
    )
    result = await session.execute(stmt)

    return list(result.scalars().all())


async def cancel_reminder(
    session: AsyncSession,
    reminder_id: int,
    user_id: int,
) -> CancelOutcome:
    """Отменяет напоминание по ``(reminder_id, user_id)`` только из scheduled.

    Атомарный переход scheduled→cancelled: ``WHERE`` фильтрует и владельца, и
    статус; ``rowcount == 1`` означает успех. Если переход не выполнен, выполняется
    уточняющий ``SELECT status`` по ``(id, user_id)`` для различения исхода.

    Args:
        session: открытая ``AsyncSession``.
        reminder_id: идентификатор напоминания.
        user_id: владелец.

    Returns:
        ``CancelOutcome(kind='cancelled')`` при успехе;
        ``kind='already_sending'`` — запись захвачена worker (status='sending');
        ``kind='not_found'`` — не найдено или чужая.
    """
    stmt = (
        update(Reminder)
        .where(
            Reminder.id == reminder_id,
            Reminder.user_id == user_id,
            Reminder.status == "scheduled",
        )
        .values(status="cancelled")
    )
    result = await session.execute(stmt)

    if result.rowcount == 1:
        await session.commit()
        return CancelOutcome(kind="cancelled")

    status_stmt = select(Reminder.status).where(
        Reminder.id == reminder_id,
        Reminder.user_id == user_id,
    )
    status_result = await session.execute(status_stmt)
    status = status_result.scalar_one_or_none()

    if status == "sending":
        return CancelOutcome(kind="already_sending")

    return CancelOutcome(kind="not_found")


async def set_user_timezone(
    session: AsyncSession,
    telegram_user_id: int,
    timezone: str,
    now: datetime,
) -> bool:
    """Обновляет часовой пояс пользователя.

    Валидация IANA-зоны выполняется сценарием ``services`` до вызова; репозиторий
    только сохраняет значение. Одновременно обновляется ``updated_at_utc``.

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: PRIMARY KEY пользователя.
        timezone: новое значение IANA-зоны.
        now: момент изменения (aware UTC).

    Returns:
        ``True``, если обновлён ровно один пользователь (``rowcount == 1``);
        иначе ``False`` (пользователь не найден).
    """
    stmt = (
        update(User)
        .where(User.telegram_user_id == telegram_user_id)
        .values(timezone=timezone, updated_at_utc=now.isoformat())
    )
    result = await session.execute(stmt)

    if result.rowcount == 1:
        await session.commit()
        return True

    return False


async def create_note(
    session: AsyncSession,
    telegram_user_id: int,
    text: str,
    now: datetime,
) -> Note:
    """Создаёт заметку и фиксирует транзакцию.

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: владелец (FK на users.telegram_user_id).
        text: текст заметки (валидирован сценарием ``services`` 1–500).
        now: момент создания (aware UTC) для ``created_at_utc``.

    Returns:
        Сохранённая ``Note`` с присвоенным ``id``.

    Raises:
        SQLAlchemyError: при ошибке БД — транзакция откатывается, контекст
            залогирован, исключение поднимается повторно.
    """
    note = Note(
        user_id=telegram_user_id,
        text=text,
        created_at_utc=now.isoformat(),
    )

    try:
        session.add(note)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "note create failed",
            extra={"user_id": telegram_user_id},
        )
        raise

    return note


async def list_notes(session: AsyncSession, user_id: int) -> list[Note]:
    """Возвращает заметки текущего пользователя, новые сверху.

    Args:
        session: открытая ``AsyncSession``.
        user_id: владелец.

    Returns:
        Список не более чем из 20 заметок текущего пользователя, порядок
        ``created_at_utc desc``.
    """
    stmt = (
        select(Note)
        .where(Note.user_id == user_id)
        .order_by(Note.created_at_utc.desc())
        .limit(20)
    )
    result = await session.execute(stmt)

    return list(result.scalars().all())


async def delete_note(session: AsyncSession, note_id: int, user_id: int) -> bool:
    """Физически удаляет заметку по ``(note_id, user_id)``.

    Args:
        session: открытая ``AsyncSession``.
        note_id: идентификатор заметки.
        user_id: владелец.

    Returns:
        ``True``, если удалена ровно одна запись (``rowcount == 1``); иначе
        ``False`` (не найдено или чужая — существование чужих не раскрывается).

    Raises:
        SQLAlchemyError: при ошибке БД — откат, лог, re-raise.
    """
    stmt = delete(Note).where(Note.id == note_id, Note.user_id == user_id)

    try:
        result = await session.execute(stmt)
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "note delete failed",
            extra={"note_id": note_id, "user_id": user_id},
        )
        raise

    if result.rowcount == 1:
        await session.commit()
        return True

    return False


async def create_todo(
    session: AsyncSession,
    telegram_user_id: int,
    text: str,
    due_at_utc: datetime | None,
    now: datetime,
) -> Todo:
    """Создаёт задачу в статусе active (срок может быть ``None`` или в прошлом).

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: владелец (FK на users.telegram_user_id).
        text: текст задачи.
        due_at_utc: срок (aware UTC) или ``None`` — задача без срока; past
            допускается.
        now: момент создания (aware UTC) для ``created_at_utc``.

    Returns:
        Сохранённая ``Todo`` с присвоенным ``id`` (``status='active'``).

    Raises:
        SQLAlchemyError: при ошибке БД — откат, лог, re-raise.
    """
    due_iso = due_at_utc.isoformat() if due_at_utc is not None else None

    todo = Todo(
        user_id=telegram_user_id,
        text=text,
        due_at_utc=due_iso,
        status="active",
        created_at_utc=now.isoformat(),
    )

    try:
        session.add(todo)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "todo create failed",
            extra={"user_id": telegram_user_id},
        )
        raise

    return todo


async def list_todos(session: AsyncSession, user_id: int) -> list[Todo]:
    """Возвращает активные задачи: сначала со сроком (asc), затем без срока.

    Args:
        session: открытая ``AsyncSession``.
        user_id: владелец.

    Returns:
        Список активных задач (``status='active'``): сначала со сроком
        (``due_at_utc asc``), затем без срока (``NULLS LAST``); при равенстве —
        по ``created_at_utc desc``; не более 20.
    """
    stmt = (
        select(Todo)
        .where(Todo.user_id == user_id, Todo.status == "active")
        .order_by(nullslast(Todo.due_at_utc.asc()), Todo.created_at_utc.desc())
        .limit(20)
    )
    result = await session.execute(stmt)

    return list(result.scalars().all())


async def list_completed(session: AsyncSession, user_id: int) -> list[Todo]:
    """Возвращает выполненные задачи текущего пользователя, недавние сверху.

    Args:
        session: открытая ``AsyncSession``.
        user_id: владелец.

    Returns:
        Список выполненных задач (``status='completed'``), порядок
        ``completed_at_utc desc``, не более 20.
    """
    stmt = (
        select(Todo)
        .where(Todo.user_id == user_id, Todo.status == "completed")
        .order_by(Todo.completed_at_utc.desc())
        .limit(20)
    )
    result = await session.execute(stmt)

    return list(result.scalars().all())


async def complete_todo(
    session: AsyncSession,
    todo_id: int,
    user_id: int,
    now: datetime,
) -> bool:
    """Переводит задачу active→completed и проставляет ``completed_at_utc``.

    Атомарный переход через ``WHERE`` по владельцу и статусу; ``rowcount == 1``
    отличает «завершено» от «уже completed / не найдено / чужая».

    Args:
        session: открытая ``AsyncSession``.
        todo_id: идентификатор задачи.
        user_id: владелец.
        now: момент завершения (aware UTC) для ``completed_at_utc``.

    Returns:
        ``True``, если завершена ровно одна active-запись (``rowcount == 1``);
        иначе ``False`` (повторный клик по completed / не найдено / чужая —
        существование чужих не раскрывается).

    Raises:
        SQLAlchemyError: при ошибке БД — откат, лог, re-raise.
    """
    stmt = (
        update(Todo)
        .where(
            Todo.id == todo_id,
            Todo.user_id == user_id,
            Todo.status == "active",
        )
        .values(status="completed", completed_at_utc=now.isoformat())
    )

    try:
        result = await session.execute(stmt)
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "todo complete failed",
            extra={"todo_id": todo_id, "user_id": user_id},
        )
        raise

    if result.rowcount == 1:
        await session.commit()
        return True

    return False


async def delete_todo(
    session: AsyncSession,
    todo_id: int,
    user_id: int,
) -> bool | None:
    """Удаляет задачу по ``(todo_id, user_id)``, возвращая статус до удаления.

    Возвращённый статус управляет целевой перерисовкой списка в callback-роутере:
    ``True`` → ``/completed``, ``False`` → ``/todos``, ``None`` → запись уже
    изменена или удалена.

    Args:
        session: открытая ``AsyncSession``.
        todo_id: идентификатор задачи.
        user_id: владелец.

    Returns:
        ``None`` — не найдено или чужая (существование чужих не раскрывается);
        ``True`` — удалена и была ``completed``; ``False`` — удалена и была
        ``active``.

    Raises:
        SQLAlchemyError: при ошибке БД — откат, лог, re-raise.
    """
    status_stmt = select(Todo.status).where(
        Todo.id == todo_id,
        Todo.user_id == user_id,
    )
    status_result = await session.execute(status_stmt)
    status = status_result.scalar_one_or_none()

    if status is None:
        return None

    del_stmt = delete(Todo).where(Todo.id == todo_id, Todo.user_id == user_id)

    try:
        await session.execute(del_stmt)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "todo delete failed",
            extra={"todo_id": todo_id, "user_id": user_id},
        )
        raise

    return status == "completed"


# --- Worker: delivery primitives ---


async def find_due_reminders(session: AsyncSession, now: datetime) -> list[Reminder]:
    """Возвращает напоминания, готовые к доставке, не меняя статус.

    Готовность — ``status='scheduled'`` И ``next_attempt_at_utc <= now`` (сравнение
    ISO 8601 строк лексикографически корректно при фиксированном формате). Захват
    записи выполняется отдельно — ``claim_for_sending``; эта функция только выбирает.

    Args:
        session: открытая ``AsyncSession``.
        now: текущий aware-UTC момент.

    Returns:
        Список ``Reminder`` в статусе ``scheduled`` с наступившим
        ``next_attempt_at_utc``.
    """
    now_iso = now.isoformat()
    stmt = select(Reminder).where(
        Reminder.status == "scheduled",
        Reminder.next_attempt_at_utc <= now_iso,
    )
    result = await session.execute(stmt)

    return list(result.scalars().all())


async def claim_for_sending(
    session: AsyncSession,
    reminder_id: int,
    now: datetime,
) -> bool:
    """Атомарно захватывает напоминание scheduled→sending.

    Фильтр ``status='scheduled'`` в ``WHERE`` + ``rowcount == 1`` гарантируют, что
    две итерации цикла доставки не заберут одну запись: повторный claim уже
    захваченной записи вернёт ``False``. Проставляет ``locked_at_utc`` моментом захвата.

    Args:
        session: открытая ``AsyncSession``.
        reminder_id: идентификатор напоминания.
        now: момент захвата (aware UTC) для ``locked_at_utc``.

    Returns:
        ``True``, если захвачена ровно одна scheduled-запись (``rowcount == 1``);
        иначе ``False`` (уже захвачена/изменена/отсутствует).
    """
    stmt = (
        update(Reminder)
        .where(Reminder.id == reminder_id, Reminder.status == "scheduled")
        .values(status="sending", locked_at_utc=now.isoformat())
    )
    result = await session.execute(stmt)

    if result.rowcount == 1:
        await session.commit()
        return True

    return False


async def mark_sent(
    session: AsyncSession,
    reminder_id: int,
    now: datetime,
) -> None:
    """Отмечает напоминание отправленным: ``status='sent'``, ``sent_at_utc=now``.

    Вызывается worker после успешной ``bot.send_message``; фильтр только по ``id``
    (запись уже захвачена и принадлежит захватившему процессу).

    Args:
        session: открытая ``AsyncSession``.
        reminder_id: идентификатор напоминания.
        now: момент отправки (aware UTC) для ``sent_at_utc``.
    """
    stmt = (
        update(Reminder)
        .where(Reminder.id == reminder_id)
        .values(status="sent", sent_at_utc=now.isoformat())
    )
    await session.execute(stmt)
    await session.commit()


async def mark_failed(session: AsyncSession, reminder_id: int) -> bool:
    """Переводит напоминание sending→failed без инкремента ``attempt_count``.

    Атомарный переход через ``WHERE status='sending'``: блокировка бота пользователем
    (``TelegramForbiddenError``) завершает доставку сразу, без повторов. ``rowcount``
    отличает «завершено» от «уже не sending».

    Args:
        session: открытая ``AsyncSession``.
        reminder_id: идентификатор напоминания.

    Returns:
        ``True``, если переведена ровно одна sending-запись (``rowcount == 1``);
        иначе ``False``.
    """
    stmt = (
        update(Reminder)
        .where(Reminder.id == reminder_id, Reminder.status == "sending")
        .values(status="failed")
    )
    result = await session.execute(stmt)

    if result.rowcount == 1:
        await session.commit()
        return True

    return False


async def record_send_failure(
    session: AsyncSession,
    reminder_id: int,
    now: datetime,
) -> bool:
    """Регистрирует неудачу доставки: повтор по 30/120/600 c или ``failed`` на 4-й.

    Считывает ``attempt_count``; ``new_count = attempt_count + 1``. При
    ``new_count >= 4`` делегирует ``mark_failed`` и возвращает ``True``. Иначе
    назначает следующий attempt: ``status='scheduled'``, ``attempt_count=new_count``,
    ``next_attempt_at_utc = now + {1:30, 2:120, 3:600}[new_count]`` секунд,
    ``locked_at_utc = None`` — и возвращает ``False``. Текст записи и токен в лог не
    попадают.

    Args:
        session: открытая ``AsyncSession``.
        reminder_id: идентификатор напоминания (в статусе ``sending``).
        now: момент неудачи (aware UTC) — база для отсрочки.

    Returns:
        ``True``, если запись переведена в ``failed`` (4-я неудача); ``False``, если
        назначен повтор.
    """
    count_stmt = select(Reminder.attempt_count).where(Reminder.id == reminder_id)
    count_result = await session.execute(count_stmt)
    attempt_count = count_result.scalar_one()

    new_count = attempt_count + 1

    if new_count >= 4:
        return await mark_failed(session=session, reminder_id=reminder_id)

    delay = {1: 30, 2: 120, 3: 600}[new_count]
    next_attempt = now + timedelta(seconds=delay)

    stmt = (
        update(Reminder)
        .where(Reminder.id == reminder_id)
        .values(
            status="scheduled",
            attempt_count=new_count,
            next_attempt_at_utc=next_attempt.isoformat(),
            locked_at_utc=None,
        )
    )
    try:
        await session.execute(stmt)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "reminder send failure record failed",
            extra={"reminder_id": reminder_id, "new_attempt_count": new_count},
        )
        raise

    return False


async def recover_stuck_sending(
    session: AsyncSession,
    now: datetime,
    stale_after_seconds: int = 60,
) -> int:
    """Возвращает зависшие записи ``sending`` обратно в ``scheduled`` при старте.

    Запись считается зависшей, если ``status='sending'`` И ``locked_at_utc`` старше
    порога ``now - stale_after_seconds``. Такие записи возвращаются в ``scheduled`` с
    обнулённым ``locked_at_utc`` и в следующем тике снова выбираются
    ``find_due_reminders``. ``NULL``-зафиксированные записи сравнением не
    захватываются (``NULL <= x`` ложно в SQL).

    Args:
        session: открытая ``AsyncSession``.
        now: текущий aware-UTC момент.
        stale_after_seconds: порог «зависания» в секундах (по умолчанию 60).

    Returns:
        Количество возвращённых записей (``rowcount``).
    """
    threshold = (now - timedelta(seconds=stale_after_seconds)).isoformat()
    stmt = (
        update(Reminder)
        .where(
            Reminder.status == "sending",
            Reminder.locked_at_utc <= threshold,
        )
        .values(status="scheduled", locked_at_utc=None)
    )
    result = await session.execute(stmt)
    await session.commit()

    return result.rowcount
