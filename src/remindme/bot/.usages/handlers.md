# Handlers — регистрация в диспетчере

Домен: подключение обработчиков клетки `bot` к aiogram `Dispatcher` через единый фасад.
Аудитория: клетка `main` (точка входа).

Все пишущие обработчики фильтруются `PrivateOnly` (только приватные чаты). Регистрация инкапсулирована в `register_handlers`: командные обработчики регистрируются раньше текстовых; обработчик фразы «напомни …» — перед общим `handle_text`, чтобы фраза не уходила в общий разбор; последним без `PrivateOnly` регистрируется `handle_group`.

## Регистрация (этап 2)

```python
from aiogram import Dispatcher

from .bot.handlers import register_handlers

dp = Dispatcher()
register_handlers(dp, session_factory)
```

`register_handlers` принимает фабрику сессий `session_factory`, замыкает её в пишущих обработчиках и регистрирует в порядке:
1. `cmd_start` (PrivateOnly + команда start)
2. `cmd_help` (PrivateOnly + команда help)
3. `cmd_remind` (PrivateOnly + команда remind)
4. `cmd_reminders` (PrivateOnly + команда reminders)
5. `handle_unknown_command` (PrivateOnly + любая команда)
6. `handle_remind_phrase` (PrivateOnly; текст, начинающийся с «напомни»)
7. `handle_text` (PrivateOnly; общий текст — последний среди приватных)
8. `handle_group` (без PrivateOnly; последним — сообщения из групп)

## Предусловия и побочные эффекты

- Командные обработчики раньше текстовых; `handle_remind_phrase` раньше `handle_text`; `handle_group` последним без `PrivateOnly`.
- `session_factory` строится точкой входа один раз и переиспользуется всеми пишущими обработчиками.

## Смена часового пояса и централизованная обработка ошибок — этап 3

```python
from aiogram.filters import Command
from aiogram.types import Message

# /timezone Europe/Moscow — смена зоны; неизвестная IANA отклоняется
@dp.message(Command("timezone"))
async def cmd_timezone(message: Message):
    ...
    updated = await set_timezone_scenario(
        session=session, telegram_user_id=user.telegram_user_id, timezone=arg, now=now,
    )
    # updated=True  → «часовой пояс изменён на {arg}»
    # updated=False → «неизвестный пояс, используйте IANA, например Europe/Moscow»
```

Глобальный errors-handler ловит ошибки БД и показывает единый текст:

```python
from aiogram.types import ErrorEvent
from sqlalchemy.exc import SQLAlchemyError

@dp.errors()
async def handle_db_error(event: ErrorEvent):
    exc = event.exception
    if not isinstance(exc, SQLAlchemyError):
        return  # пропускаем неперехваченные исключения дальше
    # лог ERROR (user_id из update, код ошибки; без текста записи и токена); rollback НЕ нужен — его сделал репозиторий
    await message.answer("Не удалось выполнить действие. Попробуйте ещё раз немного позже.")
```

- `/timezone` и errors-handler регистрируются в `register_handlers` после команд этапа 2; `/timezone` — с фильтром
  `PrivateOnly` + `Command`.
- errors-handler реагирует только на `SQLAlchemyError`; код ошибки логируется, текст записи и токен — никогда. Откат
  транзакции делает репозиторий (до re-raise), errors-handler не дублирует его — сессия обработчика к этому моменту
  уже закрыта.

`/cancel <id>` отменяет своё напоминание и показывает различимый исход:

```python
@dp.message(Command("cancel"))
async def cmd_cancel(message: Message):
    ...
    outcome = await cancel_reminder_scenario(
        session=session, telegram_user_id=user.telegram_user_id, reminder_id=reminder_id,
    )
    # outcome.kind ∈ {cancelled, not_found, already_sending}
    await message.answer(format_cancel_outcome(outcome))
    # already_sending → «Напоминание уже отправляется и не может быть отменено.»
```

- `/cancel` регистрируется с `PrivateOnly` + `Command`; изоляция по владельцу — в `cancel_reminder_scenario`.

## Команды заметок/задач и callback-роутер — этап 4

`register_handlers` дополнительно регистрирует (PrivateOnly + Command): `cmd_note`, `cmd_notes`, `cmd_todo`, `cmd_todos`, `cmd_completed`; и `handle_callback` через `dp.callback_query()`. Командные обработчики остаются раньше текстовых; `session_factory` переиспользуется.

```python
@dp.message(Command("note"))
async def cmd_note(message: Message):
    ...
    outcome = await create_note_scenario(session=session, telegram_user_id=user.telegram_user_id, raw=raw, now=now)
    # NoteError → format_note_error; иначе → format_note_confirmation

@dp.message(Command("todos"))
async def cmd_todos(message: Message):
    todos = await list_todos(session=session, user_id=user.telegram_user_id)
    await message.answer(format_todo_list(todos, user.timezone), reply_markup=todos_keyboard([t.id for t in todos]))
```

- Списки `/notes /todos /completed` и `/reminders` (модифицирован) отправляются с inline-клавиатурой (`reply_markup`).
