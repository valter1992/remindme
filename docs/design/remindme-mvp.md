# Design Document: `remindme-mvp`

Консолидированная архитектурная спецификация RemindMe, полученная из итогового (очищенного) корпуса
CODEMANIFEST шести клеток. Проект — зелёное поле: контракты есть, реализации (`.py`) пока нет.
Документ описывает **что** и **как** реализовать; порядок реализации — отдельная задача планирования.

- **Источник контрактов:** `src/remindme/{config,db,services,bot,worker,main}/CODEMANIFEST`
- **Практики:** `.goga/usages/conventions.md` + `.goga/usages/cooks/*.md`
- **ТЗ:** `docs/propose/project.md`
- **Валидация:** `goga lint` → `cells: 6 errors: 0`; `goga schema` → ацикличный граф.

---

## Contract Changes

Контрактных изменений относительно внешнего git-состояния нет (ветки выровнены, рабочее дерево было
чистым до начала дизайна). Дизайн строится по всей поверхности CODEMANIFEST как единому «изменению»
(greenfield). В рамках аудита согласованности в CODEMANIFEST внесены точечные правки (см. **Applied Fixes**).

### Changed CODEMANIFEST Files
- `src/remindme/services/CODEMANIFEST`: `parse_todo_input` переведён на терпимое поведение по ТЗ; `TodoError.kind` лишён `invalid_format`.
- `src/remindme/bot/CODEMANIFEST`: `ensure_user` получил параметр `now`; `format_todo_error` лишён ветки `invalid_format`; в `cmd_remind`/`handle_remind_phrase` `now` вычисляется до `ensure_user`; добавлена глобальная аннотация о thread'инге `now`.

### New Entities
Все сущности — новые (greenfield). Перечисление по клеткам:
- **config** — `Settings`, `get_settings`
- **db** — `Base`, `User`, `Reminder`, `Note`, `Todo`, `create_engine`, `create_session_factory`, репозитории (`create_reminder`, `list_reminders`, `CancelOutcome`, `cancel_reminder`, `set_user_timezone`, `create_note`, `list_notes`, `delete_note`, `create_todo`, `list_todos`, `list_completed`, `complete_todo`, `delete_todo`, `find_due_reminders`, `claim_for_sending`, `mark_sent`, `mark_failed`, `record_send_failure`, `recover_stuck_sending`)
- **services** — `parse_remind_time`, `ParsedReminder`, `ParseError`, `create_reminder_scenario`, `set_timezone_scenario`, `cancel_reminder_scenario`, `NoteError`, `create_note_scenario`, `TodoError`, `ParsedTodo`, `parse_todo_input`, `create_todo_scenario`
- **bot** — `PrivateOnly`, `ensure_user`, `cmd_start`, `cmd_help`, `handle_unknown_command`, `handle_text`, `handle_group`, `cmd_remind`, `cmd_reminders`, `cmd_timezone`, `cmd_cancel`, `cmd_note`, `cmd_notes`, `cmd_todo`, `cmd_todos`, `cmd_completed`, `handle_db_error`, `handle_remind_phrase`, `handle_callback`, форматтеры (`format_*`, `to_local_string`), клавиатуры (`*_keyboard`), `register_handlers`
- **worker** — `run_reminder_worker`
- **main** — `set_commands`, `apply_migrations`, `main`

### Changed Entities
- `parse_todo_input` — алгоритм переписан: `|` разделяет срок только когда левая часть — валидная дата, иначе весь текст сохраняется без срока (раньше: `invalid_format`).
- `ensure_user` — добавлен параметр `now: datetime`; `created_at_utc`/`updated_at_utc` берутся из него, а не из внутреннего `datetime.now()`.
- `cmd_remind`, `handle_remind_phrase` — `now` фиксируется до `ensure_user` и прокидывается далее.
- `TodoError`, `format_todo_error` — убран вариант `invalid_format`.

### Deleted Entities
Нет.

### Usages and Annotations Changes
- Глобальная `Annotations` клетки `bot` дополнена правилом thread'инга `now` (фиксация aware-UTC момента первым шагом каждого пишущего handler'а).

---

## Applied Fixes

### Fixed CODEMANIFEST Defects

1. **`services/CODEMANIFEST` — `parse_todo_input` ↔ ТЗ (project.md 138, 271-274)**
   - Было: при наличии `|` и не-дате слева → `TodoError(invalid_format)`.
   - Стало: `|` разделяет срок только при валидной дате слева; иначе весь аргумент — текст задачи без срока.
   - Причина: противоречие «контракт ↔ ТЗ» (defect type: interface↔spec). Решение пользователя: по ТЗ.

2. **`bot/CODEMANIFEST` — `ensure_user` и принцип явного времени**
   - Было: `ensure_user(session, telegram_user_id, timezone)` — единственное место с внутренним `datetime.now()`.
   - Стало: `ensure_user(session, telegram_user_id, timezone, now)`; handler'ы вычисляют `now` раньше.
   - Причина: нарушение принципа «текущий момент фиксируется один раз и передаётся аргументом» + негарантируемые тесты (defect type: consistency/testability).

### Решения «оставить как есть» (задокументировано, правок нет)
- **TelegramForbiddenError → немедленный `mark_failed`** (worker). ТЗ формально пишет «после четырёх попыток», но блокировка пользователя необратима, повторы бессмысленны. Контракт оставлен: Forbidden → `mark_failed` сразу; транзиентные ошибки → `record_send_failure` с эскалацией до `failed` на 4-й неудаче.
- **Команда `/cancel` оставлена** как дополнительный способ отмены напоминания (переиспользует `cancel_reminder_scenario`, нужный для inline `delete:reminder`). В ТЗ `/cancel` отсутствует, но это безопасное аддитивное расширение.

---

## Entity Interaction and Data Flow

### Interaction Diagram

```
                    ┌───────────────────────────┐
                    │          main             │  точка входа (один процесс)
                    │  apply_migrations → bot,  │
                    │  worker, session_factory  │
                    └─────┬───────────────┬─────┘
                          │               │ asyncio.create_task
                  register│               │
                   handlers│               ▼
        ┌─────────────────▼─────┐   ┌──────────────┐
        │          bot          │   │    worker    │
        │ handlers / callbacks  │   │ run_reminder │
        │ formatter / keyboards │   │   _worker    │
        └──┬──────────┬─────────┘   └──────┬───────┘
           │          │ format_            │ find_due / claim /
        sce│          │ notification       │ mark_sent / fail
        nari│          │      └─────────────┤
           ▼          │                     ▼
   ┌──────────────┐   │            ┌────────────────┐
   │   services   │   │            │       db       │  SQLAlchemy 2.x async
   │ parser +     │   └────────────┤ models +       │  (aiosqlite, WAL, FK=ON)
   │ scenarios    │                │ repositories   │
   └──────┬───────┘                └───────┬────────┘
          │ Reminder/Note/Todo/           │ Settings
          │ CancelOutcome (Imports)        │
          └────────────────────────────────┘
                           │
                  ┌────────▼────────┐
                  │     config      │  pydantic-settings (SecretStr)
                  │ Settings/get_   │
                  │ settings        │
                  └─────────────────┘

Граф Imports (ацикличный, проверено goga schema):
  config  (leaf)
    └─► db        (Settings)
         └─► services (Reminder/Note/Todo/CancelOutcome/репозитории + settings)
              └─► bot    (get_settings + db-типы + services-сценарии/ошибки)
  worker ─► bot (format_notification) + db (репозитории доставки)
  main   ─► config + db + bot + worker
```

Слои направлены строго вниз; `worker` зависит от `bot` только ради чистой функции `format_notification`
(не цикл: `bot` не импортирует `worker`).

### Data Flows

**A. Создание напоминания (`/remind` или фраза «напомни …»).**
`Message` → `cmd_remind`/`handle_remind_phrase` → `ensure_user` (User) → `create_reminder_scenario` →
`parse_remind_time` (→ `ParsedReminder | ParseError`) → `create_reminder` (Reminder, status=`scheduled`) →
`format_reminder_confirmation`/`format_parse_error` → `message.answer`.
Данные: `raw` (str) + `now` (aware UTC) + `timezone` (IANA) + `default_time` (HH:MM) → UTC-момент + очищенный текст.

**B. Доставка уведомления (фон).**
`run_reminder_worker` (цикл, `poll_interval`) → `find_due_reminders` (status=`scheduled`, `next_attempt_at_utc <= now`)
→ `claim_for_sending` (атомарно `scheduled→sending`) → `format_notification` → `bot.send_message(chat_id=reminder.user_id)`
→ `mark_sent` ИЛИ (`TelegramForbiddenError`→`mark_failed`) ИЛИ (иная ошибка→`record_send_failure`: 30/120/600 и failed на 4-й).
Стартовая разовая `recover_stuck_sending` возвращает зависшие `sending` (>60 c) в `scheduled`.

**C. Inline-действие (callback `<action>:<entity>:<id>`).**
`CallbackQuery` → `handle_callback` → разбор `action:entity:id` → сессия → ветвление:
`complete:todo`→`complete_todo`; `delete:note`→`delete_note`; `delete:todo`→`delete_todo` (возвращает `was_completed`);
`delete:reminder`→`cancel_reminder_scenario`. Не найдено/чужая/устаревшая → `callback.answer("Запись уже изменена или удалена.")`.
Успех → перерисовка списка через `callback.message.edit_text` + нужная клавиатура.

**D. Создание задачи (`/todo`).**
`Message` → `cmd_todo` → `ensure_user` → `create_todo_scenario` → `parse_todo_input`
(`|`+валидная дата→срок; иначе весь текст без срока) → `create_todo` (Todo, status=`active`) → `format_todo_*`.

**E. Запуск (`main`).**
`apply_migrations` (alembic `upgrade head` in-process, до polling) → `get_settings` → `create_engine`/`create_session_factory`
→ `Bot(token)` + `Dispatcher` → `register_handlers(dp, session_factory)` → `set_commands` →
`asyncio.create_task(run_reminder_worker(...))` → `bot.delete_webhook(drop_pending_updates=True)` → `dp.start_polling(bot)`.

### Entity Dependencies & Initialization Order
Сборка снизу вверх (порядок реализации): `config → db → services → bot → worker → main`.
`session_factory` создаётся в `main` один раз и замыкается в пишущих обработчиках и worker.
`Settings` — синглтон через `get_settings()`; валидация (токен/IANA/HH:MM/poll>0) выполняется при первом вызове до старта event loop.

---

## Code Stack Trace

Ниже — сквозные трассы ключевых точек входа с чекпойнтами типов и логики. Прочие сущности раскрыты в **Algorithm Design**.

### Trace: `main()` — запуск приложения

#### Chain
1. **Input**: процесс стартует (`python -m remindme.main`).
2. `apply_migrations()` → alembic `command.upgrade("head")` in-process; `DATABASE_URL` разрешается в `env.py` через `get_settings`. → checkpoint: миграции применены ДО polling/worker ✓ (синхронный upgrade не блокирует event loop — конкурентных корутин ещё не запущено) ✓
3. `settings = get_settings()` → первый вызов валидирует конфигурацию; при ошибке — понятный raise без токена. → checkpoint: `SecretStr`-токен не попадает в исключение ✓
4. `engine = create_engine(settings)`; `session_factory = create_session_factory(engine)` → PRAGMA `foreign_keys=ON`/`WAL` на listener `connect`; `data/` создаётся. → checkpoint: `expire_on_commit=False` ✓
5. `bot = Bot(token=settings.TELEGRAM_BOT_TOKEN.get_secret_value())`; `dp = Dispatcher()`. → checkpoint: токен взят через `get_secret_value()` ✓
6. `register_handlers(dp, session_factory)` → `set_commands(bot)`. → checkpoint: порядок регистрации (командные → remind-фраза → `handle_text` → `handle_group`) ✓
7. `asyncio.create_task(run_reminder_worker(bot, session_factory, settings.REMINDER_POLL_INTERVAL_SECONDS))`. → checkpoint: worker — фоновая задача в том же loop ✓
8. `await bot.delete_webhook(drop_pending_updates=True)`; `await dp.start_polling(bot)`.
9. **Output**: long polling крутится; worker тикает каждые `poll_interval` секунд.

#### Checkpoint Summary
- migrations-before-polling: passed
- token secrecy: passed
- single event loop (worker ∥ polling): passed

### Trace: `cmd_remind(message)` / `handle_remind_phrase(message)` — создание напоминания

#### Chain
1. **Input**: `Message` из приватного чата (`PrivateOnly`); `message.text` (≤1000 проверяется в handler).
2. Открыть сессию; `now = aware UTC` (фиксируется один раз). → checkpoint: `now` единый для разбора и проверки границ ✓
3. `user = ensure_user(session, message.from_user.id, settings.DEFAULT_TIMEZONE, now)` → `User` (создан/найден). → checkpoint: идемпотентность регистрации, `now` прокинут ✓
4. `outcome = create_reminder_scenario(session, user.telegram_user_id, message.text, now, user.timezone, settings.DEFAULT_REMINDER_TIME)`.
   - `parse = parse_remind_time(raw, now, timezone, default_time)`:
     - режим: `/remind` → split по первому `|`; фраза → шаблоны (частное→общее, `re.IGNORECASE`).
     - календарная проверка `strptime` (`31.02` → `date_not_exist`).
     - локальный naive → `ZoneInfo` со сравнением fold=0/fold=1 → `nonexistent_time`/`ambiguous_time` ИЛИ aware UTC.
     - текст: пусто → `empty_text`; >500 → `text_too_long`; `remind_at<=now` → `past`; >365 дней → `horizon_exceeded`.
     - → `ParsedReminder(remind_at_utc, text)` | `ParseError(kind)`.
   - если `ParseError` → вернуть без сохранения; иначе `create_reminder(session, telegram_user_id, parse.text, parse.remind_at_utc, now)`.
     → checkpoint: тип-поток `ParsedReminder.remind_at_utc: datetime` → `create_reminder(..., datetime)` ✓
   - `create_reminder`: datetime→ISO 8601 UTC str; `status="scheduled"`, `attempt_count=0`, `next_attempt_at_utc=remind_at_utc`; `add+commit`; при `SQLAlchemyError` → rollback, structured-лог, re-raise.
5. **Output**: `outcome` — `Reminder` или `ParseError`.
6. `Reminder` → `message.answer(format_reminder_confirmation(outcome, user.timezone))`; `ParseError` → `message.answer(format_parse_error(outcome))`.
   - `format_reminder_confirmation` → `to_local_string(reminder.remind_at_utc, timezone)`. → checkpoint: `Reminder.remind_at_utc: str` (ISO из БД) → `to_local_string(utc_iso: str)` ✓
7. (При `SQLAlchemyError`, проброшенном из репозитория) → `handle_db_error(event)`: лог ERROR (user_id, код; без текста/токена) + единый текст пользователю.

#### Checkpoint Summary
- DST до past/horizon: passed (aware-момент определён до сравнения с `now`)
- единый `now` через весь путь: passed
- тип datetime→ISO конверсия в репозитории: passed
- изоляция ошибок: ParseError не пишет в БД; SQLAlchemyError откатывается и обрабатывается централизованно

### Trace: `run_reminder_worker(bot, session_factory, poll_interval)` — доставка

#### Chain
1. **Input**: `Bot`, `session_factory`, `poll_interval`.
2. Startup-recovery (один раз): сессия → `recover_stuck_sending(session, now, 60)` → `UPDATE ... status='scheduled', locked_at_utc=NULL WHERE status='sending' AND locked_at_utc <= (now-60s)`; `next_attempt_at_utc` не пересчитывается → запись остаётся due. → checkpoint: зависшие `sending` возвращаются в очередь; просроченное доставится сразу ✓
3. Бесконечный цикл:
   1. `await asyncio.sleep(poll_interval)`.
   2. `now = aware UTC`; открыть сессию.
   3. `due = find_due_reminders(session, now)` → `status="scheduled" AND next_attempt_at_utc <= now_iso`. → checkpoint: лексикографическое сравнение ISO 8601 корректно при фиксированном формате ✓
   4. для каждого `reminder` в `due`:
      - `if claim_for_sending(session, reminder.id, now)` (атомарно `scheduled→sending`, `locked_at_utc=now`; `rowcount==1`):
        - `text = format_notification(reminder)` → `"Напоминание\n<reminder.text>"`. → checkpoint: `reminder.user_id` (= telegram_user_id) используется как `chat_id` ✓
        - `try: await bot.send_message(chat_id=reminder.user_id, text=text)` (в try — только отправка).
        - `except TelegramForbiddenError: mark_failed(session, reminder.id)`; лог WARNING (user_id, "bot blocked").
        - `except TelegramAPIError (иные ошибки отправки aiogram): failed = record_send_failure(session, reminder.id, now)`; лог WARNING (user_id, attempt_count, код, длительность).
        - `else: mark_sent(session, reminder.id, now)` — только при успешной отправке; ошибка БД из `mark_sent` не маскируется под ошибку отправки (иначе — ложный повтор/дубль).
   5. закрыть сессию (один раз за итерацию, после цикла по due).
4. **Output**: уведомления доставлены; статусы переведены (`sent`/`failed`/`scheduled`+повтор).

`record_send_failure` детально: `SELECT attempt_count`; `new_count=attempt_count+1`; `new_count>=4` → `mark_failed` (True); иначе `delay={1:30,2:120,3:600}[new_count]`, `UPDATE status='scheduled', attempt_count=new_count, next_attempt_at_utc=now+delay, locked_at_utc=NULL`. → checkpoint: ровно 4 попытки отправки (1 исходная + 3 повтора 30/120/600), 4-я неудача → `failed` ✓

#### Checkpoint Summary
- one-session-per-iteration + one recovery-at-startup: passed
- атомарный захват (`status='scheduled'` в WHERE + `rowcount==1`): passed — две итерации цикла не заберут одну запись
- отменённые (`cancelled`) не отправляются: passed (`claim_for_sending` фильтрует `scheduled`)
- ошибка одного reminder не прерывает цикл: passed
- секретность: токен и текст записи не логируются: passed

### Trace: `handle_callback(callback)` — inline-действие

#### Chain
1. **Input**: `CallbackQuery`; `callback.data` вида `<action>:<entity>:<id>`.
2. `now = aware UTC` (один раз). Разобрать по `:`: если не 3 части или `raw_id` не int → `callback.answer("Запись уже изменена или удалена.")` без обращения к БД. → checkpoint: некорректный payload не доходит до БД ✓
3. Открыть сессию; ветвление `action+entity` (изоляция по `(id, user_id)` — в репозиториях):
   - `complete:todo` → `complete_todo(session, id, user_id, now)`; не выполнено → «Запись уже изменена…»; иначе `edit_text(format_todo_list + list_todos, todos_keyboard)`.
   - `delete:note` → `delete_note`; не удалено → «Запись уже изменена…»; иначе `edit_text(format_note_list + list_notes, notes_keyboard)`.
   - `delete:todo` → `was = delete_todo`; `None` → «Запись уже изменена…»; `True`(была completed) → completed-список; `False`(была active) → todos-список. → checkpoint: `delete_todo -> bool | None` управляет целевой перерисовкой ✓
   - `delete:reminder` → `cancel_reminder_scenario`; `already_sending` → «Напоминание уже отправляется…»; `not_found` → «Запись уже изменена…»; `cancelled` → reminders-список.
4. На успех — `callback.answer()`.
5. **Output**: список перерисован (`editMessageText`) или показан alert.

#### Checkpoint Summary
- проверка владельца через `(id, user_id)`: passed — чужие/устаревшие записи не раскрываются
- маршрутизация 4 пар action:entity: passed

### Trace: `cmd_todo(message)` → `parse_todo_input` — задача (терпимый парсер)

#### Chain
1. **Input**: `raw` (аргумент `/todo`), `timezone` (IANA), `now` (aware UTC).
2. `parsed = parse_todo_input(raw, timezone)`:
   - если в `raw` есть `|` И левая часть == `ГГГГ-ММ-ДД ЧЧ:ММ` (`strptime`): `due_raw` → naive local → `ZoneInfo(timezone)` → aware UTC `due_at_utc`; `raw_text` = правая часть.
   - иначе: `due_at_utc = None`; `raw_text` = весь `raw` (включая литерал `|`).
   - `text = raw_text.strip()`; пусто → `TodoError(empty_text)`; `len>500` → `TodoError(text_too_long)`.
   - → `ParsedTodo(due_at_utc, text)`.
   - → checkpoint: «нет даты после |» НЕ ошибка — весь текст без срока (по ТЗ) ✓
3. `TodoError` → вернуть без сохранения; иначе `create_todo(session, telegram_user_id, parsed.text, parsed.due_at_utc, now)` (status=`active`; past допускается).
4. **Output**: `Todo` или `TodoError` → `format_todo_confirmation`/`format_todo_error`.

#### Checkpoint Summary
- поведение по ТЗ (lenient date-after-`|`): passed
- past-срок допускается, без DST/горизонта: passed

### Trace: `apply_migrations()` — bootstrap схемы

#### Chain
1. **Input**: нет аргументов; `DATABASE_URL` разрешается в `env.py` через `get_settings`.
2. `alembic command.upgrade("head")` программно in-process (без subprocess/CLI). Единая начальная ревизия: 4 таблицы (`users`, `reminders`, `notes`, `todos`) + 4 индекса + FK. → checkpoint: `PRAGMA foreign_keys=ON` в `run_migrations_online` ✓
3. **Output**: полная схема; идемпотентен (no-op, если актуально).

#### Checkpoint Summary
- идемпотентность: passed
- синхронный upgrade до запуска конкурентных корутин: passed

---

## Algorithm Design

### `Settings` (config)
**Ответственность**: единый источник настроек; загрузка из `.env`/окружения (env приоритетнее).
**Algorithm**: `BaseSettings` + `SettingsConfigDict(env_file=".env")`; поля `TELEGRAM_BOT_TOKEN: SecretStr`, `DATABASE_URL`, `DEFAULT_TIMEZONE`, `DEFAULT_REMINDER_TIME`, `REMINDER_POLL_INTERVAL_SECONDS: int=5`, `LOG_LEVEL="INFO"`; `@field_validator` на зону (`available_timezones()` + `ZoneInfo`), время (`HH:MM`, 0..23/0..59), интервал (`>0`).
**Errors**: отсутствие токена/неверная зона/неверное время → `ValidationError` при первом `get_settings()`; текст не содержит токен.
**Edge cases**: env переопределяет `.env`; `get_settings()` — синглтон (`functools.cache`/модульная переменная).

### `get_settings()` (config)
**Ответственность**: точка доступа к синглтону настроек.
**Algorithm**: ленивая инициализация + кеш; возвращает закешированный `Settings`.

### `Base`/`User`/`Reminder`/`Note`/`Todo` (db.models)
**Ответственность**: декларативные ORM-модели (SQLAlchemy 2.x `Mapped`/`mapped_column`). Все timestamp-поля — `Text` ISO 8601 UTC. `User.telegram_user_id` — PK+UNIQUE; FK `user_id → users.telegram_user_id` у `Reminder`/`Note`/`Todo`.
**Edge cases**: нарушение UNIQUE при конкурентной регистрации → `IntegrityError`; FK → `IntegrityError` при отсутствии `user_id`.

### `create_engine(settings)` / `create_session_factory(engine)` (db.session)
**Ответственность**: async-движок SQLite с PRAGMA и фабрика сессий.
**Algorithm**: `create_async_engine(settings.DATABASE_URL)`; `mkdir data/` при отсутствии; `@event.listens_for(engine.sync_engine, "connect")` → `PRAGMA foreign_keys=ON`, `PRAGMA journal_mode=WAL`. `async_sessionmaker(engine, expire_on_commit=False)`.

### Репозитории (db.repositories)
- **Создание** (`create_reminder`/`create_note`/`create_todo`): datetime→ISO str; `add+commit`; `SQLAlchemyError`→rollback+structured-лог+re-raise.
- **Списки** (`list_reminders`/`list_notes`/`list_todos`/`list_completed`): фильтр `user_id` (+`status`), сортировка (reminders — `remind_at_utc asc`; notes — `created_at_utc desc`; todos — `due_at_utc asc NULLS LAST, created_at_utc desc`; completed — `completed_at_utc desc`), `limit 20`.
- **Отмена/удаление/завершение** (`cancel_reminder`/`delete_note`/`delete_todo`/`complete_todo`/`set_user_timezone`): `UPDATE/DELETE ... WHERE id AND user_id (+status)`; возврат `rowcount==1` / `CancelOutcome` / `bool|None`. Чужие записи → индикатор «не найдено», существование не раскрывается.
- **Доставка** (`find_due_reminders`/`claim_for_sending`/`mark_sent`/`mark_failed`/`record_send_failure`/`recover_stuck_sending`): описано в Trace B.
**Errors**: `SQLAlchemyError` → rollback + re-raise (централизованный текст — в `handle_db_error`).
**Edge cases**: `cancel_reminder` отличает `cancelled`/`already_sending`/`not_found` дополнительным `SELECT status`.

### `parse_remind_time` (services.parser)
**Ответственность**: чистый детерминированный парсер времени напоминания с DST-валидацией.
**Algorithm**: см. Trace (5 шаблонов, режим `/remind` vs фраза, `strptime`, fold-сравнение, проверки текста/границ).
**Errors**: `ParseError(kind)` ∈ {invalid_format, date_not_exist, past, horizon_exceeded, empty_text, text_too_long, nonexistent_time, ambiguous_time}.
**Edge cases**: `now` фиксирован снаружи; DST-проверка раньше past/horizon; `через N дней` = ровно `N×24` ч.

### `parse_todo_input` (services.todos)
**Ответственность**: чистый парсер входа задачи (терпимый).
**Algorithm**: см. Trace D.
**Errors**: `TodoError(kind)` ∈ {empty_text, text_too_long}.
**Edge cases**: past допускается; без DST/горизонта; не-дата слева от `|` → весь текст без срока.

### Сценарии (services)
- `create_reminder_scenario`: parse → (ошибка? вернуть) → `create_reminder`.
- `set_timezone_scenario`: `ZoneInfo(timezone)` → (ошибка? `False`) → `set_user_timezone`.
- `cancel_reminder_scenario`: делегирует `cancel_reminder` → `CancelOutcome`.
- `create_note_scenario`: `strip`, 1–500 → `create_note`; иначе `NoteError`.
- `create_todo_scenario`: `parse_todo_input` → (ошибка? вернуть) → `create_todo`.

### `ensure_user` (bot.handlers)
**Ответственность**: идемпотентное создание/обновление `User`.
**Algorithm**: `now_iso`; `SELECT User`; нет → создать (`created=updated=now_iso`); есть → `updated=now_iso`; `commit`.
**Edge cases**: конкурентная регистрация → `IntegrityError` → повторный `SELECT` существующей записи.

### Handlers (bot.handlers)
Командные (`cmd_start`/`cmd_help`/`cmd_remind`/`cmd_reminders`/`cmd_timezone`/`cmd_cancel`/`cmd_note`/`cmd_notes`/`cmd_todo`/`cmd_todos`/`cmd_completed`), фраза (`handle_remind_phrase`), общий текст (`handle_text`), неизвестная команда (`handle_unknown_command`), группа (`handle_group`).
**Общий паттерн**: открыть сессию → `now` → `ensure_user` → сценарий/репозиторий → форматтер → `message.answer`/`edit_text`. `PrivateOnly` на пишущих; `handle_group` без фильтра последним.
**Edge cases**: длина сообщения >1000 → отказ до разбора; групповой чат → фиксированный ответ без записи.

### `handle_callback` (bot.callbacks)
**Ответственность**: единый роутер inline-действий. См. Trace C.

### Formatter'ы (bot.formatter)
Чистые функции: `format_*` (подтверждения/списки/ошибки/уведомление), `to_local_string(utc_iso, tz)` — ISO 8601 UTC str → aware → целевая зона → локальная строка `DD.MM.YYYY HH:MM`.

### Клавиатуры (bot.keyboards)
`notes_keyboard`/`todos_keyboard`/`completed_keyboard`/`reminders_keyboard`: билдеры `InlineKeyboardMarkup`, `callback_data = <action>:<entity>:<id>` (≤64 байт); пустой список → `None`.

### `run_reminder_worker` (worker.notifications)
См. Trace B.

### `apply_migrations` / `set_commands` / `main` (main)
См. Trace `main` и Trace `apply_migrations`.

---

## Cross-cutting Concerns

- **Error handling**: репозиторий откатывает транзакцию и re-raise'ит `SQLAlchemyError`; централизованный `handle_db_error` (errors-handler aiogram) ловит только `SQLAlchemyError`, логирует user_id + код, отвечает единым текстом. Парсер/сценарии возвращают типизированные ошибки (`ParseError`/`NoteError`/`TodoError`/`CancelOutcome`) — без исключений для пользовательских ошибок формата.
- **Logging**: `logging` (structlog-стиль `extra={...}`); уровни по `conventions`. lifecycle/state-переходы → INFO; повторы/блокировка → WARNING; невыполненные операции БД → ERROR. Никогда не логируются токен, полный текст записи, персональные данные.
- **Validation**: конфиг — при `get_settings()` (токен/IANA/HH:MM/poll>0); напоминание — в `parse_remind_time` (формат/дата/DST/past/горизонт/длина); note/todo — в сценариях (1–500); длина сообщения (≤1000) — в handler; IANA зоны — `ZoneInfo`/`available_timezones`.
- **Caching**: `Settings` — синглтон (`get_settings`); engine/session_factory — один раз в `main`. ORM-кеша/прикладного кеша нет.
- **Concurrency**: один процесс, один экземпляр, один event loop; worker ∥ polling. Атомарный захват напоминаний через `UPDATE ... WHERE status='scheduled'` + `rowcount==1` (без отдельной блокировки). SQLite/WAL + один писатель (конкуренции за запись нет). `now` фиксируется на сообщение/итерацию.

---

## Usages Analysis

### `conventions` (.goga/usages/conventions.md)
- **What**: общие правила Python (relative imports, pydantic `kw_only`, structured logging, Google-docstrings, тесты mirror src, ruff/pytest).
- **Where used**: всеми клетками (глобальная практика).
- **Why chosen**: единый стиль и тестовая дисциплина.
- **How exactly**: `from .models import X`; pydantic `model_config(kw_only=True)`; `logger.info(..., extra={...})`; `tests/<pkg>/test_<module>.py`.

### `pydantic-settings` (config)
- **What**: `BaseSettings`, `SecretStr`, `@field_validator`.
- **Where used**: `Settings`, `get_settings`.
- **How exactly**: `SettingsConfigDict(env_file=".env")`; `TELEGRAM_BOT_TOKEN.get_secret_value()`.

### `sqlalchemy` (db, bot)
- **What**: async ORM 2.x, PRAGMA listener, `rowcount`, `nullslast`.
- **Where used**: модели, репозитории, `SQLAlchemyError` в `handle_db_error`.
- **How exactly**: `create_async_engine`, `event.listens_for(engine.sync_engine,"connect")`, `select/update/delete` + `.where(user_id==..., status==...)`, `result.rowcount`.

### `aiosqlite` (db)
- **What**: async-драйвер SQLite (диалект `sqlite+aiosqlite`).
- **Where used**: `DATABASE_URL`, тесты (`tmp_path`).

### `aiogram` (bot, worker, main)
- **What**: Bot 3.x, Dispatcher, фильтры (`Command`, `BaseFilter`), `callback_query`, errors-handler, `set_my_commands`, `send_message`/`edit_text`/`callback.answer`.
- **Where used**: handlers, callbacks, keyboards, worker (отправка), main (polling).
- **How exactly**: `PrivateOnly(BaseFilter)`; порядок регистрации; `TelegramForbiddenError` → `mark_failed`.

### `alembic` (main)
- **What**: версионирование схемы; единая начальная ревизия.
- **Where used**: `apply_migrations`.
- **How exactly**: `alembic init -t async`; `env.py` берёт `DATABASE_URL` из `get_settings`; `command.upgrade("head")` in-process; `PRAGMA foreign_keys=ON` в online-контексте.

### `timezone` (services, inline practice)
- **What**: валидация/локализация времени через stdlib `zoneinfo`; DST-детекция fold=0/fold=1.
- **Where used**: `parse_remind_time`, `set_timezone_scenario`, `parse_todo_input`.
- **How exactly**: `ZoneInfo(name)`; сравнение `utcoffset` для fold=0/1 → `nonexistent_time`/`ambiguous_time`.

### `deployment` (main, project-level)
- **What**: упаковка в Docker Compose (один сервис, том `data/`, `.env` через env_file — без `COPY .env`).
- **Where used**: `main` (точка входа применяет миграции в процессе).

### Imported Usages (cross-cell)
- `settings` from `config` — потребление `get_settings()` (`src/remindme/config/.usages/settings.md`); трассируемая связь для bot/worker/main.
- `models`, `repositories` from `db` — ORM-модели и репозитории для bot/services (`src/remindme/db/.usages/{models,repositories}.md`).
- `engine` from `db` — bootstrap движка/сессий для main (`src/remindme/db/.usages/engine.md`).
- `reminders`, `parser`, `notes`, `todos` from `services` — сценарии и DTO для bot (`src/remindme/services/.usages/*.md`).
- `handlers`, `formatter` from `bot` — фасад регистрации и `format_notification` для worker/main.

---

## `.usages/` Update

### Cell: `src/remindme/services`

#### Existing Files — Consistency
- **`todos.md`** → `src/remindme/services/.usages/todos.md`
  - Status: **current** — `TodoError.kind` уже без `invalid_format` (`empty_text / text_too_long`); в секции «Формат срока» уже зафиксировано правило «`|` присутствует, но левая часть — не дата → весь аргумент сохраняется как текст без срока». Правок не требуется.

### Cell: `src/remindme/bot`

#### Existing Files — Consistency
- **`formatter.md`** → `src/remindme/bot/.usages/formatter.md`
  - Status: **current** — комментарий к `format_todo_error` уже без `invalid_format` (`empty_text / text_too_long`). Правок не требуется.
- **`handlers.md`**, **`testing.md`** → current (правило явного `now` уже зафиксировано в `testing.md`; сигнатура `ensure_user` в `.usages` напрямую не демонстрируется).

#### New Files
Не требуются — все домены (settings/engine/models/repositories/parser/reminders/notes/todos/handlers/callbacks/testing/formatter/running) уже покрыты существующими файлами.

### Остальные клетки
- **config**, **db**, **worker**, **main**: `.usages/` соответствуют текущему CODEMANIFEST; изменений нет.

---

## Test Stack Trace

### General Setup
- `tests/conftest.py`: `session` (через `tmp_path` + `Base.metadata.create_all` на временном `sqlite+aiosqlite`-движке, `expire_on_commit=False`, `foreign_keys=ON`); `fixed_now` (явный aware UTC, напр. `datetime(2026,6,24,12,0,tzinfo=UTC)`); `bot` mock (`AsyncMock`); `pyproject` → `asyncio_mode=auto`, `ruff`, `[project.optional-dependencies].test`.
- Структура: `tests/{config,db,services,bot,worker,main}/test_<module>.py`; интеграция — `tests/` напрямую.
- Время — только явное (`fixed_now`); реальный `sleep` не используется; Telegram API подменяется mock, кроме ручного e2e smoke (`pytest.mark.skipif`).

### Source File Registry
`config.py`; `db/{models,session,repositories}.py`; `services/{parser,reminders,notes,todos}.py`; `bot/{handlers,callbacks,formatter,keyboards}.py`; `worker/notifications.py`; `main/main.py`.

---

### Positive Tests

#### `test_parse_remind_time_relative_minutes`
**Setup**: `fixed_now = datetime(2026,6,24,12,0,0, tzinfo=UTC)`; `timezone="Europe/Moscow"`; `default_time="09:00"`.
**Input**: `raw="напомни мне через 15 минут проверить духовку"`.
**Trace**:
```
parse_remind_time(raw, fixed_now, "Europe/Moscow", "09:00")
  → режим «фраза», шаблон «через N минут» (IGNORECASE), N=15, text="проверить духовку"
  → remind_at = fixed_now + 15 мин = 2026-06-24 12:15:00 UTC
  → DST: +15 мин, fold-расхождений нет → aware UTC
  → текст не пуст, ≤500, >now, ≤365 дней
  → ParsedReminder(remind_at_utc=12:15:00 UTC, text="проверить духовку")
```
**Assertions**: `isinstance(r, ParsedReminder)`; `r.remind_at_utc == fixed_now+timedelta(minutes=15)`; `r.text == "проверить духовку"`.
**Sufficiency**: критерий приёмки «Относительное время»; регресс на сдвиг момента и очистку текста.

#### `test_parse_remind_time_absolute_command`
**Setup**: `fixed_now = datetime(2026,6,23,12,0,tzinfo=UTC)`; `timezone="Europe/Moscow"`.
**Input**: `raw="/remind 2026-06-24 18:00 | Купить продукты"`.
**Trace**: режим `/remind` → split по первому `|` → left=`"2026-06-24 18:00"`, right=`"Купить продукты"`; strptime OK; локализация Moscow (UTC+3) → `2026-06-24 15:00:00 UTC`.
**Assertions**: `r.remind_at_utc == datetime(2026,6,24,15,0,tzinfo=UTC)`; `r.text == "Купить продукты"`.
**Sufficiency**: критерий «Абсолютное время»; режим `/remind` и split по `|`.

#### `test_worker_delivers_due_reminder`
**Setup**: в БД `Reminder(status="scheduled", next_attempt_at_utc=fixed_now-10s, user_id=42, text="X")`; `bot.send_message = AsyncMock()`.
**Input**: один тик worker с `now=fixed_now`.
**Trace**:
```
recover_stuck_sending(now,60) → 0 (нет зависших)
find_due_reminders(now) → [reminder]
claim_for_sending(reminder.id, now) → True (status scheduled→sending)
format_notification(reminder) → "Напоминание\nX"
bot.send_message(chat_id=42, text="Напоминание\nX")
mark_sent(reminder.id, now) → status="sent", sent_at_utc=now_iso
```
**Assertions**: `bot.send_message.await_count == 1`; `send_message` called with `chat_id=42`; в БД `Reminder.status == "sent"`.
**Sufficiency**: критерий «Доставка уведомления»; атомарный захват + переход в `sent`.

#### `test_complete_todo_callback`
**Setup**: `Todo(id=7, user_id=42, status="active")`; `callback.data="complete:todo:7"`, `callback.from_user.id=42`.
**Input**: `handle_callback(callback)`.
**Trace**: разбор → `complete_todo(session, 7, 42, now)` → `UPDATE ... WHERE id=7 AND user_id=42 AND status='active'` → `rowcount==1` → `True` → `edit_text(format_todo_list + list_todos, todos_keyboard)`; `callback.answer()`.
**Assertions**: в БД `Todo.status == "completed"`, `completed_at_utc` заполнен; `callback.answer` вызван.
**Sufficiency**: критерий «Управление задачей».

---

### Negative Tests

#### `test_parse_remind_time_past`
**Setup**: `fixed_now`, `timezone="Europe/Moscow"`, локальное «сегодня 19:00» при текущем 20:00.
**Input**: `raw="напомни сегодня в 19:00 проверить почту"`.
**Trace**: локализация → aware UTC < `fixed_now` → `ParseError(kind="past")`.
**Assertions**: `isinstance(r, ParseError)`; `r.kind == "past"`.
**Sufficiency**: критерий «Напоминание в прошлом».

#### `test_parse_remind_time_unsupported_phrase`
**Input**: `raw="напомни в пятницу вечером позвонить врачу"`.
**Trace**: ни один из 5 шаблонов не matched → `ParseError(kind="invalid_format")`.
**Assertions**: `r.kind == "invalid_format"`.
**Sufficiency**: критерий «Неподдерживаемая свободная фраза».

#### `test_callback_foreign_record_isolation`
**Setup**: `Reminder(id=15, user_id=999)` (чужой); `callback.data="delete:reminder:15"`, `callback.from_user.id=42`.
**Input**: `handle_callback(callback)`.
**Trace**: `cancel_reminder_scenario(session, 42, 15)` → `cancel_reminder` `UPDATE ... WHERE id=15 AND user_id=42 AND status='scheduled'` → `rowcount==0` → доп. `SELECT` → `not_found` → `callback.answer("Запись уже изменена или удалена.")`.
**Assertions**: запись 999 не изменена; `callback.answer` вызван с текстом «Запись уже изменена или удалена.».
**Sufficiency**: критерий «Изоляция данных».

#### `test_worker_forbidden_marks_failed`
**Setup**: `Reminder(status="scheduled", ...)`; `bot.send_message` raises `TelegramForbiddenError`.
**Input**: один тик worker.
**Trace**: claim OK → `send_message` → `TelegramForbiddenError` → `mark_failed(reminder.id)` → `status="failed"`.
**Assertions**: `Reminder.status == "failed"`; `bot.send_message.await_count == 1` (без повторов).
**Sufficiency**: решение «немедленный failed при Forbidden».

---

### Edge Case Tests

#### `test_parse_todo_input_nondate_after_pipe`
**Input**: `raw="не дата | Купить хлеб"`, `timezone="Europe/Moscow"`.
**Trace**: `|` есть, левая часть не `ГГГГ-ММ-ДД ЧЧ:ММ` → `due_at_utc=None`, `raw_text="не дата | Купить хлеб"`, `text="не дата | Купить хлеб"` (≤500) → `ParsedTodo(None, "не дата | Купить хлеб")`.
**Assertions**: `parsed.due_at_utc is None`; `parsed.text == "не дата | Купить хлеб"`; не `TodoError`.
**Sufficiency**: поведение по ТЗ (lenient); регресс на устранённый `invalid_format`.

#### `test_parse_remind_time_dst_nonexistent`
**Setup**: зона с весенним переводом; локальное время в провале.
**Input**: соответствующая фраза.
**Trace**: fold=0/fold=1 дают разный `utcoffset` → `ParseError(kind="nonexistent_time")`.
**Assertions**: `r.kind == "nonexistent_time"`.
**Sufficiency**: TZ-требование по DST.

#### `test_worker_record_send_failure_schedule`
Параметризация по `attempt_count` ∈ {0, 1, 2, 3} (статус записи до вызова — `sending`).
**Setup**: `fixed_now = datetime(2026,6,24,12,0,0,tzinfo=UTC)`; в БД `Reminder(id=R, user_id=42, status="sending", attempt_count=<param>, next_attempt_at_utc=fixed_now, text="X")`; `bot.send_message` поднимает ошибку отправки (`TelegramAPIError`).
**Input**: один тик worker с `now=fixed_now`; `claim_for_sending` уже отработал (запись в `sending`), далее `record_send_failure(session, R, fixed_now)`.
**Trace**:
```
record_send_failure(session, R, fixed_now):
  SELECT attempt_count WHERE id=R → <param>
  new_count = <param> + 1
  param=0 → new_count=1 → delay=30 → UPDATE status='scheduled', attempt_count=1,
            next_attempt_at_utc=fixed_now+30s, locked_at_utc=NULL WHERE id=R → вернуть False
  param=1 → new_count=2 → delay=120 → UPDATE ... attempt_count=2, +120s → False
  param=2 → new_count=3 → delay=600 → UPDATE ... attempt_count=3, +600s → False
  param=3 → new_count=4 ≥4 → mark_failed(session, R) → UPDATE status='failed' WHERE id=R AND status='sending' → вернуть True
```
**Assertions** (parametrize):
- `attempt_count=0` → в БД `status=="scheduled"`, `attempt_count==1`, `next_attempt_at_utc==fixed_now+timedelta(seconds=30)`, `locked_at_utc is None`; `record_send_failure` вернул `False`.
- `attempt_count=1` → `attempt_count==2`, `next_attempt_at_utc==fixed_now+timedelta(seconds=120)`.
- `attempt_count=2` → `attempt_count==3`, `next_attempt_at_utc==timedelta(seconds=600)`.
- `attempt_count=3` → `status=="failed"`; `record_send_failure` вернул `True`.
**Sufficiency**: критерий «повторы 30/120/600 и failed на 4-й неудаче»; регресс на расписание повторов и эскалацию. До правки fixture (`0..2`) не достигала 4-й неудачи — переход в `failed` не проверялся.

#### `test_recover_stuck_sending_at_startup`
**Setup**: `Reminder(status="sending", locked_at_utc=now-120s)`.
**Input**: `recover_stuck_sending(now, 60)`.
**Trace**: `UPDATE status='scheduled', locked_at_utc=NULL WHERE status='sending' AND locked_at_utc<=now-60s` → `rowcount=1`; `next_attempt_at_utc` не меняется → `find_due_reminders` подхватит.
**Assertions**: `status=="scheduled"`; `find_due_reminders` возвращает запись.
**Sufficiency**: критерий «Восстановление после перезапуска».

#### `test_settings_idempotent_start`
**Setup**: `/start` дважды от одного `telegram_user_id`.
**Input**: `ensure_user(..., now)` ×2.
**Assertions**: одна запись `User`; `updated_at_utc` обновлён; `created_at_utc` неизменен.
**Sufficiency**: идемпотентность регистрации.

---

### Дополнительные параметризованные и граничные тесты

Эти тесты закрывают distinct-ветви `ParseError` (есть тексты в `format_parse_error`, но не было тестов), параметризованные наборы парсера и границы, требуемые ТЗ (577–584).

#### `test_parse_remind_time_date_not_exist`
**Setup**: `fixed_now = datetime(2026,6,24,12,0,0,tzinfo=UTC)`; `timezone="Europe/Moscow"`.
**Input**: `raw="напомни 31.02.2026 в 18:00 совещание"`.
**Trace**: фразовый режим → strip «напомни» → шаблон «ДД.ММ.ГГГГ в ЧЧ:ММ» сматчился (31, 02, 2026, 18:00) → календарная проверка `strptime("%d.%m.%Y")` → 31 февраля не существует → `ParseError(kind='date_not_exist')` (до локализации/DST).
**Assertions**: `isinstance(r, ParseError)`; `r.kind == "date_not_exist"`.
**Sufficiency**: критерий «корректные и некорректные даты»; регресс, что календарная проверка раньше локализации.

#### `test_parse_remind_time_horizon_exceeded`
**Setup**: `fixed_now = datetime(2026,6,24,12,0,0,tzinfo=UTC)`; `timezone="Europe/Moscow"`.
**Input**: `raw="напомни через 400 дней долгий план"` (400×24ч > 365 дней).
**Trace**: шаблон «через N дней», N=400 → `remind_at = fixed_now + 400×24ч` → aware UTC → DST-расхождения нет → текст не пуст, ≤500 → `remind_at > now`, но горизонт > 365 дней → `ParseError(kind='horizon_exceeded')`.
**Assertions**: `r.kind == "horizon_exceeded"`.
**Sufficiency**: граница 365 дней (ТЗ 584 «за пределами 365 дней»); пара с тестом «в пределах 365».

#### `test_parse_remind_time_horizon_boundary_in_range`
**Setup**: `fixed_now`; `timezone="Europe/Moscow"`.
**Input**: `raw="напомни через 365 дней годовой план"`.
**Trace**: N=365 → `remind_at = fixed_now + 365×24ч` → ≤365 дней → `ParsedReminder`.
**Assertions**: `isinstance(r, ParsedReminder)`; `r.remind_at_utc == fixed_now + timedelta(days=365)`.
**Sufficiency**: граница «ровно 365» принимается; регресс на off-by-one горизонта.

#### `test_parse_remind_time_text_too_long`
**Setup**: `fixed_now`; `timezone="Europe/Moscow"`.
**Input**: `raw="напомни через 15 минут " + "а"*501` (текст 501 символ).
**Trace**: шаблон «через N минут» сматчился, N=15, text = 501 «а» → aware UTC OK → `len(text) > 500` → `ParseError(kind='text_too_long')`.
**Assertions**: `r.kind == "text_too_long"`.
**Sufficiency**: граница длины текста записи (ТЗ 583 «501 символ»); пара с 1/500.

#### `test_parse_remind_time_text_length_boundaries`
**Setup**: `fixed_now`; `timezone="Europe/Moscow"`. Параметризация: текст 1, 500, 501 символ.
**Input**: `raw="напомни через 1 минуту " + "x"*k`, k ∈ {0(т.е. 1 символ «x»), 499, 500} → текст 1/500/501.
**Trace**: при k=500 → text 501 → `text_too_long`; k=499 → text 500 → `ParsedReminder`; 1 символ → `ParsedReminder`.
**Assertions**: k=500 → `r.kind == "text_too_long"`; k∈{0,499} → `isinstance(r, ParsedReminder)`.
**Sufficiency**: границы 1/500/501 (ТЗ 583).

#### `test_parse_remind_time_empty_text`
**Setup**: `fixed_now`; `timezone="Europe/Moscow"`.
**Input**: `raw="напомни через 15 минут "` (после очистки временной части текст пуст).
**Trace**: шаблон сматчился, N=15, text после strip == "" → `ParseError(kind='empty_text')`.
**Assertions**: `r.kind == "empty_text"`.
**Sufficiency**: запрет пустого текста записи (ТЗ 288).

#### `test_parse_remind_time_ambiguous_time`
**Setup**: зона с осенним переводом (напр. `Europe/Moscow` в исторический год перевода, или `America/New_York`); `fixed_now` так, чтобы целевое локальное время попадало в осеннее наложение.
**Input**: соответствующая фраза с локальным временем в наложении.
**Trace**: fold=0 и fold=1 дают разный `utcoffset` (оба существуют, но различаются) → `ParseError(kind='ambiguous_time')`.
**Assertions**: `r.kind == "ambiguous_time"`.
**Sufficiency**: парный DST-кейс к `nonexistent_time`; критерий «неоднозначное локальное время отклоняется» (ТЗ 224).

#### `test_parse_remind_time_unit_forms` (parametrize)
**Setup**: `fixed_now`; `timezone="Europe/Moscow"`. Параметризация по `(единица, секунд_на_единицу)`:
`("минуту",60), ("минуты",60), ("минут",60), ("час",3600), ("часа",3600), ("часов",3600), ("день",86400), ("дня",86400), ("дней",86400)`; N=2 (согласование не проверяется — «2 часов» принимается).
**Input**: `raw=f"напомни через 2 {единица} дело"` (без «мне», нижний регистр); плюс варианты с «мне», в верхнем регистре, с `|`.
**Trace**: каждый шаблон сматчивается → `remind_at = fixed_now + 2×секунд_на_единицу` → `ParsedReminder`.
**Assertions**: для каждого случая `isinstance(r, ParsedReminder)` и `r.remind_at_utc == fixed_now + timedelta(seconds=2*секунд_на_единицу)`; вариант «2 часов» → 2 часа (согласование не валидируется).
**Sufficiency**: все формы единиц и регистронезависимость (ТЗ 579–580).

#### `test_parse_remind_time_n_boundaries` (parametrize)
**Setup**: `fixed_now`; `timezone="Europe/Moscow"`.
**Input**: `raw=f"напомни через {N} минут дело"`, N ∈ {1, большое (напр. 525600)}.
**Trace**: N=1 → +60с → `ParsedReminder`; большое N → если горизонт ≤365д → `ParsedReminder`, иначе `horizon_exceeded`.
**Assertions**: N=1 → `r.remind_at_utc == fixed_now+timedelta(minutes=1)`; N, выходящее за 365д в минутах → `r.kind=="horizon_exceeded"`.
**Sufficiency**: положительность N и горизонт (ТЗ 584).

#### `test_list_reminders_limit`
**Setup**: в БД 21 `Reminder(user_id=42, status="scheduled", remind_at_utc=…)` с возрастающими `remind_at_utc`; `user_id=42`.
**Input**: `list_reminders(session, 42)`.
**Trace**: `select … order_by remind_at_utc asc limit 20` → возвращено 20 (21-я отсечена).
**Assertions**: `len(reminders) == 20`; возвращены 20 ближайших по времени.
**Sufficiency**: лимит 20 (ТЗ 170); изоляция — только user_id=42.

#### `test_list_todos_nulls_last`
**Setup**: `Todo(user_id=42, due_at_utc=None)` и `Todo(user_id=42, due_at_utc=…)` активные; `user_id=42`.
**Input**: `list_todos(session, 42)`.
**Trace**: `order_by due_at_utc asc NULLS LAST, created_at_utc desc` → сначала со сроком, затем без срока.
**Assertions**: все записи со сроком идут раньше записей с `due_at_utc is None`.
**Sufficiency**: «сначала со сроком, затем без срока» (ТЗ 172).

#### `test_settings_poll_interval_validation` (parametrize)
**Setup**: конструирование `Settings(REMINDER_POLL_INTERVAL_SECONDS=N)`, N ∈ {0, -1, -5}.
**Input**: `Settings(...)` с невалидным интервалом.
**Trace**: `validate_poll_interval(N)` → N ≤ 0 → `ValueError`.
**Assertions**: `pytest.raises(ValidationError)`; текст ошибки не содержит токен.
**Sufficiency**: запуск с poll ≤ 0 завершается понятной ошибкой без токена (ТЗ 443); граница >0.

#### `test_message_length_boundary` (parametrize)
**Setup**: `fixed_now`; приватный чат; `user_id=42`.
**Input**: `/remind через 15 минут <текст>`, полная длина `message.text` ∈ {1000, 1001}.
**Trace**: длина 1000 → проходит длину-чек → далее разбор; 1001 → отказ до разбора (ответ об ограничении).
**Assertions**: 1000 → доходит до `create_reminder_scenario`; 1001 → `message.answer` про ограничение, в БД напоминание не создаётся.
**Sufficiency**: граница длины сообщения 1000 (ТЗ 286).

---

## Additional Instructions for the Implementation Agent

- Реализовывать строго снизу вверх: `config → db → services → bot → worker → main`; после каждой клетки — `goga lint` и фасад-чек (`python -c "from remindme.<cell> import ..."`).
- `now` (aware UTC) — всегда параметр; `datetime.now()` допустимо вызывать только в handler'ах (одно на сообщение/итерацию) и в цикле worker. В `ensure_user`, парсерах и репозиториях — никогда.
- ISO 8601 UTC — единственный формат timestamp-полей в БД (`Text`); сравнение `next_attempt_at_utc <= now_iso` — лексикографическое при фиксированном формате.
- Изоляция по владельцу: любой读写 пользовательской сущности — через `(id, user_id)`; поиск только по `id` запрещён (включая callback'и).
- `SQLAlchemyError` откатывается и re-raise'ится в репозитории; пользовательский текст — только в `handle_db_error`. Токен и текст записи — никогда в логи/исключения/ответы.
- DST-проверка в `parse_remind_time` — до проверок past/horizon; `parse_todo_input` — без DST/горизонта, past допускается.
- `/todo`: `|` разделяет срок только при валидной дате слева; иначе весь аргумент — задача без срока (не ошибка).
- Worker: одна итерация — одна сессия; recovery один раз при старте; Forbidden → `mark_failed` сразу; прочие ошибки → `record_send_failure` (30/120/600, failed на 4-й).
- После реализации — прогнать `pytest tests/ -x`, `ruff check src/`, `ruff format --check` и сверить 11 критериев приёмки ТЗ (`.usages/todos.md` и `.usages/formatter.md` уже актуальны — `invalid_format` убран, lenient-правило `|` присутствует; отдельных правок usages не требуется).
