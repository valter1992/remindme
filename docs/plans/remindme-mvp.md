# Plan: `remindme-mvp`

## Purpose

Greenfield-реализация Telegram-бота **RemindMe** — напоминалки с заметками и задачами — по всей
поверхности CODEMANIFEST шести клеток. Контракты есть, кода (`.py`) пока нет.

После реализации пакет `remindme` должен предоставлять:
- **config** — `Settings`/`get_settings` (pydantic-settings, валидация при первом вызове).
- **db** — ORM-модели (SQLAlchemy 2.x async), async-движок SQLite с PRAGMA, репозиторий (CRUD, статусные
  переходы, повторы 30/120/600, восстановление зависших `sending`) с изоляцией по владельцу `(id, user_id)`.
- **services** — чистые парсеры (`parse_remind_time` с DST-валидацией, терпимый `parse_todo_input`) и сценарии.
- **bot** — обработчики aiogram (только приватные чаты), форматтеры, inline-клавиатуры, callback-роутер,
  централизованный errors-handler БД.
- **worker** — фоновый цикл доставки с атомарным захватом и эскалацией до `failed`.
- **main** — точка входа: in-process alembic-миграции до polling, сборка инфраструктуры, запуск worker ∥ polling.

Главные пробелы контракт↔код: кода нет совсем. Стратегия — **сборка строго снизу вверх**
(`config → db → services → bot → worker → main`), каждая клетка реализуется полностью до перехода к следующей;
внутри клетки — инфраструктура → каркасы сущностей → поведение; каждая coding-задача идёт по TDD
(контракт-тесты → код → верификация интерфейса → логика → отладка → повторная контракт-верификация → lint).

Принцип «явного времени»: `now` (aware UTC) — всегда параметр; `datetime.now()` допустим только в handler'ах
(одно на сообщение) и в цикле worker. В парсерах, сценариях, репозиториях и `ensure_user` — никогда.

## Context

### Contract Surface

> Все `location`-пути указаны относительно директории клетки: `src/remindme/<cell>/<location>`.
> Фасад клетки — `src/remindme/<cell>/__init__.py` с `__all__`. В CODEMANIFEST **нет** блоков реэкспорта
> (`->Name: {}`) — фасад каждой клетки экспонирует только собственные сущности.

#### Cell: `config` — `src/remindme/config/config.py`

**Entity: `Settings`** — class, `location: config.py`, импортируем из `remindme.config`.
- Свойства (с дефолтами/валидаторами): `TELEGRAM_BOT_TOKEN -> SecretStr` (обязательный),
  `DATABASE_URL -> str` (по умолчанию `sqlite+aiosqlite:///data/remindme.db`),
  `DEFAULT_TIMEZONE -> str` (по умолчанию `Europe/Moscow`, `validate_timezone`),
  `DEFAULT_REMINDER_TIME -> str` (по умолчанию `09:00`, `validate_reminder_time`),
  `REMINDER_POLL_INTERVAL_SECONDS -> int` (по умолчанию `5`, `validate_poll_interval`),
  `LOG_LEVEL -> str` (по умолчанию `INFO`).
- Методы-валидаторы (статические, через `@field_validator`):
  `validate_timezone(value) -> str` (в `available_timezones()` + `ZoneInfo`),
  `validate_reminder_time(value) -> str` (`ЧЧ:ММ`, 0..23/0..59),
  `validate_poll_interval(value) -> int` (`> 0` иначе `ValueError`).
- Семантика: `BaseSettings` + `SettingsConfigDict(env_file=".env")`; env приоритетнее `.env`; класс pydantic
  `kw_only=True`. Запуск без токена/с неизвестной зоной/неверным временем → понятный `ValidationError` без токена.

**Routine: `get_settings() -> Settings`** — `location: config.py`. Ленивый синглтон (модульная переменная /
`functools.cache`): первый вызов создаёт и валидирует, последующие — закешированный экземпляр.

- Импорт-зависимости: нет (листовой cell).
- Usages: `conventions`, `pydantic-settings` (`.goga/usages/cooks/pydantic-settings.md`).
- Аннотации: токен как `SecretStr`, не попадает в repr/логи/ошибки; относительные импорты.

#### Cell: `db` — `models.py`, `session.py`, `repositories.py`

**Imports**: тип `Settings` из `src/remindme/config`. Usages (project): `conventions`, `sqlalchemy`, `aiosqlite`.

**Entity: `Base`** — `location: models.py`. `DeclarativeBase`; свойство `metadata -> MetaData` (реестр таблиц).

**Entity: `User`** — `models.py`. `telegram_user_id: int` (PK + UNIQUE), `timezone: str` (NOT NULL, String(64)),
`created_at_utc: str`, `updated_at_utc: str` (NOT NULL ISO 8601).

**Entity: `Reminder`** — `models.py`. `id` (PK autoincrement), `user_id` (NOT NULL FK → `users.telegram_user_id`),
`text` (NOT NULL), `remind_at_utc` (NOT NULL ISO), `status` (NOT NULL: `scheduled|sending|sent|cancelled|failed`),
`attempt_count` (NOT NULL, default 0), `next_attempt_at_utc` (NOT NULL ISO), `locked_at_utc` (nullable),
`created_at_utc` (NOT NULL ISO), `sent_at_utc` (nullable). Все timestamp-поля — `Text` ISO 8601 UTC.

**Entity: `Note`** — `models.py`. `id`, `user_id` FK, `text` (NOT NULL), `created_at_utc` (NOT NULL ISO).

**Entity: `Todo`** — `models.py`. `id`, `user_id` FK, `text` (NOT NULL), `due_at_utc` (nullable),
`status` (NOT NULL: `active|completed`), `created_at_utc` (NOT NULL ISO), `completed_at_utc` (nullable).

**Routine: `create_engine(settings: Settings) -> AsyncEngine`** — `session.py`. `create_async_engine(DATABASE_URL)`,
создать `data/`, listener `connect` → `PRAGMA foreign_keys=ON`, `PRAGMA journal_mode=WAL`.

**Routine: `create_session_factory(engine: AsyncEngine) -> factory`** — `session.py`. `async_sessionmaker(..., expire_on_commit=False)`.

**Routine: `create_reminder(session, telegram_user_id, text, remind_at_utc: datetime, now: datetime) -> Reminder`** — `repositories.py`.
datetime→ISO; `status="scheduled"`, `attempt_count=0`, `next_attempt_at_utc=remind_at_utc`; add+commit;
`SQLAlchemyError`→rollback+structured-лог+re-raise.

**Routine: `list_reminders(session, user_id) -> list[Reminder]`** — `repositories.py`. `status="scheduled"`,
`order_by remind_at_utc asc`, `limit 20`; только по `(user_id)`.

**Entity: `CancelOutcome(kind: str)`** — `repositories.py`. pydantic `kw_only=True`; свойство `kind` ∈ `cancelled/not_found/already_sending`.

**Routine: `cancel_reminder(session, reminder_id, user_id) -> CancelOutcome`** — `repositories.py`. `UPDATE … WHERE id AND user_id AND status='scheduled'`;
`rowcount==1`→`cancelled`; иначе доп. `SELECT status`: `'sending'`→`already_sending`, иначе→`not_found`.

**Routine: `set_user_timezone(session, telegram_user_id, timezone, now) -> bool`** — `repositories.py`. `UPDATE User … WHERE telegram_user_id`; `rowcount==1`.

**Routine: `create_note(session, telegram_user_id, text, now) -> Note`** — `repositories.py`. ISO `created_at_utc`; add+commit; rollback/re-raise.

**Routine: `list_notes(session, user_id) -> list[Note]`** — `repositories.py`. `order_by created_at_utc desc`, `limit 20`.

**Routine: `delete_note(session, note_id, user_id) -> bool`** — `repositories.py`. `DELETE WHERE id AND user_id`; `rowcount==1`.

**Routine: `create_todo(session, telegram_user_id, text, due_at_utc: datetime | None, now) -> Todo`** — `repositories.py`. `status="active"`; `due_at_utc` ISO или `None`; past допускается.

**Routine: `list_todos(session, user_id) -> list[Todo]`** — `repositories.py`. `status="active"`,
`order_by due_at_utc asc NULLS LAST, created_at_utc desc`, `limit 20`.

**Routine: `list_completed(session, user_id) -> list[Todo]`** — `repositories.py`. `status="completed"`, `order_by completed_at_utc desc`, `limit 20`.

**Routine: `complete_todo(session, todo_id, user_id, now) -> bool`** — `repositories.py`. `UPDATE … SET status='completed', completed_at_utc WHERE id AND user_id AND status='active'`; `rowcount==1`.

**Routine: `delete_todo(session, todo_id, user_id) -> bool | None`** — `repositories.py`. `None`=не найдено/чужая, `True`=была completed, `False`=была active.

**Routine: `find_due_reminders(session, now) -> list[Reminder]`** — `repositories.py`. `status="scheduled" AND next_attempt_at_utc <= now_iso`; без перевода статуса.

**Routine: `claim_for_sending(session, reminder_id, now) -> bool`** — `repositories.py`. `UPDATE … SET status='sending', locked_at_utc WHERE id AND status='scheduled'`; `rowcount==1`.

**Routine: `mark_sent(session, reminder_id, now)`** — `repositories.py`. `UPDATE … SET status='sent', sent_at_utc WHERE id`.

**Routine: `mark_failed(session, reminder_id) -> bool`** — `repositories.py`. `UPDATE … SET status='failed' WHERE id AND status='sending'`; `rowcount==1`. Не инкрементит `attempt_count`.

**Routine: `record_send_failure(session, reminder_id, now) -> bool`** — `repositories.py`. `SELECT attempt_count`; `new_count+1`; `>=4`→`mark_failed`(вернуть `True`);
иначе `delay={1:30,2:120,3:600}[new_count]`, `UPDATE status='scheduled', attempt_count=new_count, next_attempt_at_utc=now+delay, locked_at_utc=NULL`; вернуть `False`.

**Routine: `recover_stuck_sending(session, now, stale_after_seconds=60) -> int`** — `repositories.py`. `threshold=now-60s`; `UPDATE status='scheduled', locked_at_utc=NULL WHERE status='sending' AND locked_at_utc<=threshold`; `rowcount`.

- Семантика общая: изоляция по `(id, user_id)` (запрос только по `id` запрещён); `SQLAlchemyError`→rollback+re-raise; ISO 8601 UTC как `Text`.
- Usages: `conventions`, `sqlalchemy`, `aiosqlite`.

#### Cell: `services` — `parser.py`, `reminders.py`, `notes.py`, `todos.py`

**Imports**: из `src/remindme/db` — типы `Reminder, create_reminder, cancel_reminder, set_user_timezone, CancelOutcome, Note, Todo, create_note, create_todo` + usage `repositories`.
Usages (project): `conventions`, `timezone` (inline — DST через `zoneinfo` fold=0/fold=1).

**Routine: `parse_remind_time(raw, now: datetime, timezone, default_time) -> ParsedReminder | ParseError`** — `parser.py`. Чистый детерминированный.
Режимы: `/remind` (split по первому `|`), фраза «напомни [мне]» (5 шаблонов `re.IGNORECASE`, частное→общее). `strptime` (31.02→`date_not_exist`).
Локализация naive→`ZoneInfo`: fold-расхождение→`nonexistent_time`/`ambiguous_time`; иначе aware UTC.
Проверки: пустой→`empty_text`; `>500`→`text_too_long`; `<=now`→`past`; `>365 дней`→`horizon_exceeded`; не распознано→`invalid_format`.
**Constraint**: DST-проверка ДО past/horizon.

**Entity: `ParsedReminder(remind_at_utc: datetime, text: str)`** / **`ParseError(kind: str)`** — `parser.py`. pydantic `kw_only`.
`ParseError.kind` ∈ `invalid_format/date_not_exist/past/horizon_exceeded/empty_text/text_too_long/nonexistent_time/ambiguous_time`.

**Routine: `create_reminder_scenario(session, telegram_user_id, raw, now, timezone, default_time) -> Reminder | ParseError`** — `reminders.py`. parse→(ParseError вернуть без сохранения)→`create_reminder`.

**Routine: `set_timezone_scenario(session, telegram_user_id, timezone, now) -> bool`** — `reminders.py`. `ZoneInfo(timezone)` (ошибка→`False` без БД)→`set_user_timezone`.

**Routine: `cancel_reminder_scenario(session, telegram_user_id, reminder_id) -> CancelOutcome`** — `reminders.py`. Делегирует `cancel_reminder`.

**Entity: `NoteError(kind: str)`** (`empty_text/text_too_long`) — `notes.py`.
**Routine: `create_note_scenario(session, telegram_user_id, raw, now) -> Note | NoteError`** — `notes.py`. `strip`; пусто→`empty_text`; `>500`→`text_too_long`; иначе `create_note`.

**Entity: `TodoError(kind: str)`** (`empty_text/text_too_long`) / **`ParsedTodo(due_at_utc: datetime | None, text: str)`** — `todos.py`.
**Routine: `parse_todo_input(raw, timezone) -> ParsedTodo | TodoError`** — `todos.py`. Терпимый: `|` разделяет срок ТОЛЬКО при валидной дате слева (`ГГГГ-ММ-ДД ЧЧ:ММ` через `strptime`); иначе весь `raw` (с литералом `|`) — текст без срока. `strip`; пусто→`empty_text`; `>500`→`text_too_long`. **Constraint**: past допускается; без DST/горизонта; чистая функция.
**Routine: `create_todo_scenario(session, telegram_user_id, raw, now, timezone) -> Todo | TodoError`** — `todos.py`. parse→(TodoError вернуть)→`create_todo`.

#### Cell: `bot` — `handlers.py`, `callbacks.py`, `formatter.py`, `keyboards.py`

**Imports**: из `config` — `get_settings` + usage `settings`; из `db` — `User, Reminder, list_reminders, CancelOutcome, Note, Todo, list_notes, list_todos, list_completed, complete_todo, delete_note, delete_todo` + usages `models, repositories`; из `services` — `create_reminder_scenario, ParseError, set_timezone_scenario, cancel_reminder_scenario, create_note_scenario, create_todo_scenario, NoteError, TodoError` + usages `reminders, parser, notes, todos`.
Usages (project): `conventions`, `aiogram`, `sqlalchemy`.

**Entity: `PrivateOnly`** — `handlers.py`. `BaseFilter`; метод `__call__(message) -> bool` (private→True).
**Routine: `ensure_user(session, telegram_user_id, timezone, now: datetime) -> User`** — `handlers.py`. Идемпотентное создание/обновление; конкурентная регистрация→`IntegrityError`→повторный `SELECT`. `now` прокинут.
**Routines (handlers.py)**: `cmd_start`, `cmd_help`, `handle_unknown_command`, `handle_text`, `handle_group` (без фильтра последним),
`cmd_remind`, `cmd_reminders`, `cmd_timezone`, `cmd_cancel`, `cmd_note`, `cmd_notes`, `cmd_todo`, `cmd_todos`, `cmd_completed`,
`handle_remind_phrase` (раньше `handle_text`), `handle_db_error(event: ErrorEvent)` (централизованный: только `SQLAlchemyError`, лог ERROR без токена/текста, единый ответ),
`register_handlers(dp, session_factory)` (фасад: командные→remind-фраза→`handle_text`→`handle_group` последним; `handle_callback` через `dp.callback_query`; `handle_db_error` как errors-handler с фильтром `SQLAlchemyError`).
**Routine: `handle_callback(callback)`** — `callbacks.py`. Разбор `action:entity:id`; некорректный payload→`callback.answer("Запись уже изменена или удалена.")` без БД; 4 ветки (`complete:todo`/`delete:note`/`delete:todo`/`delete:reminder`) с проверкой владельца `(id, user_id)` и перерисовкой списка через `edit_text`.
**Routines (formatter.py)**: `to_local_string(utc_iso, timezone) -> str` (`DD.MM.YYYY в HH:MM`), `format_notification(reminder) -> "Напоминание\n<text>"`,
`format_parse_error`, `format_cancel_outcome`, `format_reminder_confirmation` (с суффиксом `(IANA)`), `format_reminder_list`,
`format_note_confirmation/list/error`, `format_todo_confirmation/list/error`, `format_completed_list`.
**Routines (keyboards.py)**: `notes_keyboard`, `todos_keyboard`, `completed_keyboard`, `reminders_keyboard` (`callback_data=<action>:<entity>:<id>`, ≤64 байт; пустой список→`None`).

- Глобальная аннотация клетки: каждый пишущий handler первым шагом фиксирует aware-UTC `now` и передаёт его в `ensure_user` и далее.

#### Cell: `worker` — `notifications.py`

**Imports**: из `db` — `Reminder, find_due_reminders, claim_for_sending, mark_sent, record_send_failure, mark_failed, recover_stuck_sending` + usage `repositories`; из `bot` — `format_notification` + usage `formatter`.
Usages (project): `conventions`, `aiogram`.

**Routine: `run_reminder_worker(bot, session_factory, poll_interval)`** — `notifications.py`. Startup-recovery (1 раз, `recover_stuck_sending(now,60)`); бесконечный цикл: `sleep(poll_interval)`, одна сессия на итерацию, `find_due_reminders`→для каждого `claim_for_sending`→`format_notification`→`bot.send_message(chat_id=reminder.user_id)`; `TelegramForbiddenError`→`mark_failed`; иная `TelegramAPIError`→`record_send_failure`; успех→`mark_sent`. Токен и текст не логируются.

#### Cell: `main` — `main.py` (+ alembic-каркас)

**Imports**: из `config` — `get_settings` + usage `settings`; из `db` — `create_engine, create_session_factory` + usage `engine`; из `bot` — `register_handlers` + usage `handlers`; из `worker` — `run_reminder_worker` + usage `running`.
Usages (project): `conventions`, `aiogram`, `alembic`, `deployment`.

**Routine: `set_commands(bot)`** — `main.py`. `set_my_commands` для всех реализованных команд.
**Routine: `apply_migrations()`** — `main.py`. `alembic command.upgrade("head")` программно in-process (без CLI/subprocess); `DATABASE_URL` из `get_settings`; до polling; идемпотентен.
**Routine: `main()`** — `main.py`. `apply_migrations`→`get_settings`→engine/factory→`Bot`/`Dispatcher`→`register_handlers`→`set_commands`→`asyncio.create_task(run_reminder_worker(...))`→`delete_webhook(drop_pending_updates=True)`→`start_polling`.
Внутренний `__main__.py` (entry для `python -m remindme.main`) — `asyncio.run(main())`. Внутренний alembic-каркас: `alembic.ini`, `env.py` (async, `DATABASE_URL` из `get_settings`, `PRAGMA foreign_keys=ON` в online-контексте), `script.py.mako`, `versions/<initial>` (4 таблицы (`users`, `reminders`, `notes`, `todos`) с PK/UNIQUE/FK).

### Re-exports

В CODEMANIFEST всех клеток **нет блоков реэкспорта** (`->Name: {}`). Фасад каждой клетки
(`__init__.py` + `__all__`) экспонирует только собственные сущности. Импортируемые типы (`Imports.Types`)
используются как зависимости в сигнатурах и НЕ переэкспортируются. Работ по реэкспорту нет.

### Usages Context

- **`conventions`** (`.goga/usages/conventions.md`) — глобальная практика: Python 3.12+, `pyproject.toml`,
  относительные импорты, pydantic `kw_only=True`, `logging` structlog-стиль (`extra={...}`), Google-docstrings,
  тесты mirror src (`tests/<cell>/test_<module>.py`), ruff/pytest. Применяется во всех клетках.
- **`pydantic-settings`** (`.goga/usages/cooks/pydantic-settings.md`) — `BaseSettings`, `SecretStr`,
  `@field_validator`, `get_secret_value()`. Применяется в config.
- **`sqlalchemy`** (`.goga/usages/cooks/sqlalchemy.md`) — async ORM 2.x (`Mapped`/`mapped_column`),
  `create_async_engine`, `event.listens_for(engine.sync_engine,"connect")`, `select/update/delete`,
  `result.rowcount`, `nullslast`, `SQLAlchemyError`. Применяется в db, bot (errors-handler).
- **`aiosqlite`** (`.goga/usages/cooks/aiosqlite.md`) — async-драйвер SQLite, диалект `sqlite+aiosqlite`.
- **`aiogram`** (`.goga/usages/cooks/aiogram.md`) — Bot 3.x, Dispatcher, фильтры (`Command`, `BaseFilter`),
  `callback_query`, errors-handler, `set_my_commands`, `send_message`/`edit_text`/`callback.answer`,
  `TelegramForbiddenError`/`TelegramAPIError`. Применяется в bot, worker, main.
- **`alembic`** (`.goga/usages/cooks/alembic.md`) — `alembic init -t async`, `env.py`, программный
  `command.upgrade("head")`, `PRAGMA foreign_keys=ON` в online-контексте. Применяется в main.
- **`timezone`** (inline в services) — stdlib `zoneinfo`: `ZoneInfo(name)`, DST-детекция сравнением `utcoffset` fold=0/fold=1.
- **`deployment`** (`.goga/usages/deployment.md`) — Docker Compose (один сервис, том `data/`, `.env` через env_file без `COPY .env`). Применяется в main.
- **`pytest-asyncio`** (`.goga/usages/cooks/pytest-asyncio.md`) — `asyncio_mode=auto`, async-фикстуры. Применяется во всех тестах.

### Imported Usages (cross-cell)

- **services ← db**: `repositories` (`src/remindme/db/.usages/repositories.md`) — паттерны вызова
  `create_reminder`/`cancel_reminder`/`set_user_timezone` (изоляция по владельцу, ISO 8601, `rowcount`).
- **bot ← config**: `settings` (`src/remindme/config/.usages/settings.md`) — потребление `get_settings()`.
- **bot ← db**: `models` (`src/remindme/db/.usages/models.md`), `repositories` (`src/remindme/db/.usages/repositories.md`).
- **bot ← services**: `reminders`, `parser`, `notes`, `todos` (`src/remindme/services/.usages/*.md`) — сценарии и DTO.
- **worker ← db**: `repositories` (`src/remindme/db/.usages/repositories.md`).
- **worker ← bot**: `formatter` (`src/remindme/bot/.usages/formatter.md`) — `format_notification`.
- **main ← config**: `settings`; **main ← db**: `engine` (`src/remindme/db/.usages/engine.md`); **main ← bot**: `handlers`; **main ← worker**: `running`.

### Local Usages

Согласно дизайн-документу (раздел `.usages/ Update`), **все существующие `.usages/`-файлы актуальны**
и соответствуют текущему CODEMANIFEST (в т.ч. `services/.usages/todos.md` уже без `invalid_format` и с lenient-правилом `|`;
`bot/.usages/formatter.md` уже без `invalid_format`). **Новых файлов и правок не требуется** — задач по созданию/обновлению usages нет.

### External Dependencies

- **Сторонние библиотеки** (в `pyproject.toml`): `pydantic`, `pydantic-settings`, `sqlalchemy[asyncio]>=2`, `aiosqlite`,
  `aiogram>=3`, `alembic`. Каждой зависимости — минимальная версия.
- **Тестовые** (`[project.optional-dependencies].test`): `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`.
- **Инструменты/фреймворки**: ruff (lint+format), pytest (asyncio_mode=auto), alembic (in-process upgrade), stdlib `zoneinfo`.

## Facts

- Проект greenfield: нет `pyproject.toml`, нет `src/remindme/__init__.py`, нет `tests/` — каркас создаётся с нуля.
- Граф Imports ацикличный и проверен: `config → db → services → bot`; `worker → bot(db-форматтер) + db`; `main → config+db+bot+worker`.
- В CODEMANIFEST нет реэкспортов (`->Name:{}`) — фасад каждой клетки экспонирует только свои сущности через `__all__`.
- Все timestamp-поля БД — `Text` ISO 8601 UTC; сравнение `next_attempt_at_utc <= now_iso` лексикографически корректно при фиксированном формате.
- `now` (aware UTC) — параметр; `datetime.now()` только в handler'ах (одно на сообщение) и в цикле worker; нигде больше.
- Worker: одна итерация = одна сессия; recovery один раз при старте; Forbidden→`mark_failed` сразу; иные ошибки→`record_send_failure` (30/120/600, `failed` на 4-й).
- Изоляция по владельцу: любой доступ к пользовательской сущности — через `(id, user_id)`; поиск только по `id` запрещён.
- `SQLAlchemyError` откатывается и re-raise'ится в репозитории; пользовательский текст — только в `handle_db_error`; токен и текст записи никогда не логируются.
- `parse_todo_input` — терпимый: `|` разделяет срок только при валидной дате слева; иначе весь текст без срока (по ТЗ, не ошибка).

## Gap Analysis

- **Missing contract entities**: все сущности шести клеток (новые, greenfield).
- **Missing facade exposure**: ни у одной клетки нет `__init__.py` с `__all__`.
- **Incorrect `location` placement**: нет — `location` пока не реализованы.
- **API mismatches**: нет — контракт как источник истины.
- **Behavioral mismatches**: нет.
- **Existing code that can be reused**: нет кода; переиспользуются только `.usages/`-практики и `CODEMANIFEST`.
- **Test coverage gaps**: полное отсутствие тестов; `tests/conftest.py` и зеркальная структура `tests/<cell>/` отсутствуют.
- **Missing visibility**: нет `pyproject.toml`, venv не настроен под зависимости.

---

## Tasks

> **Package ordering rule**: coding tasks for each cell are completed before starting the next. Within each coding task, contract tests are written first (TDD workflow). The ralphex execution protocol (declaration → contract tests → implementation → interface verification → logic tests → debugging → contract re-verification → lint) is enforced by the checkbox structure of each coding task.

---

### Task 1: Project bootstrap (infrastructure)

Создать общий каркас проекта: `pyproject.toml` (src-layout, все зависимости + test-extras, ruff/pytest config),
виртуальное окружение, `.env.example`, корневой пакет `src/remindme/__init__.py`, тестовый корень `tests/__init__.py`
и базовый `tests/conftest.py` (общие фикстуры `fixed_now` aware-UTC и `bot` AsyncMock). Конфигурация: `asyncio_mode=auto`,
line-length по ruff-умолчанию, `[project.optional-dependencies].test`. Это enables все последующие фасад-чеки и pytest.

**Usages relevant to this task:**
- `conventions`: src-layout (`<src>/module/file.py` → `tests/module/test_file.py`), относительные импорты, `[project.optional-dependencies].test`, команды валидации (`pytest tests/ -x`, `ruff check src/`).

**CRITICAL: `CODEMANIFEST` files — read-only contract definitions. Do NOT modify them.**

- [x] Создать `pyproject.toml`: `[build-system]`, `[project]` (name=`remindme`, requires-python=`>=3.12`), `dependencies` (`pydantic`, `pydantic-settings`, `sqlalchemy[asyncio]>=2`, `aiosqlite`, `aiogram>=3`, `alembic`), `[project.optional-dependencies].test` (`pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`), `[tool.setuptools.packages.find] where=["src"]`, `[tool.pytest.ini_options] asyncio_mode="auto"`, `[tool.ruff]` (lint + format).
- [x] Создать `.env.example` с переменными `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `DEFAULT_TIMEZONE`, `DEFAULT_REMINDER_TIME`, `REMINDER_POLL_INTERVAL_SECONDS`, `LOG_LEVEL` и комментариями-дефолтами (без реального токена).
- [x] Создать корневой пакет `src/remindme/__init__.py` (минимальный docstring, `__all__: list[str] = []`).
- [x] Создать тестовый корень: `tests/__init__.py` и `tests/conftest.py` с фикстурой `fixed_now` (явный aware UTC, напр. `datetime(2026,6,24,12,0,0,tzinfo=timezone.utc)`) и `bot` (`AsyncMock` aiogram `Bot`).
- [x] Создать/активировать venv и установить пакет с test-extras: `python -m venv .venv && . .venv/bin/activate && pip install -e ".[test]"`.
- [x] Verify: `pytest tests/ -x` (пусто/0 тестов — без ошибок сбора) и `ruff check src/` (без ошибок).
- [x] Lint: `ruff format --check src/ tests/`.

---

### Task 2: `config` cell package scaffold (infrastructure)

Создать пакет `src/remindme/config/` с фасадом-оболочкой `__init__.py` (`__all__ = []` — наполняется в Task 3) и
тестовый пакет `tests/config/__init__.py`. Это даёт importability пакета до реализации сущностей.

**Usages relevant to this task:**
- `conventions`: тесты mirror src (`tests/config/test_config.py`), относительные импорты внутри клетки, `__init__.py` в каждой тест-директории.

**CRITICAL: `CODEMANIFEST` files — read-only.**

- [x] Создать `src/remindme/config/__init__.py` (docstring, `__all__: list[str] = []`).
- [x] Создать `tests/config/__init__.py`.
- [x] Verify: `python -c "import remindme.config"` (импорт пакета проходит; сущностей пока нет).
- [x] Lint: `ruff check src/remindme/config/`.

---

### Task 3: `config` — `Settings` + `get_settings` + validators

Реализовать `Settings` (pydantic `BaseSettings`, `kw_only=True`) и `get_settings` (синглтон) в `src/remindme/config/config.py`.
Покрывает контракты: `Settings` (6 свойств + 3 валидатора) и `get_settings`. Источник: `config/CODEMANIFEST`.
Семантика: env приоритетнее `.env`; валидация при первом `get_settings()`; `ValidationError` без токена.

**Usages relevant to this task:**
- `pydantic-settings`: `BaseSettings`, `SettingsConfigDict(env_file=".env")`, `SecretStr`, `@field_validator` (шаблоны `_valid_timezone`/`_valid_time`), `get_secret_value()`.
- `conventions`: pydantic `kw_only=True`, Google-docstrings, `logging.getLogger(__name__)`, относительные импорты.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: `Settings`/`get_settings` в `config.py`.**

- [x] **Task declaration**: работаем над Task 3 (`config` — Settings/get_settings).
- [x] **Contract tests** (`tests/config/test_config.py`): фасад `from remindme.config import Settings, get_settings`; форма API — `Settings(...)` принимает поля как kwargs; свойства `TELEGRAM_BOT_TOKEN: SecretStr`, `DATABASE_URL`, `DEFAULT_TIMEZONE`, `DEFAULT_REMINDER_TIME`, `REMINDER_POLL_INTERVAL_SECONDS: int`, `LOG_LEVEL`; сигнатуры трёх валидаторов; `get_settings() -> Settings`. (Ожидаемо падают — кода нет.)
- [x] **Code**: реализовать `Settings(BaseSettings)` с `model_config = SettingsConfigDict(env_file=".env", ...)`, полями с дефолтами и `@field_validator` на `DEFAULT_TIMEZONE` (`available_timezones()` + `ZoneInfo`, иначе `ValueError("unknown IANA timezone")`), `DEFAULT_REMINDER_TIME` (`ЧЧ:ММ`, 0..23/0..59, иначе `ValueError`), `REMINDER_POLL_INTERVAL_SECONDS` (`> 0`, иначе `ValueError`). Google-docstrings, `kw_only=True`.
- [x] **Code**: реализовать `get_settings()` — ленивый синглтон (модульная переменная/`functools.cache`); первый вызов создаёт и валидирует `Settings`.
- [x] **Code**: добавить `Settings`, `get_settings` в `src/remindme/config/__init__.py` (`__all__`).
- [x] **Interface verification**: `pytest tests/config/test_config.py -v` — контракт-тесты зелёные.
- [x] **Logic tests**: `test_settings_poll_interval_validation` (parametrize N ∈ {0, -1, -5} → `pytest.raises(ValidationError)`, текст без токена); `test_settings_unknown_timezone_rejected`; `test_settings_bad_reminder_time_rejected`; `test_settings_token_is_secretstr_and_absent_from_error` (при `ValidationError` без токена токен не в `str(exc)`); `test_settings_env_overrides_dotenv` (monkeypatch env); `test_get_settings_singleton` (повторный вызов — тот же объект).
- [x] **Debugging**: `pytest tests/config/test_config.py -x` — фиксить реализацию, пока все тесты не пройдут (тесты не править).
- [x] **Contract re-verification**: фасад `from remindme.config import Settings, get_settings`; форма API и поведения соответствуют `config/CODEMANIFEST`.
- [x] **Lint**: `ruff check src/remindme/config/` + `ruff format --check src/remindme/config/`.

---

### Task 4: `db` cell package scaffold (infrastructure)

Создать пакет `src/remindme/db/` (`__init__.py`, `__all__=[]`) и тестовый пакет `tests/db/__init__.py`. Это даёт
importability пакета до реализации сущностей. Тестовая фикстура `session` (зависящая от `Base`) создаётся в Task 5
после появления моделей — чтобы после каждой задачи проект оставался валидным (без ссылок на ещё несуществующие типы).

**Usages relevant to this task:**
- `conventions`: тесты mirror src (`tests/db/test_<module>.py`), `__init__.py` в каждой тест-директории, относительные импорты.

**CRITICAL: `CODEMANIFEST` files — read-only.**

- [x] Создать `src/remindme/db/__init__.py` (docstring, `__all__: list[str] = []`).
- [x] Создать `tests/db/__init__.py`.
- [x] Verify: `python -c "import remindme.db"`.
- [x] Lint: `ruff check src/remindme/db/`.

---

### Task 5: `db` — models (`Base`, `User`, `Reminder`, `Note`, `Todo`)

Реализовать ORM-модели в `src/remindme/db/models.py`. Покрывает контракты `Base`, `User`, `Reminder`, `Note`, `Todo`
(SQLAlchemy 2.x `Mapped`/`mapped_column`). Все timestamp-поля — `Text` ISO 8601 UTC; `User.telegram_user_id` PK+UNIQUE;
FK `user_id → users.telegram_user_id`. После появления `Base` задача также добавляет в `tests/conftest.py` общую
async-фикстуру `session` (она используется logic-тестами моделей и всеми последующими `db`/`services`/`bot` тестами).

**Usages relevant to this task:**
- `sqlalchemy`: `DeclarativeBase`, `Mapped`/`mapped_column`, `ForeignKey`, `String`, `Text`, `Integer`, `metadata`, `create_async_engine`, `event.listens_for` PRAGMA, `async_sessionmaker`.
- `aiosqlite`: async-движок на `tmp_path` (диалект `sqlite+aiosqlite`).
- `pytest-asyncio`: async-фикстуры (`asyncio_mode=auto`).
- `conventions`: относительные импорты, Google-docstrings, файлы через `tmp_path`.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: модели в `models.py`.**

- [x] **Task declaration**: Task 5 (`db` — models).
- [x] **Contract tests** (`tests/db/test_models.py`): фасад `from remindme.db import Base, User, Reminder, Note, Todo`; формы классов; наличие свойств (`telegram_user_id`, `timezone`, `status` и т.д.) как `Mapped`-колонок; `Base.metadata` содержит 4 таблицы (`users`, `reminders`, `notes`, `todos`); FK `reminders.user_id → users.telegram_user_id` (и для notes/todos).
- [x] **Code**: реализовать `Base(DeclarativeBase)`, `User`, `Reminder`, `Note`, `Todo` с колонками и типами строго по контракту (`Reminder.status`/`attempt_count`/`next_attempt_at_utc`/`locked_at_utc`/`sent_at_utc`; `Todo.due_at_utc`/`completed_at_utc` nullable; `attempt_count` default 0).
- [x] **Code**: добавить `Base, User, Reminder, Note, Todo` в `src/remindme/db/__init__.py` (`__all__`).
- [x] **Code**: добавить в `tests/conftest.py` async-фикстуру `session` (теперь `Base` определён): движок `create_async_engine("sqlite+aiosqlite:///"+tmp_path/"test.db")`, listener `connect` → `PRAGMA foreign_keys=ON`; `async_sessionmaker(..., expire_on_commit=False)`; setup `await conn.run_sync(Base.metadata.create_all)`; yield сессии; teardown drop.
- [x] **Interface verification**: `pytest tests/db/test_models.py -v`.
- [x] **Logic tests** (через фикстуру `session`): `test_user_pk_unique_integrity_error` (дубликат `telegram_user_id` → `IntegrityError`); `test_reminder_fk_missing_user_raises` (FK без `user_id` → `IntegrityError`); `test_reminder_status_scheduled_default` (создание → `status="scheduled"`, `attempt_count=0`); `test_todo_nullable_due_and_completed` (Todo без срока); `test_metadata_has_four_tables` (`len(Base.metadata.tables) == 4`).
- [x] **Debugging**: `pytest tests/db/test_models.py -x`.
- [x] **Contract re-verification**: свойства/типы колонок соответствуют `db/CODEMANIFEST`; `from remindme.db import Base, User, Reminder, Note, Todo`.
- [x] **Lint**: `ruff check src/remindme/db/`.

---

### Task 6: `db` — session (`create_engine`, `create_session_factory`)

Реализовать `create_engine(settings)` и `create_session_factory(engine)` в `src/remindme/db/session.py`.
`create_async_engine(DATABASE_URL)`; создать `data/`; listener `connect` → PRAGMA `foreign_keys=ON` + `journal_mode=WAL`;
`async_sessionmaker(..., expire_on_commit=False)`.

**Usages relevant to this task:**
- `sqlalchemy`: `create_async_engine`, `event.listens_for(engine.sync_engine, "connect")`, `async_sessionmaker`, `text` для PRAGMA.
- `aiosqlite`: URL-формат, работа с файлом БД/директорией.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: session.py.**

- [x] **Task declaration**: Task 6 (`db` — session).
- [x] **Contract tests** (`tests/db/test_session.py`): фасад `from remindme.db import create_engine, create_session_factory`; сигнатуры; `create_engine(settings: Settings) -> AsyncEngine`; `create_session_factory(engine) -> factory`.
- [x] **Code**: `create_engine` — взять `DATABASE_URL`, создать родительскую директорию файла БД (`data/` или `tmp_path`), `create_async_engine`, listener PRAGMA; `create_session_factory` — `async_sessionmaker(expire_on_commit=False)`.
- [x] **Code**: добавить `create_engine, create_session_factory` в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/db/test_session.py -v`.
- [x] **Logic tests**: `test_create_engine_creates_data_dir` (`tmp_path`/`data/`, `Path.exists()`); `test_pragma_foreign_keys_on` (через соединение `PRAGMA foreign_keys` → 1); `test_pragma_wal_mode`; `test_factory_expire_on_commit_false` (сессия после commit сохраняет доступ к объектам).
- [x] **Debugging**: `pytest tests/db/test_session.py -x`.
- [x] **Contract re-verification**: PRAGMA и `expire_on_commit=False` по контракту; фасад importable.
- [x] **Lint**: `ruff check src/remindme/db/`.

---

### Task 7: `db` — repositories (reminders & user timezone)

Реализовать первую группу репозиториев в `src/remindme/db/repositories.py`: `create_reminder`, `list_reminders`,
`CancelOutcome`, `cancel_reminder`, `set_user_timezone`. Изоляция по `(id, user_id)`; ISO 8601; `rowcount`;
`SQLAlchemyError`→rollback+re-raise. `CancelOutcome` — pydantic `kw_only`.

**Usages relevant to this task:**
- `sqlalchemy`: `select`, `update`, `add`/`commit`/`rollback`, `result.rowcount`.
- `conventions`: ISO 8601, structured logging, относительные импорты, pydantic `kw_only`.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: repositories.py (группа reminders).**

- [x] **Task declaration**: Task 7 (`db` — repositories: reminders+TZ).
- [x] **Contract tests** (`tests/db/test_repositories.py`): фасад `from remindme.db import create_reminder, list_reminders, cancel_reminder, set_user_timezone, CancelOutcome`; сигнатуры; `CancelOutcome.kind` варианты `cancelled/not_found/already_sending`.
- [x] **Code**: реализовать 5 сущностей: `CancelOutcome` (pydantic kw_only), `create_reminder` (datetime→ISO, `status="scheduled"`, `attempt_count=0`, `next_attempt_at_utc=remind_at_utc`, add+commit, rollback/re-raise), `list_reminders` (`status="scheduled"`, `order_by remind_at_utc asc`, `limit 20`, фильтр `user_id`), `cancel_reminder` (`UPDATE … WHERE id AND user_id AND status='scheduled'`, `rowcount==1`→`cancelled`, иначе доп. `SELECT status`→`already_sending`/`not_found`), `set_user_timezone` (`UPDATE User … WHERE telegram_user_id`, `rowcount==1`).
- [x] **Code**: добавить имена в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/db/test_repositories.py -v -k "reminder or timezone or cancel"`.
- [x] **Logic tests**: `test_create_reminder_scheduled_defaults`; `test_list_reminders_limit` (21 запись → 20, только user_id=42, ближайшие по времени — перенос из дизайна **Test Stack Trace**); `test_list_reminders_isolates_by_user`; `test_cancel_reminder_scheduled_to_cancelled`; `test_cancel_reminder_already_sending`; `test_cancel_reminder_foreign_user_returns_not_found` (изоляция по владельцу, чужие не раскрываются); `test_set_user_timezone_rowcount`.
- [x] **Debugging**: `pytest tests/db/test_repositories.py -x -k "reminder or timezone or cancel"`.
- [x] **Contract re-verification**: изоляция `(id,user_id)`, ISO 8601, `rowcount`, `CancelOutcome` по контракту.
- [x] **Lint**: `ruff check src/remindme/db/`.

---

### Task 8: `db` — repositories (notes & todos)

Реализовать в `src/remindme/db/repositories.py` (добавлением к Task 7): `create_note`, `list_notes`, `delete_note`,
`create_todo`, `list_todos`, `list_completed`, `complete_todo`, `delete_todo`. `NULLS LAST` для `list_todos`;
`delete_todo` возвращает `bool | None`; `complete_todo` только `active→completed`.

**Usages relevant to this task:**
- `sqlalchemy`: `select`, `delete`, `update`, `nullslast`, `rowcount`.
- `conventions`: ISO 8601, structured logging.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: repositories.py (группа notes/todos).**

- [x] **Task declaration**: Task 8 (`db` — repositories: notes+todos).
- [x] **Contract tests** (`tests/db/test_repositories.py`): фасад `from remindme.db import create_note, list_notes, delete_note, create_todo, list_todos, list_completed, complete_todo, delete_todo`; сигнатуры; `delete_todo -> bool | None`.
- [x] **Code**: реализовать 8 сущностей: `create_note` (ISO `created_at_utc`), `list_notes` (`order_by created_at_utc desc`, `limit 20`), `delete_note` (`DELETE WHERE id AND user_id`, `rowcount==1`), `create_todo` (`status="active"`, `due_at_utc` ISO|None, past допускается), `list_todos` (`status="active"`, `order_by due_at_utc asc NULLS LAST, created_at_utc desc`, `limit 20`), `list_completed` (`status="completed"`, `order_by completed_at_utc desc`, `limit 20`), `complete_todo` (`UPDATE … WHERE id AND user_id AND status='active'`, `completed_at_utc`, `rowcount==1`), `delete_todo` (статус до удаления по `(id,user_id)`; `None`/`True`/`False`).
- [x] **Code**: добавить имена в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/db/test_repositories.py -v -k "note or todo or completed or complete"`.
- [x] **Logic tests**: `test_create_note_and_list_desc`; `test_delete_note_foreign_returns_false`; `test_create_todo_active_with_and_without_due`; `test_list_todos_nulls_last` (сначала со сроком, затем без — перенос из дизайна); `test_list_completed_desc`; `test_complete_todo_active_to_completed_and_repeat_returns_false`; `test_delete_todo_was_completed_none_true_false` (parametrize: чужая→None, completed→True, active→False).
- [x] **Debugging**: `pytest tests/db/test_repositories.py -x -k "note or todo or completed or complete"`.
- [x] **Contract re-verification**: `NULLS LAST`, `status`-фильтры, изоляция, `delete_todo` возвращаемые значения по контракту.
- [x] **Lint**: `ruff check src/remindme/db/`.

---

### Task 9: `db` — repositories (delivery primitives)

Реализовать в `src/remindme/db/repositories.py` (добавлением): `find_due_reminders`, `claim_for_sending`, `mark_sent`,
`mark_failed`, `record_send_failure`, `recover_stuck_sending`. Атомарный захват (`status='scheduled'` в WHERE + `rowcount==1`);
расписание повторов `{1:30,2:120,3:600}`, `failed` на 4-й; recovery зависших `sending` (>60 c).

**Usages relevant to this task:**
- `sqlalchemy`: `select`, `update`, `rowcount`; ISO-лексикографическое сравнение.
- `conventions`: ISO 8601, structured logging (ERROR без текста записи и токена в `record_send_failure`).

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: repositories.py (группа delivery).**

- [x] **Task declaration**: Task 9 (`db` — repositories: delivery).
- [x] **Contract tests** (`tests/db/test_repositories.py`): фасад `from remindme.db import find_due_reminders, claim_for_sending, mark_sent, mark_failed, record_send_failure, recover_stuck_sending`; сигнатуры; `record_send_failure -> bool`; `recover_stuck_sending(...) -> int`.
- [x] **Code**: реализовать 6 сущностей строго по Algorithm контракта: `find_due_reminders` (ISO `now`, `next_attempt_at_utc <= now_iso`, без перевода статуса), `claim_for_sending` (`UPDATE … SET status='sending', locked_at_utc WHERE id AND status='scheduled'`, `rowcount==1`), `mark_sent` (`status='sent'`, `sent_at_utc`), `mark_failed` (`status='failed' WHERE id AND status='sending'`, `rowcount==1`, без инкремента), `record_send_failure` (`SELECT attempt_count`; `new_count+1`; `>=4`→`mark_failed`→`True`; иначе `delay={1:30,2:120,3:600}[new_count]`, `UPDATE status='scheduled', attempt_count, next_attempt_at_utc=now+delay, locked_at_utc=NULL`→`False`), `recover_stuck_sending` (`threshold=now-60s`; `UPDATE status='scheduled', locked_at_utc=NULL WHERE status='sending' AND locked_at_utc<=threshold`; `rowcount`).
- [x] **Code**: добавить имена в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/db/test_repositories.py -v -k "due or claim or sent or failed or failure or recover"`.
- [x] **Logic tests**: `test_find_due_reminders_lexicographic_iso` (просроченные `scheduled`, не трогает `sent`/`cancelled`); `test_claim_for_sending_atomic_rowcount` (повторный claim той же записи → `False`); `test_mark_sent_sets_sent_at`; `test_mark_failed_only_sending`; `test_record_send_failure_schedule` (parametrize `attempt_count` ∈ {0,1,2,3}: 0→scheduled/attempt=1/+30s/False; 1→+120s; 2→+600s; 3→failed/True — перенос дословно из дизайна **Test Stack Trace**); `test_recover_stuck_sending_at_startup` (`sending` с `locked_at_utc<=now-60s`→`scheduled`, запись остаётся due — перенос из дизайна).
- [x] **Debugging**: `pytest tests/db/test_repositories.py -x -k "due or claim or sent or failed or failure or recover"`.
- [x] **Contract re-verification**: атомарный захват, расписание 30/120/600, 4-я→failed, recovery по порогу 60 с — по контракту и Trace B.
- [x] **Lint**: `ruff check src/remindme/db/`.

---

### Task 10: `services` cell package scaffold (infrastructure)

Создать пакет `src/remindme/services/` (`__init__.py`, `__all__=[]`) и `tests/services/__init__.py`.

**Usages relevant to this task:**
- `conventions`: mirror тестов, относительные импорты, `tests/services/test_<module>.py`.

**CRITICAL: `CODEMANIFEST` files — read-only.**

- [x] Создать `src/remindme/services/__init__.py` (docstring, `__all__: list[str] = []`).
- [x] Создать `tests/services/__init__.py`.
- [x] Verify: `python -c "import remindme.services"`.
- [x] Lint: `ruff check src/remindme/services/`.

---

### Task 11: `services` — `parse_remind_time`, `ParsedReminder`, `ParseError`

Реализовать чистый детерминированный парсер в `src/remindme/services/parser.py`. Самая логически насыщенная задача:
режимы `/remind` и фраза «напомни [мне]», 5 шаблонов, `strptime`, DST-валидация (fold=0/fold=1), проверки текста/границ.
DTO `ParsedReminder`/`ParseError` — pydantic `kw_only`. `now` — параметр (aware UTC).

**Usages relevant to this task:**
- `timezone` (inline): `ZoneInfo(name)`, локализация naive со сравнением `utcoffset` fold=0/fold=1 → `nonexistent_time`/`ambiguous_time`.
- `conventions`: pydantic `kw_only`, `@pytest.mark.parametrize`, относительные импорты. `now` фиксируется снаружи — `datetime.now()` внутри НЕ вызывается.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: parser.py; DST-проверка ДО past/horizon.**

- [x] **Task declaration**: Task 11 (`services` — parse_remind_time).
- [x] **Contract tests** (`tests/services/test_parser.py`): фасад `from remindme.services import parse_remind_time, ParsedReminder, ParseError`; сигнатура `parse_remind_time(raw, now, timezone, default_time) -> ParsedReminder | ParseError`; `ParsedReminder.remind_at_utc/text`, `ParseError.kind`.
- [x] **Code**: реализовать разбор режимов (`/remind` split по первому `|`; фраза — strip «напомни [мне]» `re.IGNORECASE`), 5 шаблонов (`через N минут/часов/дней`, абсолют `ГГГГ-ММ-ДД ЧЧ:ММ`, `ДД.ММ.ГГГГ в ЧЧ:ММ` и пр. — частное→общее), `strptime` (календарная проверка → `date_not_exist`), локализация через `ZoneInfo` (fold → `nonexistent_time`/`ambiguous_time`), проверки текста (`empty_text`/`text_too_long`) и границ (`past`/`horizon_exceeded`), `invalid_format`. `ParsedReminder`/`ParseError` (pydantic kw_only).
- [x] **Code**: добавить имена в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/services/test_parser.py -v`.
- [x] **Logic tests** (перенос дословно из дизайна **Test Stack Trace** и **Дополнительные параметризованные**): `test_parse_remind_time_relative_minutes`; `test_parse_remind_time_absolute_command` (split по `|`, Москва UTC+3 → 15:00 UTC); `test_parse_remind_time_past`; `test_parse_remind_time_unsupported_phrase` (`invalid_format`); `test_parse_remind_time_date_not_exist` (`31.02` → `date_not_exist`); `test_parse_remind_time_horizon_exceeded` (400 дней); `test_parse_remind_time_horizon_boundary_in_range` (ровно 365); `test_parse_remind_time_text_too_long` (501); `test_parse_remind_time_text_length_boundaries` (param 1/500/501); `test_parse_remind_time_empty_text`; `test_parse_remind_time_dst_nonexistent`; `test_parse_remind_time_ambiguous_time`; `test_parse_remind_time_unit_forms` (param единицы: минуту/час/день и формы); `test_parse_remind_time_n_boundaries` (N=1 и большое N→horizon). Для каждого — точные assertions по дизайну.
- [x] **Debugging**: `pytest tests/services/test_parser.py -x`.
- [x] **Contract re-verification**: все 8 `ParseError.kind` достижимы; DST-проверка раньше past/horizon; чистая функция (нет `datetime.now()`/БД/Telegram).
- [x] **Lint**: `ruff check src/remindme/services/`.

---

### Task 12: `services` — reminder scenarios

Реализовать в `src/remindme/services/reminders.py`: `create_reminder_scenario`, `set_timezone_scenario`, `cancel_reminder_scenario`.
Сценарии оркестрируют парсер → репозиторий; типизированные ошибки без сохранения.

**Usages relevant to this task:**
- `repositories` (imports из db): `create_reminder`, `cancel_reminder`, `set_user_timezone` (изоляция, `rowcount`).
- `timezone` (inline): `ZoneInfo` в `set_timezone_scenario`.
- `conventions`: относительные импорты.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: reminders.py; при ParseError запись не создаётся.**

- [x] **Task declaration**: Task 12 (`services` — reminder scenarios).
- [x] **Contract tests** (`tests/services/test_reminders.py`): фасад `from remindme.services import create_reminder_scenario, set_timezone_scenario, cancel_reminder_scenario`; сигнатуры; типы возврата `Reminder | ParseError`, `bool`, `CancelOutcome`.
- [x] **Code**: `create_reminder_scenario` (`parse_remind_time`→ParseError вернуть без сохранения→`create_reminder`), `set_timezone_scenario` (`ZoneInfo(timezone)`, ошибка→`False` без БД→`set_user_timezone`), `cancel_reminder_scenario` (делегирует `cancel_reminder`→`CancelOutcome`).
- [x] **Code**: добавить имена в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/services/test_reminders.py -v`.
- [x] **Logic tests**: `test_create_reminder_scenario_success` (через фикстуру `session`); `test_create_reminder_scenario_parse_error_no_save` (ParseError→БД пуста); `test_set_timezone_scenario_unknown_returns_false` (без БД); `test_set_timezone_scenario_updates`; `test_cancel_reminder_scenario_delegates` (все три `CancelOutcome.kind`).
- [x] **Debugging**: `pytest tests/services/test_reminders.py -x`.
- [x] **Contract re-verification**: parse→repo-поток, `CancelOutcome`, валидация IANA до БД — по контракту.
- [x] **Lint**: `ruff check src/remindme/services/`.

---

### Task 13: `services` — `NoteError`, `create_note_scenario`

Реализовать в `src/remindme/services/notes.py`: `NoteError` (pydantic kw_only, `empty_text/text_too_long`) и
`create_note_scenario` (`strip`, 1–500 → `create_note`).

**Usages relevant to this task:**
- `repositories` (imports): `create_note`. `conventions`: pydantic kw_only.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: notes.py.**

- [x] **Task declaration**: Task 13 (`services` — notes scenario).
- [x] **Contract tests** (`tests/services/test_notes.py`): фасад `from remindme.services import NoteError, create_note_scenario`; `NoteError.kind` варианты.
- [x] **Code**: `NoteError(kind)` (pydantic kw_only), `create_note_scenario` (`text=raw.strip()`; пусто→`empty_text`; `len>500`→`text_too_long`; иначе `create_note`).
- [x] **Code**: добавить имена в фасад.
- [x] **Interface verification**: `pytest tests/services/test_notes.py -v`.
- [x] **Logic tests**: `test_create_note_scenario_success`; `test_create_note_scenario_empty` (`NoteError(empty_text)`); `test_create_note_scenario_too_long` (501 → `text_too_long`); `test_create_note_scenario_strips_whitespace`.
- [x] **Debugging**: `pytest tests/services/test_notes.py -x`.
- [x] **Contract re-verification**: 1–500, kinds, без сохранения при ошибке.
- [x] **Lint**: `ruff check src/remindme/services/`.

---

### Task 14: `services` — `TodoError`, `ParsedTodo`, `parse_todo_input`, `create_todo_scenario`

Реализовать в `src/remindme/services/todos.py` терпимый парсер задач и сценарий. Ключевое: `|` разделяет срок
ТОЛЬКО при валидной дате слева; иначе весь текст (с литералом `|`) — задача без срока. Past допускается; без DST/горизонта.

**Usages relevant to this task:**
- `timezone` (inline): `ZoneInfo` локализация `due_raw`→aware UTC. `repositories` (imports): `create_todo`.
- `conventions`: pydantic kw_only, parametrize.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: todos.py; некорректная дата слева от `|` НЕ ошибка.**

- [x] **Task declaration**: Task 14 (`services` — todos parser+scenario).
- [x] **Contract tests** (`tests/services/test_todos.py`): фасад `from remindme.services import TodoError, ParsedTodo, parse_todo_input, create_todo_scenario`; `ParsedTodo.due_at_utc: datetime | None`; `TodoError.kind` ∈ `empty_text/text_too_long`.
- [x] **Code**: `TodoError`, `ParsedTodo(due_at_utc, text)` (pydantic kw_only), `parse_todo_input` (есть `|` И левая==`ГГГГ-ММ-ДД ЧЧ:ММ` (`strptime`)→`due_raw`→aware UTC, `raw_text`=правая; иначе `due_at_utc=None`, `raw_text`=весь `raw`; `text=raw_text.strip()`; пусто→`empty_text`; `>500`→`text_too_long`), `create_todo_scenario` (parse→TodoError вернуть→`create_todo`).
- [x] **Code**: добавить имена в фасад.
- [x] **Interface verification**: `pytest tests/services/test_todos.py -v`.
- [x] **Logic tests**: `test_parse_todo_input_nondate_after_pipe` (`"не дата | Купить хлеб"` → `due_at_utc is None`, `text=="не дата | Купить хлеб"`, не `TodoError` — перенос дословно из дизайна); `test_parse_todo_input_valid_date_due`; `test_parse_todo_input_no_pipe_no_due`; `test_parse_todo_input_empty_text`; `test_parse_todo_input_too_long`; `test_create_todo_scenario_success_with_and_without_due`.
- [x] **Debugging**: `pytest tests/services/test_todos.py -x`.
- [x] **Contract re-verification**: lenient-правило `|` (по ТЗ), past допускается, без DST/горизонта, чистая функция.
- [x] **Lint**: `ruff check src/remindme/services/`.

---

### Task 15: `bot` cell package scaffold (infrastructure)

Создать пакет `src/remindme/bot/` (`__init__.py`, `__all__=[]`) и `tests/bot/__init__.py`. Существующие `bot/.usages/*.md`
не трогать (актуальны). `worker` зависит от `format_notification` — поэтому formatter (Task 16) идёт раньше worker.

**Usages relevant to this task:**
- `aiogram`: типы для фасада (появятся в последующих задачах). `conventions`: mirror тестов.

**CRITICAL: `CODEMANIFEST` files — read-only.**

- [x] Создать `src/remindme/bot/__init__.py` (docstring, `__all__: list[str] = []`).
- [x] Создать `tests/bot/__init__.py`.
- [x] Verify: `python -c "import remindme.bot"`.
- [x] Lint: `ruff check src/remindme/bot/`.

---

### Task 16: `bot` — formatter (`to_local_string` + `format_*`)

Реализовать чистые функции форматирования в `src/remindme/bot/formatter.py`. Базовый helper `to_local_string(utc_iso, timezone)`
(`DD.MM.YYYY в HH:MM`); `format_notification` (`"Напоминание\n<text>"`); `format_parse_error`/`format_cancel_outcome`
(по `kind`); списки/подтверждения для reminder/note/todo/completed.

**Usages relevant to this task:**
- `models` (imports из db): `Reminder`, `Note`, `Todo`. `parser`/`reminders` (imports из services): `ParseError`, `CancelOutcome`.
- `timezone` через stdlib `zoneinfo`: aware→целевая зона→локальная строка.
- `conventions`: чистые функции без побочных эффектов, Google-docstrings.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: formatter.py; формат `DD.MM.YYYY в HH:MM`; суффикс `(IANA)` только в `format_reminder_confirmation`.**

- [x] **Task declaration**: Task 16 (`bot` — formatter).
- [x] **Contract tests** (`tests/bot/test_formatter.py`): фасад `from remindme.bot import to_local_string, format_notification, format_parse_error, format_cancel_outcome, format_reminder_confirmation, format_reminder_list, format_note_confirmation, format_note_list, format_note_error, format_todo_confirmation, format_todo_list, format_completed_list, format_todo_error`; сигнатуры.
- [x] **Code**: `to_local_string` (ISO→aware UTC→`ZoneInfo(timezone)`→`strftime("%d.%m.%Y в %H:%M")`); `format_notification` (`"Напоминание\n"+reminder.text`); `format_parse_error` (по `kind`: invalid_format/past/horizon_exceeded/empty_text/date_not_exist/text_too_long/nonexistent_time/ambiguous_time — **без `invalid_format` для todo**; здесь — ParseError-варианты); `format_cancel_outcome` (cancelled/not_found/already_sending); `format_reminder_confirmation` (с `(IANA)`); `format_reminder_list`; note/todo/completed confirmation/list/error.
- [x] **Code**: добавить имена в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/bot/test_formatter.py -v`.
- [x] **Logic tests**: `test_to_local_string_moscow_offset` (`"2026-06-24T15:00:00+00:00"`, `"Europe/Moscow"` → локальная строка); `test_format_notification` (`"Напоминание\nX"`); `test_format_reminder_confirmation_has_iana_suffix`; `test_format_reminder_list_empty_and_filled`; `test_format_parse_error_each_kind`; `test_format_cancel_outcome_each_kind`; `test_format_todo_error_without_invalid_format` (только `empty_text`/`text_too_long`); note/todo/completed списки и подтверждения.
- [x] **Debugging**: `pytest tests/bot/test_formatter.py -x`.
- [x] **Contract re-verification**: формат локальной строки, `format_todo_error` без `invalid_format` (актуальный `.usages/formatter.md`), `format_notification` = `"Напоминание\n<text>"`.
- [x] **Lint**: `ruff check src/remindme/bot/`.

---

### Task 17: `bot` — keyboards

Реализовать билдеры inline-клавиатур в `src/remindme/bot/keyboards.py`: `notes_keyboard`, `todos_keyboard`,
`completed_keyboard`, `reminders_keyboard`. `callback_data=<action>:<entity>:<id>` (≤64 байт); пустой список → `None`.

**Usages relevant to this task:**
- `aiogram`: `InlineKeyboardMarkup`, `InlineKeyboardButton`. `conventions`: чистые билдеры без БД.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: keyboards.py.**

- [x] **Task declaration**: Task 17 (`bot` — keyboards).
- [x] **Contract tests** (`tests/bot/test_keyboards.py`): фасад `from remindme.bot import notes_keyboard, todos_keyboard, completed_keyboard, reminders_keyboard`; сигнатуры `(...) -> InlineKeyboardMarkup | None`.
- [x] **Code**: 4 билдера (`notes_keyboard`→`delete:note:{id}`; `todos_keyboard`→`complete:todo:{id}`+`delete:todo:{id}`; `completed_keyboard`→`delete:todo:{id}`; `reminders_keyboard`→`delete:reminder:{id}`); пустой список → `None`.
- [x] **Code**: добавить имена в фасад.
- [x] **Interface verification**: `pytest tests/bot/test_keyboards.py -v`.
- [x] **Logic tests**: `test_empty_list_returns_none` (для каждой); `test_callback_data_format` (`action:entity:id`); `test_todos_keyboard_has_complete_and_delete`; `test_callback_data_under_64_bytes`.
- [x] **Debugging**: `pytest tests/bot/test_keyboards.py -x`.
- [x] **Contract re-verification**: 4 пары action:entity, `None` при пустом, ≤64 байт.
- [x] **Lint**: `ruff check src/remindme/bot/`.

---

### Task 18: `bot` — handlers core & phase 1 (`PrivateOnly`, `ensure_user`, start/help/text/group/unknown)

Реализовать в `src/remindme/bot/handlers.py` фильтр `PrivateOnly`, `ensure_user` (идемпотентная регистрация с `now`),
и обработчики этапа 1: `cmd_start`, `cmd_help`, `handle_unknown_command`, `handle_text`, `handle_group`. Фабрика сессий
замыкается; `now` фиксируется первым шагом каждого пишущего handler'а.

**Usages relevant to this task:**
- `aiogram`: `BaseFilter`, `Command`, `Message`, `message.answer`. `settings` (imports): `get_settings`. `models` (imports): `User`.
- `testing` (`src/remindme/bot/.usages/testing.md`): handler'ы вызываются как async-функции с mock `Message` (`AsyncMock`), `now` и зона передаются явно.
- `conventions`: handler'ы — прямой вызов с mock Telegram API.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: handlers.py (core+phase1); `ensure_user` принимает `now`.**

- [x] **Task declaration**: Task 18 (`bot` — handlers core+phase1).
- [x] **Contract tests** (`tests/bot/test_handlers.py`): фасад `from remindme.bot import PrivateOnly, ensure_user, cmd_start, cmd_help, handle_unknown_command, handle_text, handle_group`; сигнатуры; `ensure_user(session, telegram_user_id, timezone, now) -> User`.
- [x] **Code**: `PrivateOnly(BaseFilter).__call__` (chat.type=="private"→True); `ensure_user` (найти `User`→нет→создать с `created/updated=now_iso`, `IntegrityError`→rollback+повторный SELECT; есть→`updated=now_iso`; commit); `cmd_start`/`cmd_help`/`handle_text` (сессия→`now`→`get_settings().DEFAULT_TIMEZONE`→`ensure_user`→ответ); `handle_unknown_command` (→/help, без БД); `handle_group` (фиксированный ответ, без БД).
- [x] **Code**: добавить имена в фасад.
- [x] **Interface verification**: `pytest tests/bot/test_handlers.py -v -k "start or help or text or group or unknown or ensure_user or private"`.
- [x] **Logic tests**: `test_private_only_private_true_group_false`; `test_settings_idempotent_start` (дважды `ensure_user(..., now)` → одна запись `User`, `updated_at_utc` обновлён, `created_at_utc` неизменен — перенос из дизайна); `test_ensure_user_concurrent_integrity_error_recovery` (mock `IntegrityError`→повторный SELECT); `test_cmd_start_answers_greeting`; `test_handle_group_answers_unsupported`.
- [x] **Debugging**: `pytest tests/bot/test_handlers.py -x -k "start or help or text or group or unknown or ensure_user or private"`.
- [x] **Contract re-verification**: идемпотентность, `now` прокинут, группы не пишут в БД.
- [x] **Lint**: `ruff check src/remindme/bot/`.

---

### Task 19: `bot` — remind flow (`cmd_remind`, `cmd_reminders`, `handle_remind_phrase`)

Реализовать в `handlers.py` (добавлением): `cmd_remind`, `cmd_reminders`, `handle_remind_phrase`. Поток: `now`→`ensure_user`
→проверка длины (≤1000)→`create_reminder_scenario`→форматтер→`message.answer`.

**Usages relevant to this task:**
- `reminders` (imports): `create_reminder_scenario`. `parser` (imports): `ParseError`. `format_reminder_confirmation`/`format_parse_error`/`format_reminder_list`. `reminders_keyboard`.
- `aiogram`: `Command`, `message.answer`. `testing`: mock-вызовы.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: handlers.py (remind flow); `now` до `ensure_user`; лимит сообщения 1000.**

- [x] **Task declaration**: Task 19 (`bot` — remind flow).
- [x] **Contract tests** (`tests/bot/test_handlers.py`): фасад `from remindme.bot import cmd_remind, cmd_reminders, handle_remind_phrase`; сигнатуры.
- [x] **Code**: `cmd_remind` (сессия→`now`→`ensure_user`→`len(message.text)>1000`→ответ об ограничении→`create_reminder_scenario`→ParseError→`format_parse_error`/иначе `format_reminder_confirmation`); `cmd_reminders` (`list_reminders`+`reminders_keyboard`+`format_reminder_list`); `handle_remind_phrase` (та же логика, проверка начала «напомни» `re.IGNORECASE`).
- [x] **Code**: добавить имена в фасад.
- [x] **Interface verification**: `pytest tests/bot/test_handlers.py -v -k "remind or reminders or phrase"`.
- [x] **Logic tests**: `test_cmd_remind_creates_reminder_and_confirms`; `test_cmd_remind_parse_error_path`; `test_handle_remind_phrase_creates_reminder`; `test_message_length_boundary` (param 1000/1001: 1000→доходит до сценария; 1001→ответ об ограничении, БД пуста — перенос из дизайна).
- [x] **Debugging**: `pytest tests/bot/test_handlers.py -x -k "remind or reminders or phrase"`.
- [x] **Contract re-verification**: `now` до `ensure_user`, лимит 1000, ParseError без сохранения.
- [x] **Lint**: `ruff check src/remindme/bot/`.

---

### Task 20: `bot` — phase 3/4 commands + `handle_db_error`

Реализовать в `handlers.py` (добавлением): `cmd_timezone`, `cmd_cancel`, `cmd_note`, `cmd_notes`, `cmd_todo`, `cmd_todos`,
`cmd_completed`, и централизованный `handle_db_error(event: ErrorEvent)`. errors-handler ловит только `SQLAlchemyError`,
логирует ERROR (user_id + код; без текста/токена), отвечает единым текстом.

**Usages relevant to this task:**
- `reminders` (imports): `set_timezone_scenario`, `cancel_reminder_scenario`. `notes`/`todos` (imports): `create_note_scenario`/`NoteError`, `create_todo_scenario`/`TodoError`. `repositories` (imports): `list_notes`/`list_todos`/`list_completed`. Форматтеры и клавиатуры.
- `sqlalchemy`: `SQLAlchemyError` в `handle_db_error`. `aiogram`: `Command`, `message.answer`, errors-handler.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: handlers.py (phase3/4+errors); откат делает репозиторий, не errors-handler.**

- [x] **Task declaration**: Task 20 (`bot` — phase3/4 commands + handle_db_error).
- [x] **Contract tests** (`tests/bot/test_handlers.py`): фасад `from remindme.bot import cmd_timezone, cmd_cancel, cmd_note, cmd_notes, cmd_todo, cmd_todos, cmd_completed, handle_db_error`; `handle_db_error(event: ErrorEvent)`.
- [x] **Code**: `cmd_timezone` (аргумент зоны; пуст→подсказка; `set_timezone_scenario`→текст), `cmd_cancel` (id из аргумента; нечисловой→подсказка; `cancel_reminder_scenario`→`format_cancel_outcome`), `cmd_note`/`cmd_notes` (note-сценарий/список+клавиатура), `cmd_todo`/`cmd_todos`/`cmd_completed` (todo-сценарий/списки+клавиатуры); `handle_db_error` (извлечь исключение; не `SQLAlchemyError`→пропустить; лог ERROR без токена/текста; единый ответ).
- [x] **Code**: добавить имена в фасад.
- [x] **Interface verification**: `pytest tests/bot/test_handlers.py -v -k "timezone or cancel or note or todo or completed or db_error"`.
- [x] **Logic tests**: `test_cmd_timezone_ok_and_unknown`; `test_cmd_cancel_outcomes`; `test_cmd_note_success_and_error`; `test_cmd_notes_list_and_keyboard`; `test_cmd_todo_success_and_error`; `test_cmd_todos_nulls_last`; `test_cmd_completed_list`; `test_handle_db_error_sqlalchemy_answers_unified_text`; `test_handle_db_error_non_sqlalchemy_passthrough`; `test_handle_db_error_no_token_in_log`.
- [x] **Debugging**: `pytest tests/bot/test_handlers.py -x -k "timezone or cancel or note or todo or completed or db_error"`.
- [x] **Contract re-verification**: только `SQLAlchemyError`, без токена/текста в логах, единый текст.
- [x] **Lint**: `ruff check src/remindme/bot/`.

---

### Task 21: `bot` — `handle_callback`

Реализовать единый callback-роутер в `src/remindme/bot/callbacks.py`. Разбор `action:entity:id`; некорректный payload→
`callback.answer("Запись уже изменена или удалена.")` без БД; 4 ветки с проверкой владельца и перерисовкой через `edit_text`.

**Usages relevant to this task:**
- `repositories` (imports): `complete_todo`, `delete_note`, `delete_todo`, `list_notes`/`list_todos`/`list_completed`/`list_reminders`. `reminders` (imports): `cancel_reminder_scenario`. Форматтеры и клавиатуры.
- `aiogram`: `CallbackQuery`, `callback.answer`, `callback.message.edit_text`. `testing`: mock `CallbackQuery`.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: callbacks.py; проверка владельца `(record_id, user_id)` на каждом действии.**

- [x] **Task declaration**: Task 21 (`bot` — handle_callback).
- [x] **Contract tests** (`tests/bot/test_callbacks.py`): фасад `from remindme.bot import handle_callback`; сигнатура `handle_callback(callback: CallbackQuery)`.
- [x] **Code**: `handle_callback` (`now`; разбор по `:`; не 3 части/не int→`callback.answer` без БД; сессия; ветвление `complete:todo`/`delete:note`/`delete:todo`(was True→completed-list / False→todos-list)/`delete:reminder`(`cancel_reminder_scenario`→already_sending/not_found/cancelled); успех→`callback.answer()`).
- [x] **Code**: добавить `handle_callback` в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/bot/test_callbacks.py -v`.
- [x] **Logic tests** (перенос из дизайна **Test Stack Trace**): `test_complete_todo_callback` (Todo active→completed, `completed_at_utc` заполнен, `callback.answer` вызван, список перерисован); `test_callback_foreign_record_isolation` (`delete:reminder:15` чужой user_id=999 → «Запись уже изменена или удалена.», запись не изменена); `test_callback_malformed_payload_no_db` (не 3 части / нечисловой id); `test_delete_note_callback_redraw`; `test_delete_todo_was_completed_redraws_completed`; `test_delete_reminder_already_sending`.
- [x] **Debugging**: `pytest tests/bot/test_callbacks.py -x`.
- [x] **Contract re-verification**: 4 ветки action:entity, проверка владельца, перерисовка по статусу.
- [x] **Lint**: `ruff check src/remindme/bot/`.

---

### Task 22: `bot` — `register_handlers` facade

Реализовать фасад регистрации всех обработчиков в `handlers.py`: `register_handlers(dp, session_factory)`. Порядок:
командные → `handle_remind_phrase` → `handle_text` → `handle_group` (без фильтра, последним); `handle_callback` через
`dp.callback_query`; `handle_db_error` как errors-handler с фильтром `SQLAlchemyError`. `session_factory` замыкается.

**Usages relevant to this task:**
- `aiogram`: `Dispatcher`, `Command`, `callback_query`, errors-handler registration. `handlers`/`callbacks` (свои): все handler'ы.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: handlers.py (register_handlers); `main` зависит от этого фасада.**

- [x] **Task declaration**: Task 22 (`bot` — register_handlers).
- [x] **Contract tests** (`tests/bot/test_register_handlers.py`): фасад `from remindme.bot import register_handlers`; сигнатура `register_handlers(dp, session_factory)`; с mock `Dispatcher` регистрируются все handler'ы, `handle_callback`, `handle_db_error`.
- [x] **Code**: `register_handlers` — зарегистрировать фазу 1+remind handlers с `PrivateOnly`+`Command`; `handle_remind_phrase` перед `handle_text`; `handle_group` последним без `PrivateOnly`; phase3/4 команды; `handle_callback` через `dp.callback_query(...)`; `handle_db_error` как errors-handler с фильтром `SQLAlchemyError`; замкнуть `session_factory`.
- [x] **Code**: добавить `register_handlers` в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/bot/test_register_handlers.py -v`.
- [x] **Logic tests**: `test_register_handlers_orders_group_last` (через mock `dp` инспектировать порядок/фильтры); `test_register_handlers_registers_callback_and_error_handler`; `test_register_handlers_attaches_privateonly`.
- [x] **Debugging**: `pytest tests/bot/test_register_handlers.py -x`.
- [x] **Contract re-verification**: порядок регистрации и `handle_group` последним — по контракту; фасад `from remindme.bot import register_handlers`.
- [x] **Lint**: `ruff check src/remindme/bot/`.

---

### Task 23: `worker` cell package scaffold (infrastructure)

Создать пакет `src/remindme/worker/` (`__init__.py`, `__all__=[]`) и `tests/worker/__init__.py`. `worker` зависит от
`format_notification` (bot) и репозиториев доставки (db) — обе клетки уже реализованы.

**Usages relevant to this task:**
- `conventions`: mirror тестов, относительные импорты.

**CRITICAL: `CODEMANIFEST` files — read-only.**

- [x] Создать `src/remindme/worker/__init__.py` (docstring, `__all__: list[str] = []`).
- [x] Создать `tests/worker/__init__.py`.
- [x] Verify: `python -c "import remindme.worker"`.
- [x] Lint: `ruff check src/remindme/worker/`.

---

### Task 24: `worker` — `run_reminder_worker`

Реализовать бесконечный цикл доставки в `src/remindme/worker/notifications.py`. Startup-recovery (1 раз, `recover_stuck_sending`);
цикл: `sleep(poll_interval)`, одна сессия на итерацию, `find_due_reminders`→`claim_for_sending`→`format_notification`→
`bot.send_message(chat_id=reminder.user_id)`; `TelegramForbiddenError`→`mark_failed`; иная `TelegramAPIError`→`record_send_failure`;
успех→`mark_sent`. Токен и текст не логируются.

**Usages relevant to this task:**
- `repositories` (imports из db): `find_due_reminders`, `claim_for_sending`, `mark_sent`, `record_send_failure`, `mark_failed`, `recover_stuck_sending`.
- `formatter` (imports из bot): `format_notification`. `aiogram`: `Bot`, `send_message`, `TelegramForbiddenError`, `TelegramAPIError`.
- `conventions`: structured logging (WARNING при retry/block, без токена/текста); `running` (`src/remindme/worker/.usages/running.md`).
- Тестирование: один тик worker выделяется в тестируемую внутреннюю функцию (или `asyncio.sleep` мокается); реальный `sleep` не используется.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: notifications.py; одна итерация = одна сессия; recovery 1 раз при старте.**

- [x] **Task declaration**: Task 24 (`worker` — run_reminder_worker).
- [x] **Contract tests** (`tests/worker/test_notifications.py`): фасад `from remindme.worker import run_reminder_worker`; сигнатура `run_reminder_worker(bot, session_factory, poll_interval)`.
- [x] **Code**: `run_reminder_worker` — startup-recovery (отдельная сессия, `recover_stuck_sending(now, 60)`); бесконечный цикл (`asyncio.sleep(poll_interval)`, `now=aware UTC`, одна сессия, `find_due_reminders`, для каждого `claim_for_sending`→`format_notification`→`bot.send_message`, `TelegramForbiddenError`→`mark_failed`+WARNING, `TelegramAPIError`→`record_send_failure`+WARNING, else `mark_sent`); закрытие сессии после цикла по due. Выделить тестируемый шаг одной итерации (для deterministic-тестов без `sleep`).
- [x] **Code**: добавить `run_reminder_worker` в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/worker/test_notifications.py -v`.
- [x] **Logic tests** (перенос из дизайна **Test Stack Trace**): `test_worker_delivers_due_reminder` (Reminder due→claim→`bot.send_message` (chat_id=user_id)→`mark_sent`→`status="sent"`, `await_count==1`); `test_worker_forbidden_marks_failed` (`TelegramForbiddenError`→`status="failed"`, без повторов); `test_worker_transient_failure_schedules_retry` (иная ошибка→`record_send_failure`→`status="scheduled"`); `test_worker_recovery_at_startup` (зависшая `sending`→recovered перед циклом); `test_worker_one_session_per_iteration`; `test_worker_cancelled_not_sent`.
- [x] **Debugging**: `pytest tests/worker/test_notifications.py -x`.
- [x] **Contract re-verification**: recovery 1 раз, atomic claim, Forbidden→failed, одна сессия/итерация, без логов токена/текста.
- [x] **Lint**: `ruff check src/remindme/worker/`.

---

### Task 25: `main` cell package + alembic scaffolding (infrastructure)

Создать пакет `src/remindme/main/` (`__init__.py`), alembic-каркас (`alembic.ini`, `alembic/env.py` async,
`script.py.mako`, `versions/`) и тестовый пакет `tests/main/__init__.py`. `env.py` берёт `DATABASE_URL` из `get_settings`,
`PRAGMA foreign_keys=ON` в online-контексте. Начальная ревизия — в Task 26.

**Usages relevant to this task:**
- `alembic`: `alembic init -t async`, async `env.py`, `run_sync_migrations`, PRAGMA в online-контексте.
- `settings`/`engine` (imports): `get_settings`, `DATABASE_URL`.

**CRITICAL: `CODEMANIFEST` files — read-only.**

- [x] Создать `src/remindme/main/__init__.py` (docstring, `__all__: list[str] = []`).
- [x] Создать `tests/main/__init__.py`.
- [x] Создать alembic-каркас: `alembic.ini` (sqlalchemy.url пуст — берётся из `env.py`), `alembic/env.py` (async-шаблон: `DATABASE_URL` из `get_settings`; `run_migrations_online` через `Connection.run_sync(do_run_migrations)`; `PRAGMA foreign_keys=ON` на connection), `alembic/script.py.mako`, пустой `alembic/versions/`. (Разместить alembic-директорию рядом с `main/` или внутри — единое место, согласованное с `deployment`.)
- [x] Verify: `alembic` доступен в venv; `python -c "import alembic.config"`; `python -c "import remindme.main"`.
- [x] Lint: `ruff check src/remindme/main/`.

---

### Task 26: `main` — `set_commands`, `apply_migrations`, `main`, initial revision, `__main__.py`

Реализовать `set_commands`, `apply_migrations`, `main` в `src/remindme/main/main.py`; создать начальную alembic-ревизию
(4 таблицы (`users`, `reminders`, `notes`, `todos`) с PK/UNIQUE/FK, соответствует `models.py`); внутренний `__main__.py` для `python -m remindme.main`.

**Usages relevant to this task:**
- `alembic`: программный `command.upgrade("head")` in-process (без CLI/subprocess); автогенерация начальной ревизии.
- `aiogram`: `Bot`, `Dispatcher`, `set_my_commands`, `BotCommand`, `start_polling`, `delete_webhook`.
- `settings`/`engine`/`handlers`/`running` (imports): сборка инфраструктуры и запуск.

**CRITICAL: `CODEMANIFEST` files — read-only. Контракт: main.py; миграции до polling, идемпотентны, токен не логируется.**

- [x] **Task declaration**: Task 26 (`main` — entrypoint).
- [x] **Contract tests** (`tests/main/test_main.py`): фасад `from remindme.main import set_commands, apply_migrations, main`; сигнатуры.
- [x] **Code**: `set_commands(bot)` (`set_my_commands` для всех команд этапов 1-4); `apply_migrations` (`command.upgrade("head")` программно); `main()` (apply_migrations→get_settings→engine/factory→Bot/Dispatcher→register_handlers→set_commands→`asyncio.create_task(run_reminder_worker(...))`→`delete_webhook(drop_pending_updates=True)`→`start_polling`); `__main__.py` (`asyncio.run(main())`).
- [x] **Code**: создать начальную ревизию (`alembic revision --autogenerate` против моделей, или авторская) в `versions/` — 4 таблицы (`users`, `reminders`, `notes`, `todos`) с PK/UNIQUE/FK, соответствующие `Base.metadata`.
- [x] **Code**: добавить `set_commands, apply_migrations, main` в фасад `__init__.py`.
- [x] **Interface verification**: `pytest tests/main/test_main.py -v`.
- [x] **Logic tests**: `test_set_commands_calls_set_my_commands` (mock `Bot`, проверить набор `BotCommand`); `test_apply_migrations_creates_schema_and_idempotent` (через `tmp_path` движок: `upgrade head` дважды — второй no-op; схема содержит 4 таблицы); `test_main_assembles_and_starts_polling` (mock Bot/Dispatcher/worker — `skipif` реального Telegram; проверить порядок вызовов, `delete_webhook(drop_pending_updates=True)`, `create_task` worker); `test_initial_revision_matches_metadata` (autogenerate-diff против `Base.metadata` пуст после upgrade).
- [x] **Debugging**: `pytest tests/main/test_main.py -x`.
- [x] **Contract re-verification**: миграции до polling, идемпотентность, один процесс/loop, токен не логируется.
- [x] **Lint**: `ruff check src/remindme/main/`.

---

### Task 27: Integration tests — cross-cell end-to-end

Сквозные сценарии, охватывающие несколько клеток (bot→services→db, worker→db+bot, ensure_user конкурентность).
Размещаются в `tests/` (интеграция — напрямую, без подпакета). Реальные внешние зависимости (Telegram API) под mock;
БД — через `tmp_path`/`session`.

**Usages relevant to this task:**
- `testing` (`src/remindme/bot/.usages/testing.md`): mock Telegram API, проверка аргументов вызовов, побочные эффекты через временную SQLite-сессию.
- `conventions`: integration-тесты напрямую в `tests/`; `pytest.mark.skipif` для e2e-smoke с реальным Telegram.

**CRITICAL: `CODEMANIFEST` files — read-only.**

- [ ] Создать `tests/test_integration.py`.
- [ ] Test full `/remind` round-trip: `Message` → `cmd_remind` → `create_reminder_scenario` → `create_reminder` → БД (`session`) → `format_reminder_confirmation` → `message.answer` (mock). Assert: запись в БД `status="scheduled"`, локальное время в ответе.
- [ ] Test callback complete→redraw: `handle_callback("complete:todo:<id>")` → БД `status="completed"` → `edit_text` вызван.
- [ ] Test worker e2e delivery+escalation: due Reminder → один тик worker (`bot.send_message` mock) → `status="sent"`; при transient error → `status="scheduled"`, `attempt_count` растёт; 4-я неудача → `failed`.
- [ ] Test worker restart recovery: зависшая `sending` (>60 c) → после `recover_stuck_sending` доставляется в следующем тике.
- [ ] Test ensure_user concurrent registration: эмуляция `IntegrityError` (или реальная конкурентная вставка того же `telegram_user_id`) → одна запись, идемпотентность.
- [ ] Run validation: `pytest tests/test_integration.py -v`.
- [ ] (Опц.) e2e smoke реального Telegram под `pytest.mark.skipif` (токен из окружения) — пропускать по умолчанию.

---

### Task 28: Deployment artifacts (Docker)

Создать упаковку приложения в Docker согласно практике `deployment` (`deployment.md`): образ и `docker-compose.yml`.
Приложение однопроцессное; секреты и база данных пробрасываются снаружи; миграции применяются в процессе через
`apply_migrations` (Task 26) — отдельный shell-entrypoint не нужен.

**Usages relevant to this task:**
- `deployment` (`.goga/usages/deployment.md`): Dockerfile (Python 3.12 slim, копирование `src/`/`alembic/`/`alembic.ini`/`pyproject.toml`,
  точка входа `python -m remindme.main`, `data/` и `.env` НЕ в образе), `docker-compose.yml` (один сервис, том `data/`,
  `env_file`, без портов), проверки (`docker compose up` → `/start`; `docker compose restart` → записи сохранены).

**CRITICAL: `CODEMANIFEST` files — read-only.**

- [ ] Создать `Dockerfile`: базовый образ `python:3.12-slim`; установка зависимостей из `pyproject.toml` в venv (`pip install .`);
  `COPY src/ alembic/ alembic.ini pyproject.toml ./`; рабочая директория — корень проекта; `CMD ["python", "-m", "remindme.main"]`.
  `data/` НЕ создаётся в образе; `.env` НЕ копируется (`COPY .env` запрещён).
- [ ] Создать `docker-compose.yml`: один сервис бота, `build: .`, `restart: unless-stopped`; том `./data:/app/data`
  (SQLite + WAL переживают перезапуск контейнера); `TELEGRAM_BOT_TOKEN` и настройки — через `env_file: .env` и/или `environment:`;
  портов наружу не выставлять (long polling; исходящие соединения к Telegram API).
- [ ] Создать `.dockerignore` (и дополнить корневой `.gitignore`): `.env`, `data/`, `.venv/`, `__pycache__/`, `tests/`, `.goga/`, `docs/`
  — секреты и данные не попадают в образ и репозиторий.
- [ ] Verify: `docker compose config` (конфиг валиден); при наличии Docker — `docker compose build` собирается без ошибок.

---

## Validation Commands

- `pytest tests/ -x`: Run all tests (fail-fast).
- `pytest tests/<cell>/test_<module>.py -v`: Run a specific cell/module test (task-level).
- `ruff check src/ tests/`: Lint check.
- `ruff format --check src/ tests/`: Format check.
- `python -c "from remindme.<cell> import <Entity>, ..."`: Facade accessibility per cell (e.g. `from remindme.config import Settings, get_settings`).
- `goga lint`: Contract validation (`cells: 6 errors: 0`) — design baseline.
- `goga schema`: Acyclic import graph check.
- `docker compose config`: Validate the Docker Compose configuration (deployment).

---

## Completion Criteria

- [ ] Every contract entity is implemented in the correct `location` (per `CODEMANIFEST`).
- [ ] Every contract entity is accessible from its cell facade (`__init__.py` + `__all__`).
- [ ] Properties and methods match the declared API.
- [ ] Descriptions (algorithms, constraints, requirements) are reflected in behavior.
- [ ] Contract dependencies (Imports.Types) are met; cross-cell usages consumed correctly.
- [ ] No re-export obligations (none declared) — each facade exports only its own entities.
- [ ] Every coding task followed the TDD workflow (contract tests → code → verification → logic tests → debugging → re-verification → lint).
- [ ] Contract tests and logic tests cover facade, API, and behavior within each coding task.
- [ ] Integration tests exist for cross-cell scenarios (Task 27).
- [ ] Deployment artifacts (`Dockerfile`, `docker-compose.yml`, `.dockerignore`) created per `deployment` usage — без секретов/данных в образе; миграции in-process (Task 28).
- [ ] No cell boundary was expanded (no new cells, no new facade-level interfaces).
- [ ] `CODEMANIFEST` files were not modified (contract is read-only).
- [ ] All validation commands pass (`pytest tests/ -x`, `ruff check`, `ruff format --check`, facade checks, `goga lint`, `goga schema`).
- [ ] All 26 explicitly-designed test scenarios are implemented (several parametrized); contract/logic tests cover every entity.
- [ ] `.usages/` files required no changes (verified current — `todos.md`/`formatter.md` already reflect lenient `parse_todo_input` and absence of `invalid_format`).
