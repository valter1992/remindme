# RemindMe — Архитектурный план этапа 4: Заметки и задачи

## Topic
`remindme-04-notes-todos` → `docs/arch/remindme-04-notes-todos.md`

Заметки (`Note`) и задачи (`Todo`) с командами создания/просмотра, inline-управлением (`Выполнено` / `Удалить`), единым callback-роутингом `<action>:<entity>:<id>` и проверкой владельца `(id, user_id)`. Модели `Note`/`Todo` и их таблицы уже созданы миграцией этапа 1 — **новых ячеек и миграций нет**, расширяются существующие `db`/`services`/`bot` и модифицируется `main`.

### Принятые проектные решения
1. **Перерисовка списка после inline-действия — по статусу записи.** `delete_todo` возвращает `was_completed: bool | None` (`None`=не найдено/чужая; `True`/`False`=удалена и была completed/active); `complete:todo`→`/todos`, `delete:note`→`/notes`, `delete:reminder`→`/reminders`. Формат `callback_data` `<action>:<entity>:<id>` не меняется.
2. **Отдельные DTO ошибок** — `NoteError`, `TodoError` (pydantic `kw_only=True`), по образцу `Reminder | ParseError`.
3. **Простой парсинг срока задачи** — `parse_todo_input`: `strptime("ГГГГ-ММ-ДД ЧЧ:ММ")` → локализация через `timezone` → aware UTC; past **разрешён** (просроченная активная); без DST-валидации и горизонта.

## Implementation Order
Порядок leaves → root (зависимости через `Imports`):

1. **`src/remindme/db`** (modify) — нет зависимостей кроме `config` (не меняется). Лист; репозитории для note/todo — основа для `services` и `bot`.
2. **`src/remindme/services`** (modify) — зависит от `db` (`create_note`, `create_todo`). Импортирует типы `db`, поэтому реализуется после `db`.
3. **`src/remindme/bot`** (modify) — зависит от `services` и `db` (сценарии + repo-fns + модели). Реализуется после `services`.
4. **`src/remindme/main`** (modify) — зависит от `bot` (`register_handlers`). `set_commands` дополняется после реализации команд в `bot`.

## Artifacts

### Cell: src/remindme/db (modify)
**Действие:** расширить `CODEMANIFEST` восемью repo-функциями в `repositories.py`. Модели `Note`/`Todo` и Header — **без изменений**.

#### CODEMANIFEST — Header
Без изменений (`Imports` `Settings`←`config`; `Usages`: `conventions`, `sqlalchemy`, `aiosqlite`; `Annotations` без изменений).

#### CODEMANIFEST — Body (добавить в `repositories.py`)
```yaml
"create_note(session: AsyncSession, telegram_user_id: int, text: str, now: datetime) -> note: Note":
  location: repositories.py
  annotations: |
    Создаёт запись Note для пользователя.

    `session`: открытая AsyncSession
    `telegram_user_id`: владелец
    `text`: текст заметки (валидирован сценарием 1–500)
    `now`: aware UTC для created_at_utc
    `note`: созданная и зафиксированная запись

    Algorithm:
    1. Привести `now` к ISO 8601 UTC now_iso
    2. Создать Note(user_id=`telegram_user_id`, text=`text`, created_at_utc=now_iso)
    3. session.add + commit
    4. При SQLAlchemyError — rollback, structured-лог, re-raise
    5. Вернуть `note`

    Constraints:
    - откат при ошибке; пользовательский текст — централизованный handler этапа 3

    Use `sqlalchemy` (add/commit/rollback); `conventions` (ISO 8601, relative imports, structured logging).

"list_notes(session: AsyncSession, user_id: int) -> notes: list[Note]":
  location: repositories.py
  annotations: |
    Заметки текущего пользователя, новые сверху.

    `user_id`: владелец
    `notes`: список Note (≤20)

    Algorithm:
    1. select Note where user_id==`user_id`, order_by created_at_utc desc, limit 20
    2. Вернуть list(result.scalars())

    Constraints:
    - запрос только по id без user_id запрещён (изоляция по владельцу)

    Use `sqlalchemy` (select, scalars).

"delete_note(session: AsyncSession, note_id: int, user_id: int) -> deleted: bool":
  location: repositories.py
  annotations: |
    Физическое удаление заметки по (note_id, user_id).

    `deleted`: True если ровно одна запись удалена

    Algorithm:
    1. delete Note where id==`note_id` and user_id==`user_id`
    2. commit
    3. Вернуть result.rowcount == 1

    Constraints:
    - False при не-найдено/чужой; существование чужих не раскрывается

    Use `sqlalchemy` (delete, rowcount).

"create_todo(session: AsyncSession, telegram_user_id: int, text: str, due_at_utc: datetime | None, now: datetime) -> todo: Todo":
  location: repositories.py
  annotations: |
    Создаёт запись Todo (status="active"; срок может быть None или в прошлом).

    `due_at_utc`: aware UTC (или None — без срока); past допускается
    `todo`: созданная запись

    Algorithm:
    1. now → now_iso; due_at_utc → ISO 8601 UTC или None
    2. Создать Todo(user_id, text, due_at_utc, status="active", created_at_utc=now_iso)
    3. add + commit; rollback/re-raise при ошибке
    4. Вернуть `todo`

    Requirements:
    - status="active"; due_at_utc — ISO 8601 UTC строка или None

    Use `sqlalchemy`; `conventions`.

"list_todos(session: AsyncSession, user_id: int) -> todos: list[Todo]":
  location: repositories.py
  annotations: |
    Активные задачи: сначала со сроком (asc), затем без срока.

    Algorithm:
    1. select Todo where user_id==`user_id` and status=="active",
       order_by due_at_utc asc NULLS LAST, created_at_utc desc, limit 20
    2. Вернуть list(result.scalars())

    Requirements:
    - NULLS LAST (срочные раньше безсрочных); limit 20

    Constraints:
    - изоляция по user_id

    Use `sqlalchemy` (select, nullslast).

"list_completed(session: AsyncSession, user_id: int) -> todos: list[Todo]":
  location: repositories.py
  annotations: |
    Выполненные задачи текущего пользователя.

    Algorithm:
    1. select Todo where user_id==`user_id` and status=="completed",
       order_by completed_at_utc desc, limit 20
    2. Вернуть list(result.scalars())

    Use `sqlalchemy` (select).

"complete_todo(session: AsyncSession, todo_id: int, user_id: int, now: datetime) -> completed: bool":
  location: repositories.py
  annotations: |
    Переход active→completed с completed_at_utc.

    `now`: aware UTC для completed_at_utc
    `completed`: True если ровно одна active-запись завершена

    Algorithm:
    1. now → now_iso
    2. UPDATE Todo set status='completed', completed_at_utc=now_iso
       WHERE id==`todo_id` AND user_id==`user_id` AND status='active'
    3. commit
    4. Вернуть result.rowcount == 1

    Constraints:
    - повторный клик по уже completed → False; чужие записи не раскрываются

    Use `sqlalchemy` (update, rowcount).

"delete_todo(session: AsyncSession, todo_id: int, user_id: int) -> was_completed: bool | None":
  location: repositories.py
  annotations: |
    Удаление задачи по (todo_id, user_id); возвращает статус до удаления для перерисовки списка.

    `was_completed`: None = не найдено/чужая; True = была completed; False = была active

    Algorithm:
    1. Определить статус записи по (todo_id, user_id); если нет → вернуть None
    2. Удалить запись по (todo_id, user_id)
    3. commit
    4. Вернуть True/False по статусу до удаления

    Requirements:
    - статус до удаления управляет целевой перерисовкой (/completed vs /todos)

    Constraints:
    - чужие записи не раскрываются (None); best-effort для UI-перерисовки

    Use `sqlalchemy` (select, delete, rowcount).
```

#### .usages/ — `src/remindme/db/.usages/repositories.md` (modify — добавить разделы)
```md
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
```

---

### Cell: src/remindme/services (modify)
**Действие:** расширить `CODEMANIFEST` — добавить типы в `Imports` и 6 типов в Body (`notes.py`, `todos.py`). `Usages` без изменений.

#### CODEMANIFEST — Header (diff)
```yaml
Imports:
  - Types:
      - Reminder
      - create_reminder
      - cancel_reminder
      - set_user_timezone
      - CancelOutcome
      - Note               # new
      - Todo               # new
      - create_note        # new
      - create_todo        # new
    Usages:
      - repositories
    From: src/remindme/db

Usages:
  conventions: .goga/usages/conventions.md
  timezone: |
    Валидация и локализация времени через stdlib zoneinfo.  # без изменений

Annotations: |
  (существующая global без изменений) …
  Note/todo сценарии следуют тому же паттерну; `parse_todo_input` — простой парсер срока
  (past допускается, без DST/горизонта).
```

#### CODEMANIFEST — Body (добавить)
```yaml
"NoteError(kind: str)":
  location: notes.py
  annotations: |
    Ошибка note-сценария (DTO, pydantic kw_only=True). Producer — create_note_scenario.

    Requirements:
    - pydantic kw_only=True; kind ∈ {empty_text, text_too_long}

    Use `conventions` (kw_only).
  properties:
    "kind -> str": |
      empty_text / text_too_long.

"create_note_scenario(session: AsyncSession, telegram_user_id: int, raw: str, now: datetime) -> outcome: Note | NoteError":
  location: notes.py
  annotations: |
    Валидация текста 1–500 → create_note.

    `raw`: исходный текст; `now`: aware UTC.

    Algorithm:
    1. text = raw.strip()
    2. Пусто → NoteError(empty_text)
    3. len > 500 → NoteError(text_too_long)
    4. Иначе → `create_note`(session, telegram_user_id, text, now); вернуть Note

    Constraints:
    - при ошибке запись не создаётся; SQLAlchemyError откатывается в репозитории

    Use `repositories` (`create_note`); `conventions`.

"TodoError(kind: str)":
  location: todos.py
  annotations: |
    Ошибка todo-сценария (DTO, pydantic kw_only=True).

    Requirements:
    - kind ∈ {invalid_format, empty_text, text_too_long}

    Use `conventions` (kw_only).
  properties:
    "kind -> str": |
      invalid_format / empty_text / text_too_long.

"ParsedTodo(due_at_utc: datetime | None, text: str)":
  location: todos.py
  annotations: |
    Результат парсинга входа задачи.

    Requirements:
    - pydantic kw_only=True

    Use `conventions` (kw_only).
  properties:
    "due_at_utc -> datetime | None": |
      Aware UTC срок или None (без срока).
    "text -> str": |
      Очищенный текст задачи.

"parse_todo_input(raw: str, timezone: str) -> parsed: ParsedTodo | TodoError":
  location: todos.py
  annotations: |
    Чистый парсер входа задачи: «ГГГГ-ММ-ДД ЧЧ:ММ | текст» → срок; иначе без срока.

    `raw`: исходный текст; `timezone`: IANA-зона пользователя.

    Algorithm:
    1. Разделить raw по первому «|»: левая — срок, правая — текст; если «|» нет — левая пуста, текст = raw
    2. text = (правая или raw).strip(); пусто → TodoError(empty_text)
    3. len(text) > 500 → TodoError(text_too_long)
    4. Если «|» был (срок присутствует): распарсить левую как «ГГГГ-ММ-ДД ЧЧ:ММ» (strptime);
       неудача → TodoError(invalid_format); naive local → локализовать через ZoneInfo(`timezone`) → aware UTC due_at_utc
    5. Иначе due_at_utc=None (задача без срока)
    6. Вернуть ParsedTodo(due_at_utc, text)

    Requirements:
    - past допускается; без DST-валидации и горизонта
    - проверки empty_text/text_too_long применяются к обеим веткам (со сроком и без срока)

    Constraints:
    - чистая функция — без datetime.now()/БД/Telegram

    Use `timezone` (ZoneInfo локализация); `conventions` (kw_only, parametrize).

"create_todo_scenario(session: AsyncSession, telegram_user_id: int, raw: str, now: datetime, timezone: str) -> outcome: Todo | TodoError":
  location: todos.py
  annotations: |
    parse_todo_input → create_todo.

    Algorithm:
    1. parsed = `parse_todo_input`(raw, timezone)
    2. TodoError → вернуть без сохранения
    3. Иначе → `create_todo`(session, telegram_user_id, parsed.text, parsed.due_at_utc, now); вернуть Todo

    Constraints:
    - при ошибке парсинга запись не создаётся

    Use `parse_todo_input`; `repositories` (`create_todo`); `conventions`.
```

#### .usages/ — `src/remindme/services/.usages/notes.md` (new)
```md
# Сценарий заметки — создание

Домен: бизнес-сценарий заметки поверх репозитория.
Аудитория: клетка `bot` (`cmd_note`) и тесты.

Сценарий валидирует текст и сохраняет. Принимает открытую AsyncSession и now (aware UTC). Не знает о Telegram.

## Создание заметки

Возвращает созданный Note либо NoteError (без сохранения при ошибке валидации).

```python
from .services import create_note_scenario, NoteError

outcome = await create_note_scenario(
    session=session,
    telegram_user_id=user_id,
    raw=message.text,
    now=fixed_now,
)
if isinstance(outcome, NoteError):
    # kind: empty_text / text_too_long — показать текст отказа; запись не создана
    ...
else:
    note = outcome  # Note ORM
```

## Предусловия и побочные эффекты

- session открыта; фиксация транзакции — в `create_note` (репозиторий), откат при SQLAlchemyError — там же.
- now передаётся явно (aware UTC); datetime.now() внутри не вызывается.
- При NoteError запись не создаётся.
```

#### .usages/ — `src/remindme/services/.usages/todos.md` (new)
```md
# Сценарий задачи — создание со сроком

Домен: бизнес-сценарий задачи: парсер срока и сохранение.
Аудитория: клетка `bot` (`cmd_todo`) и тесты.

Сценарий оркестрирует parse_todo_input → create_todo. Принимает AsyncSession, now (aware UTC), timezone (IANA). Не знает о Telegram.

## Создание задачи

Возвращает созданный Todo либо TodoError (без сохранения при ошибке).

```python
from .services import create_todo_scenario, TodoError

outcome = await create_todo_scenario(
    session=session,
    telegram_user_id=user_id,
    raw=message.text,        # "2026-06-25 18:00 | Отчёт" или "Купить хлеб"
    now=fixed_now,
    timezone=user.timezone,
)
if isinstance(outcome, TodoError):
    # kind: invalid_format / empty_text / text_too_long
    ...
else:
    todo = outcome  # Todo ORM (status="active")
```

## Формат срока

- «ГГГГ-ММ-ДД ЧЧ:ММ | текст» → задача со сроком (aware UTC после локализации через `timezone`).
- Текст без «|» → задача без срока (due_at_utc=None).
- Срок в прошлом допускается (просроченная активная); доставки нет (у задач нет worker).
- Без DST-валидации и горизонта (отличается от parse_remind_time).

## Предусловия и побочные эффекты

- session открыта; фиксация — в `create_todo` (репозиторий).
- now передаётся явно (aware UTC).
- При TodoError запись не создаётся.
```

---

### Cell: src/remindme/bot (modify)
**Действие:** расширить `CODEMANIFEST` — добавить типы/usages в `Imports`, 17 новых типов + modify 2 (`cmd_reminders`, `register_handlers`). Новые модули `callbacks.py`, `keyboards.py`. `Usages` без изменений.

#### CODEMANIFEST — Header (diff)
```yaml
Imports:
  - Types: [get_settings]
    Usages: [settings]
    From: src/remindme/config
  - Types:
      - User
      - Reminder
      - list_reminders
      - CancelOutcome
      - Note                 # new
      - Todo                 # new
      - list_notes           # new
      - list_todos           # new
      - list_completed       # new
      - complete_todo        # new
      - delete_note          # new
      - delete_todo          # new
    Usages: [models, repositories]
    From: src/remindme/db
  - Types:
      - create_reminder_scenario
      - ParseError
      - set_timezone_scenario
      - cancel_reminder_scenario
      - create_note_scenario   # new
      - create_todo_scenario   # new
      - NoteError              # new
      - TodoError              # new
    Usages: [reminders, parser, notes, todos]
    From: src/remindme/services

Usages:
  conventions: .goga/usages/conventions.md
  aiogram: .goga/usages/cooks/aiogram.md
  sqlalchemy: .goga/usages/cooks/sqlalchemy.md

Annotations: |
  (существующая global без изменений) …
  Этап 4: новые команды /note /notes /todo /todos /completed и единый callback-роутер
  handle_callback (<action>:<entity>:<id>); проверка владельца по (id, user_id) на каждом
  callback-действии; после действия список обновляется через editMessageText (перерисовка по статусу записи).
```

#### CODEMANIFEST — Body (добавить + modify)
```yaml
"cmd_note(message: Message)":
  location: handlers.py
  annotations: |
    Обработчик /note → создание заметки.

    Algorithm:
    1. Сессия; user=ensure_user(...)
    2. raw=аргумент /note
    3. outcome=`create_note_scenario`(session, user.telegram_user_id, raw, now)
    4. NoteError → `format_note_error`; иначе → `format_note_confirmation`

    Use `aiogram` (Command, message.answer); `notes`; ensure_user.

"cmd_notes(message: Message)":
  location: handlers.py
  annotations: |
    Список заметок + inline-«Удалить».

    Algorithm:
    1. Сессия; user=ensure_user
    2. notes=`list_notes`
    3. text=`format_note_list`(notes, user.timezone); kb=`notes_keyboard`([n.id…])
    4. message.answer(text, reply_markup=kb)

    Use `repositories` (`list_notes`); `format_note_list`; `notes_keyboard`; `aiogram`.

"cmd_todo(message: Message)":
  location: handlers.py
  annotations: |
    Обработчик /todo → создание задачи.

    Algorithm:
    1. Сессия; user=ensure_user
    2. raw=аргумент /todo
    3. outcome=`create_todo_scenario`(session, user.telegram_user_id, raw, now, user.timezone)
    4. TodoError → `format_todo_error`; иначе → `format_todo_confirmation`(outcome, user.timezone)

    Use `todos`; ensure_user.

"cmd_todos(message: Message)":
  location: handlers.py
  annotations: |
    Активные задачи + «Выполнено»/«Удалить».

    Algorithm:
    1. Сессия; user=ensure_user
    2. todos=`list_todos`
    3. text=`format_todo_list`; kb=`todos_keyboard`([t.id…])
    4. message.answer(text, reply_markup=kb)

    Use `list_todos`; `format_todo_list`; `todos_keyboard`.

"cmd_completed(message: Message)":
  location: handlers.py
  annotations: |
    Выполненные задачи + «Удалить».

    Algorithm:
    1. Сессия; user=ensure_user
    2. todos=`list_completed`
    3. text=`format_completed_list`; kb=`completed_keyboard`([t.id…])
    4. message.answer(text, reply_markup=kb)

    Use `list_completed`; `format_completed_list`; `completed_keyboard`.

"cmd_reminders(message: Message)":
  location: handlers.py
  annotations: |
    (modify) /reminders — добавить inline-«Удалить».

    Algorithm (изменение):
    1-3. (существующие шаги: сессия, user=ensure_user, reminders=`list_reminders`)
    4. kb=`reminders_keyboard`([r.id for r in reminders])
    5. message.answer(`format_reminder_list`(reminders, user.timezone), reply_markup=kb)

    Use `list_reminders`; `format_reminder_list`; `reminders_keyboard`.

"handle_callback(callback: CallbackQuery)":
  location: callbacks.py
  annotations: |
    Единый callback-роутер: разбор action:entity:id, проверка владельца, действие, editMessageText.

    `callback`: CallbackQuery от inline-кнопки

    Algorithm:
    1. now=aware UTC (один раз)
    2. Разобрать `callback.data` по «:»; если формат не `<action>:<entity>:<id>` (не 3 части или `raw_id` не int) →
       callback.answer("Запись уже изменена или удалена.") и выйти без обращения к БД;
       иначе record_id=int(raw_id), user_id=callback.from_user.id
    3. Сессия (session_factory)
    4. Ветвление action+entity:
       - complete:todo → done=`complete_todo`(session, record_id, user_id, now); not done →
         callback.answer("Запись уже изменена или удалена."); иначе edit_text(format_todo_list+list_todos, todos_keyboard)
       - delete:note → deleted=`delete_note`; not deleted → "Запись уже изменена…"; иначе
         edit_text(format_note_list+list_notes, notes_keyboard)
       - delete:todo → was=`delete_todo`; None → "Запись уже изменена…"; иначе was True →
         (format_completed_list+list_completed, completed_keyboard); was False → (format_todo_list+list_todos, todos_keyboard)
       - delete:reminder → outcome=`cancel_reminder_scenario`(session, user_id, record_id);
         already_sending → callback.answer(текст этапа 3 «уже отправляется»);
         not_found → "Запись уже изменена…"; cancelled → edit_text(format_reminder_list+list_reminders, reminders_keyboard)
    5. На успех — callback.answer()

    Constraints:
    - некорректный формат callback_data (не 3 части / нечисловой id) → answerCallbackQuery "Запись уже изменена или удалена." без обращения к БД
    - проверка владельца через (record_id, user_id) в репозиториях; устаревшая/чужая кнопка →
      answerCallbackQuery "Запись уже изменена или удалена."; чужие данные не раскрываются

    Use `complete_todo`/`delete_note`/`delete_todo`/`cancel_reminder_scenario`; `list_*`; `format_*`;
    `*_keyboard`; `aiogram` (callback.answer, callback.message.edit_text).

"notes_keyboard(note_ids: list[int]) -> kb: InlineKeyboardMarkup":
  location: keyboards.py
  annotations: |
    Inline-клавиатура: ряд «Удалить» (delete:note:{id}) на каждую заметку; пустой список → None.

    Constraints:
    - callback_data = <action>:<entity>:<id> (≤64 байт); чистый билдер без БД

    Use `aiogram` (InlineKeyboardMarkup, InlineKeyboardButton).

"todos_keyboard(todo_ids: list[int]) -> kb: InlineKeyboardMarkup":
  location: keyboards.py
  annotations: |
    Ряд «Выполнено»(complete:todo:{id}) + «Удалить»(delete:todo:{id}) на каждую активную; пустой → None.

    Use `aiogram`.

"completed_keyboard(todo_ids: list[int]) -> kb: InlineKeyboardMarkup":
  location: keyboards.py
  annotations: |
    Ряд «Удалить»(delete:todo:{id}) на каждую выполненную; пустой → None.

    Use `aiogram`.

"reminders_keyboard(reminder_ids: list[int]) -> kb: InlineKeyboardMarkup":
  location: keyboards.py
  annotations: |
    Ряд «Удалить»(delete:reminder:{id}) на каждое напоминание; пустой → None.

    Use `aiogram`.

"format_note_confirmation(note: Note) -> text: str":
  location: formatter.py
  annotations: |
    Подтверждение создания заметки (note.text).

    Use `models` (Note).

"format_note_list(notes: list[Note], timezone: str) -> text: str":
  location: formatter.py
  annotations: |
    Список заметок (локальное created_at + текст) либо «нет заметок».

    Algorithm:
    1. Пуст → «нет заметок»
    2. Иначе для каждой: `to_local_string`(created_at_utc, timezone) + text

    Use `to_local_string`; `models`.

"format_note_error(error: NoteError) -> text: str":
  location: formatter.py
  annotations: |
    Текст отказа по kind: empty_text / text_too_long.

    Use `notes` (NoteError).

"format_todo_confirmation(todo: Todo, timezone: str) -> text: str":
  location: formatter.py
  annotations: |
    Подтверждение: текст + срок (to_local_string(due_at_utc) или «без срока»).

    Use `to_local_string`; `models`.

"format_todo_list(todos: list[Todo], timezone: str) -> text: str":
  location: formatter.py
  annotations: |
    Активные задачи (срок локально / «без срока» + текст; просроченные видны) либо «нет задач».

    Use `to_local_string`; `models`.

"format_completed_list(todos: list[Todo], timezone: str) -> text: str":
  location: formatter.py
  annotations: |
    Выполненные (to_local_string(completed_at_utc) + текст) либо «нет выполненных».

    Use `to_local_string`; `models`.

"format_todo_error(error: TodoError) -> text: str":
  location: formatter.py
  annotations: |
    Текст отказа по kind: invalid_format (пример «ГГГГ-ММ-ДД ЧЧ:ММ | текст») / empty_text / text_too_long.

    Use `todos` (TodoError).

"register_handlers(dp: Dispatcher, session_factory: AsyncSessionFactory)":
  location: handlers.py
  annotations: |
    (modify) Зарегистрировать cmd_note/cmd_notes/cmd_todo/cmd_todos/cmd_completed (PrivateOnly + Command)
    и handle_callback через dp.callback_query() (замкнув session_factory).

    Constraints:
    - командные раньше текстовых; session_factory переиспользуется

    Use `aiogram` (Dispatcher, Command, callback_query).
```

#### .usages/ — `src/remindme/bot/.usages/callbacks.md` (new)
```md
# Callbacks — inline-управление заметками, задачами, напоминаниями

Домен: единый callback-роутер inline-кнопок `<action>:<entity>:<id>` с проверкой владельца.
Аудитория: клетка `main` (регистрация через `register_handlers`) и будущие расширения inline-управления.

Inline-кнопки крепятся к сообщению-списку целиком (ряд на запись). Формат callback_data: `complete:todo:<id>`, `delete:note:<id>`, `delete:todo:<id>`, `delete:reminder:<id>`.

```python
from aiogram.types import CallbackQuery

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    action, entity, raw_id = callback.data.split(":")
    record_id = int(raw_id)
    user_id = callback.from_user.id
    # действие через репозиторий/сценарий с фильтром (record_id, user_id)
    # устаревшая/чужая кнопка → callback.answer("Запись уже изменена или удалена.")
    # успех → callback.message.edit_text(...обновлённый список..., reply_markup=kb)
    await callback.answer()
```

## Перерисовка списка после действия

Целевой список для `editMessageText` определяется по статусу записи (не хранится в callback_data):
- `complete:todo`, `delete:todo` (was active) → `/todos`; `delete:todo` (was completed) → `/completed`.
- `delete:note` → `/notes`; `delete:reminder` → `/reminders`.
- `delete:reminder` при `already_sending` → текст этапа 3; при `not_found` → «Запись уже изменена или удалена.»

## Предусловия и побочные эффекты

- Проверка владельца — на стороне репозитория/сценария по `(record_id, user_id)`; callback не доверяет `id` без `user_id`.
- `now` (aware UTC) фиксируется один раз — для `complete_todo` (completed_at_utc).
- Чужие записи не раскрываются (единый ответ «Запись уже изменена или удалена.»).
```

#### .usages/ — `src/remindme/bot/.usages/handlers.md` (modify — добавить раздел)
```md
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
```

#### .usages/ — `src/remindme/bot/.usages/formatter.md` (modify — добавить раздел)
```md
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
```

---

### Cell: src/remindme/main (modify)
**Действие:** модифицировать `set_commands` (добавить 5 команд в `set_my_commands`); обновить global-строку про инкрементальное меню.

#### CODEMANIFEST — Header (diff)
```yaml
Annotations: |
  (существующая global) … Меню команд формируется инкрементально — на этапе 4 добавлены
  /note /notes /todo /todos /completed (ранее /timezone, /cancel на этапе 3).
```

#### CODEMANIFEST — Body (modify `set_commands`)
```yaml
"set_commands(bot: Bot)":
  location: main.py
  annotations: |
    Регистрирует меню команд. Инкрементально: этап 1 (/start,/help,/remind,/reminders),
    этап 3 (/timezone,/cancel), этап 4 (/note,/notes,/todo,/todos,/completed).

    `bot`: экземпляр Bot

    Algorithm:
    1. Вызвать bot.set_my_commands со списком BotCommand для /start,/help,/remind,/reminders,
       /timezone,/cancel,/note,/notes,/todo,/todos,/completed

    Requirements:
    - Регистрируются только команды, реализованные на текущем этапе

    Use `aiogram` (set_my_commands, BotCommand).
```

#### .usages/
Без изменений (`running.md` не затрагивается; состав команд зафиксирован в `aiogram` cook).

## Dependency Map
```
config ─────────────────────────────────────────────┐
   │                                                 │
   ▼                                                 │
  db ◄──── services ◄──── bot ────────────────►  main
 [+8 repo]  [+notes.py   [+handlers/callbacks/      [modify
 Note,Todo   +todos.py]   keyboards/formatter]      set_commands]
 models      NoteError,   handle_callback,
 unchanged   TodoError,   4 keyboards,
             ParsedTodo,  5 cmds + 7 fmts
             scenarios
```
Импорты однонаправленные; **циклов и кросс-импортов нет**. Порядок leaves→root: `config → db → services → bot → main`.

## Verification Checklist
**После каждого артефакта:**
- **db CODEMANIFEST** — `goga lint` (DSL валиден); после имплементации: `ruff check src/remindme/db/`; facade `python -c "from remindme.db import create_note, list_notes, delete_note, create_todo, list_todos, list_completed, complete_todo, delete_todo"`; `pytest tests/db/` (note/todo: изоляция по владельцу, rowcount, NULLS LAST, past-срок).
- **db/.usages/repositories.md** — имена примеров совпадают с сигнатурами CODEMANIFEST.
- **services CODEMANIFEST** — `goga lint`; facade `from remindme.services import create_note_scenario, create_todo_scenario, parse_todo_input, NoteError, TodoError, ParsedTodo`; `pytest tests/services/` (валидация 1–500; парсинг срока с/без «|»; past; kw_only).
- **services/.usages/notes.md, todos.md** — примеры соответствуют сигнатурам.
- **bot CODEMANIFEST** — `goga lint`; facade `from remindme.bot import cmd_note, cmd_notes, cmd_todo, cmd_todos, cmd_completed, handle_callback, notes_keyboard, todos_keyboard, completed_keyboard, reminders_keyboard, format_note_confirmation, format_note_list, format_note_error, format_todo_confirmation, format_todo_list, format_completed_list, format_todo_error`; `pytest tests/bot/` (callback-роутер: owner-check, перерисовка по статусу, устаревшая кнопка → «Запись уже изменена или удалена.»; keyboards callback_data формат; formatter).
- **bot/.usages/callbacks.md, handlers.md, formatter.md** — соответствие контрактам.
- **main CODEMANIFEST** — `goga lint`; `set_commands` содержит 11 BotCommand; `pytest` (main).
- **Общее (после всех ячеек):**
  - `goga schema` — иерархия корректна, новых циклов нет, новые типы видны в `types`.
  - `goga lint` (весь проект) — DSL всех изменённых CODEMANIFEST валиден.
  - `ruff check src/` — стиль.
  - `pytest tests/ -x` — все тесты зелёные.
  - Проверка acceptance-критериев этапа 4 (см. Acceptance Criteria Check в CELL_ASSEMBLY_REPORT): управление задачей, изоляция данных/списков, валидация /note, /todo срок/без срока/просроченная, повторный клик, editMessageText, удаление reminder (cancelled/sending).
