# Репозиторий напоминаний — операции по владельцу и статусу

Домен: CRUD и статусные переходы напоминаний через `AsyncSession` с изоляцией по владельцу.
Аудитория: клетка `services` (`create_reminder`, `cancel_reminder`, `set_user_timezone`), клетка `bot` (`list_reminders`), клетка `worker` (`find_due_reminders`, `claim_for_sending`, `mark_sent`, `record_send_failure`, `mark_failed`, `recover_stuck_sending`).

Все операции принимают открытую `AsyncSession`. Каждая операция фильтруется одновременно по `user_id` (владельцу) и статусу; поиск/изменение только по `id` запрещён. Timestamp-поля — строки ISO 8601 UTC. Возврат `rowcount` (через `result.rowcount == 1`) отличает «успех» от «не найдено/не владельцу», не раскрывая чужие данные.

## Создание напоминания

```python
from .db.repositories import create_reminder

reminder = await create_reminder(
    session=session,
    telegram_user_id=user_id,
    text="Купить продукты",
    remind_at_utc=remind_at_utc,  # aware UTC datetime
    now=now,                       # aware UTC datetime
)
# create_reminder фиксирует транзакцию сам (commit); при ошибке — rollback + re-raise
```

Начальные значения: `status="scheduled"`, `attempt_count=0`, `next_attempt_at_utc=remind_at_utc`. При ошибке SQLAlchemy репозиторий откатывает транзакцию и повторно поднимает исключение.

## Список активных напоминаний

```python
from .db.repositories import list_reminders

reminders = await list_reminders(session=session, user_id=user_id)
# только status="scheduled" текущего пользователя, order_by remind_at_utc asc, лимит 20
```

## Отмена

```python
from .db.repositories import cancel_reminder

outcome = await cancel_reminder(session=session, reminder_id=reminder_id, user_id=user_id)
# outcome.kind: "cancelled" — ровно одна запись scheduled→cancelled;
# "already_sending" — запись уже захвачена worker (status="sending");
# "not_found" — не найдено/чужая запись (существование чужих не раскрывается)
```

## Worker: выборка, захват, отметка об отправке

Атомарный захват записи гарантирует, что две итерации цикла не заберут одно напоминание: проверка `status="scheduled"` в `WHERE` + `rowcount == 1`.

```python
from .db.repositories import find_due_reminders, claim_for_sending, mark_sent

now = datetime.now(tz=timezone.utc)
due = await find_due_reminders(session=session, now=now)

for reminder in due:
    if await claim_for_sending(session=session, reminder_id=reminder.id, now=now):
        await bot.send_message(chat_id=reminder.user_id, text=text)
        await mark_sent(session=session, reminder_id=reminder.id, now=now)
```

## Worker: повторные попытки, failed и восстановление зависших (этап 3)

Повторные попытки и `failed` — на стороне worker; репозиторий предоставляет атомарные операции по статусу.

```python
from .db.repositories import record_send_failure, mark_failed, recover_stuck_sending

# при старте worker — один раз: вернуть зависшие sending (locked_at_utc старше 60с) в scheduled
recovered = await recover_stuck_sending(session=session, now=now)  # default stale_after_seconds=60

# преходящая ошибка send_message: 1–3-я неудача → повтор по 30/120/600; 4-я → mark_failed
failed_now = await record_send_failure(session=session, reminder_id=reminder.id, now=now)
# failed_now=True  → запись переведена в failed (4-я неудача)
# failed_now=False → назначен следующий attempt (scheduled, next_attempt_at_utc=now+{30|120|600})

# блокировка бота пользователем (TelegramForbiddenError) → немедленный failed, без повторов
await mark_failed(session=session, reminder_id=reminder.id)
```

Расписание повторов зафиксировано: `attempt_count` 1→+30с, 2→+120с, 3→+600с, 4→`failed`. `record_send_failure` на 4-й неудаче делегирует `mark_failed`.

## Смена часового пояса и отмена с исходом (этап 3)

```python
from .db.repositories import set_user_timezone, cancel_reminder

# смена зоны пользователя (валидация IANA — в сценарии services)
updated = await set_user_timezone(
    session=session, telegram_user_id=user_id, timezone="Europe/Moscow", now=now,
)

# отмена возвращает различимый исход CancelOutcome
outcome = await cancel_reminder(session=session, reminder_id=reminder_id, user_id=user_id)
# outcome.kind ∈ {cancelled, not_found, already_sending}
```

- `set_user_timezone` не двигает уже созданные напоминания — меняется только отображение (UTC-момент хранится).
- `cancel_reminder` отличает «отменено» (`cancelled`), «не найдено/не владельцу» (`not_found`) и «уже отправляется»
  (`already_sending`, запись в статусе `sending`); чужие записи не раскрываются.

## Заметки — CRUD по владельцу (этап 4)

Домен: CRUD заметок через `AsyncSession` с изоляцией по владельцу (тот же паттерн, что reminder).
Аудитория: клетка `services` (`create_note_scenario`), клетка `bot` (`list_notes`, `delete_note`).

```python
from .db.repositories import create_note, list_notes, delete_note

note = await create_note(session=session, telegram_user_id=user_id, text="Код 1234", now=now)
# create_note фиксирует транзакцию сам; при ошибке — rollback + re-raise

notes = await list_notes(session=session, user_id=user_id)
# только текущего пользователя, order_by created_at_utc desc, лимит 20

deleted = await delete_note(session=session, note_id=note_id, user_id=user_id)
# True — ровно одна запись удалена; False — не найдено/чужая (существование чужих не раскрывается)
```

- Текст заметки (1–500) валидируется сценарием `services` до вызова `create_note`.

## Задачи — CRUD и статусные переходы по владельцу (этап 4)

Домен: CRUD задач и переход active→completed через `AsyncSession` с изоляцией по владельцу.
Аудитория: клетка `services` (`create_todo_scenario`), клетка `bot` (`list_todos`, `list_completed`, `complete_todo`, `delete_todo`).

```python
from .db.repositories import (
    create_todo, list_todos, list_completed, complete_todo, delete_todo,
)

todo = await create_todo(
    session=session, telegram_user_id=user_id, text="Отчёт",
    due_at_utc=due_at_utc,  # aware UTC или None (задача без срока); past допускается
    now=now,
)
# status="active"

todos = await list_todos(session=session, user_id=user_id)
# активные: сначала со сроком (asc), затем без; лимит 20

completed = await list_completed(session=session, user_id=user_id)
# status="completed", order completed_at_utc desc, лимит 20

done = await complete_todo(session=session, todo_id=todo_id, user_id=user_id, now=now)
# True — ровно одна active→completed (+ completed_at_utc); False — уже completed/не найдено/чужая

was_completed = await delete_todo(session=session, todo_id=todo_id, user_id=user_id)
# None — не найдено/чужая; True — удалена и была completed; False — удалена и была active
```

- `complete_todo` через `rowcount == 1` отличает «завершено» от «уже изменено» (повторный клик → False).
- `delete_todo` возвращает статус до удаления: `True` → перерисовать `/completed`, `False` → `/todos`, `None` → «Запись уже изменена или удалена.»

## Предусловия и побочные эффекты

- `session` открыта; каждая функция фиксирует транзакцию (`commit`), при ошибке откатывает и залогирует её (structured logging) перед повторным raise.
- Сравнение `next_attempt_at_utc <= now_iso` выполняется лексикографически по строкам ISO 8601 — корректно при фиксированном формате.
- `find_due_reminders` не переводит статус; захват делает `claim_for_sending`, отметку — `mark_sent`.
- Этап 3: повторные попытки 30/120/600, `failed` после 4-й неудачи и восстановление зависших `sending` реализованы (см. выше).
