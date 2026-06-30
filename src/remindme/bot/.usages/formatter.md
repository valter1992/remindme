# Formatter — тексты ответов и уведомлений

Домен: форматирование пользовательских текстов: подтверждение создания, список напоминаний, текст уведомления, текст отказа парсера, преобразование времени в локальную строку.
Аудитория: клетка `worker` (`format_notification`) и обработчики клетки `bot` (`format_reminder_confirmation`, `format_reminder_list`, `format_parse_error`).

Все функции — чистые (без побочных эффектов). Время напоминания хранится в БД как строка ISO 8601 UTC; `to_local_string` принимает её и возвращает локальную строку пользователя.

## Текст уведомления (для worker)

```python
from .bot import format_notification

text = format_notification(reminder)  # "Напоминание\n<reminder.text>"
await bot.send_message(chat_id=reminder.user_id, text=text)
```

## Локальная строка времени

```python
from .bot import to_local_string

local = to_local_string(reminder.remind_at_utc, user.timezone)  # ISO 8601 UTC str -> локальная строка
```

## Подтверждение и список

```python
from .bot import format_reminder_confirmation, format_reminder_list, format_parse_error

confirmation = format_reminder_confirmation(reminder, user.timezone)
listing = format_reminder_list(reminders, user.timezone)  # список или «нет напоминаний»
error_text = format_parse_error(parse_error)              # шаблон + пример по kind
```

## Предусловия и побочные эффекты

- Функции чистые; не обращаются к БД и Telegram.
- `to_local_string` работает со строкой ISO 8601 UTC (как в `reminder.remind_at_utc`).

## Тексты ошибок перевода часов (DST) — этап 3

`format_parse_error` покрывает новые варианты `ParseError`:

- `nonexistent_time` → «Такого времени не существует в этот день (перевод часов). Выберите другое время.»
- `ambiguous_time` → «Это время неоднозначно из-за перевода часов. Выберите другое время.»

Остальные `kind` сохраняют тексты этапа 2 (формат/пример, прошлое, горизонт, пустой текст, длина, несуществующая дата).

## Текст исхода отмены — этап 3

`format_cancel_outcome` сопоставляет `CancelOutcome.kind` пользователю:

- `cancelled` → «напоминание отменено»;
- `not_found` → «напоминание не найдено»;
- `already_sending` → «Напоминание уже отправляется и не может быть отменено.»

## Форматы заметок и задач — этап 4

Новые чистые форматтеры переиспользуют `to_local_string` (без дублирования логики локального времени):

```python
from .bot import (
    format_note_confirmation, format_note_list, format_note_error,
    format_todo_confirmation, format_todo_list, format_completed_list, format_todo_error,
)

format_note_list(notes, tz)          # список (локальное created_at + текст) или «нет заметок»
format_todo_list(todos, tz)          # активные (срок локально / «без срока» + текст) или «нет задач»
format_completed_list(todos, tz)     # completed_at локально + текст или «нет выполненных»
format_note_error(error)             # empty_text / text_too_long
format_todo_error(error)             # invalid_format / empty_text / text_too_long
```

- Просроченные активные задачи отображаются в `/todos` (доставки нет).