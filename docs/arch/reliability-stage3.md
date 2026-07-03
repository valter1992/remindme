# Architecture Plan — RemindMe Этап 3: Надёжность

> Тема: `reliability-stage3`. Серия задач RemindMe (этап 3). Расширяет существующий вертикальный срез этапа 2 слоем
> надёжности доставки и времени: повторные попытки с эскалацией до `failed`, восстановление зависших записей при
> старте, команда `/timezone`, DST-валидация локального времени, централизованный обработчик ошибок БД,
> structured-логирование без чувствительных данных.
>
> План содержит **только** артефакты CODEMANIFEST и `.usages/` (без кода реализации). Все 5 клеток — **модификации
> существующих**; для CODEMANIFEST указаны diff'ы (ADD/CHANGE), для `.usages/` — полные обновлённые блоки.

---

## Topic

- Имя темы: `reliability-stage3`
- Путь плана: `docs/arch/reliability-stage3.md`

---

## Implementation Order

Порядок leaves → root (сначала клетки без зависимостей по новым артефактам, затем зависимые):

1. **`src/remindme/db`** — leaf по новым артефактам (зависит только от `config`, неизменного). Предоставляет новые
   репозиторий-примитивы (`record_send_failure`, `mark_failed`, `recover_stuck_sending`, `set_user_timezone`),
   расширяет `cancel_reminder` и вводит DTO `CancelOutcome`. Эти типы потребляются клетками `services` и `worker`.
2. **`src/remindme/services`** — зависит от `db`. Предоставляет `set_timezone_scenario`, расширяет
   `cancel_reminder_scenario` (возврат `CancelOutcome`) и парсер DST-валидацией. Потребляется клеткой `bot`.
3. **`src/remindme/bot`** — зависит от `services`, `db`. Добавляет `cmd_timezone`, `handle_db_error`, расширяет
   `register_handlers` и `format_parse_error`. Потребляется клеткой `main` (и `worker` через `format_notification` —
   без изменений).
4. **`src/remindme/worker`** — зависит от `db`, `bot`. Расширяет `run_reminder_worker` (recovery, повторы, `failed`,
   блокировка). Потребляется клеткой `main`.
5. **`src/remindme/main`** — root. Зависит от `bot`, `worker`. Расширяет `set_commands` (`/timezone`, `/cancel`).
   Recovery выполняется внутри `worker`, поэтому `main` не вводит новых Imports.

---

## Artifacts

### Cell: `src/remindme/db` — MODIFIED

#### CODEMANIFEST diff

- **Header** — без изменений (`Imports` из `config`; `Usages`: `conventions`, `sqlalchemy`, `aiosqlite`; global
  `Annotations` без изменений).
- **Body — ADD** типы: `CancelOutcome`, `record_send_failure`, `mark_failed`, `recover_stuck_sending`,
  `set_user_timezone`.
- **Body — CHANGE** тип `cancel_reminder`: тип возврата `cancelled: bool` → `outcome: CancelOutcome`; алгоритм
  различает `cancelled` / `already_sending` / `not_found`.
- **Footer — CHANGE** `Description`.

**ADD — `CancelOutcome` (Entity):**

```yaml
"CancelOutcome(kind: str)":
  location: repositories.py
  annotations: |
    Исход отмены напоминания (DTO, pydantic kw_only=True). Producer — cancel_reminder.

    `kind`: вариант исхода

    Requirements:
    - pydantic с kw_only=True

    Use `conventions` (kw_only).
  properties:
    "kind -> str": |
      Вариант исхода: cancelled / not_found / already_sending.
```

**ADD — `record_send_failure` (Routine):**

```yaml
"record_send_failure(session: AsyncSession, reminder_id: int, now: datetime) -> failed: bool":
  location: repositories.py
  annotations: |
    Записывает неудачу отправки reminder и решает повтор или переход в failed.

    `session`: открытая AsyncSession
    `reminder_id`: идентификатор записи (status='sending')
    `now`: текущий момент (aware UTC)
    `failed`: True, если запись переведена в failed (4-я неудача); False — назначен повтор

    Algorithm:
    1. SELECT attempt_count WHERE id=`reminder_id` (статус отправляемой записи — 'sending')
    2. new_count = attempt_count + 1
    3. Если new_count >= 4 — вызвать `mark_failed` и вернуть True
    4. Иначе: delay = {1:30, 2:120, 3:600}[new_count]; UPDATE status='scheduled',
       attempt_count=new_count, next_attempt_at_utc=(now+delay) в ISO 8601 UTC, locked_at_utc=NULL WHERE id
    5. commit; вернуть False

    Requirements:
    - расписание повторов 30/120/600 по attempt_count (ключ — new_count после инкремента)
    - 4-я неудача делегирует `mark_failed`

    Constraints:
    - лог ERROR без текста записи и токена (structured logging)

    Use `sqlalchemy` (update, rowcount); `conventions` (ISO 8601, structured logging).
```

**ADD — `mark_failed` (Routine):**

```yaml
"mark_failed(session: AsyncSession, reminder_id: int) -> marked: bool":
  location: repositories.py
  annotations: |
    Переход записи sending→failed (примитив для блокировки бота и 4-й неудачи).

    `session`: открытая AsyncSession
    `reminder_id`: идентификатор записи
    `marked`: True, если ровно одна sending-запись переведена в failed

    Algorithm:
    1. UPDATE Reminder set status='failed' WHERE id=`reminder_id` AND status='sending'
    2. commit
    3. Вернуть result.rowcount == 1

    Constraints:
    - не инкрементит attempt_count; используется для немедленного failed при блокировке бота

    Use `sqlalchemy` (update, rowcount).
```

**ADD — `recover_stuck_sending` (Routine):**

```yaml
"recover_stuck_sending(session: AsyncSession, now: datetime, stale_after_seconds: int = 60) -> recovered: int":
  location: repositories.py
  annotations: |
    Возвращает зависшие sending (locked_at_utc старше stale_after_seconds) в scheduled.

    `session`: открытая AsyncSession
    `now`: текущий момент (aware UTC)
    `stale_after_seconds`: порог «зависания» (default 60)
    `recovered`: число возвращённых записей

    Algorithm:
    1. threshold_iso = (now - `stale_after_seconds`) в ISO 8601 UTC
    2. UPDATE Reminder set status='scheduled', locked_at_utc=NULL
       WHERE status='sending' AND locked_at_utc <= threshold_iso
    3. commit
    4. Вернуть result.rowcount

    Requirements:
    - next_attempt_at_utc не пересчитывается — запись остаётся due и доставляется сразу

    Constraints:
    - вызывается ровно один раз при старте worker

    Use `sqlalchemy` (update, rowcount).
```

**ADD — `set_user_timezone` (Routine):**

```yaml
"set_user_timezone(session: AsyncSession, telegram_user_id: int, timezone: str, now: datetime) -> updated: bool":
  location: repositories.py
  annotations: |
    Смена users.timezone по владельцу.

    `session`: открытая AsyncSession
    `telegram_user_id`: идентификатор Telegram-пользователя
    `timezone`: IANA-зона (валидирована сценарием)
    `now`: текущий момент (aware UTC) для updated_at_utc
    `updated`: True, если зона обновлена

    Algorithm:
    1. UPDATE User set timezone=`timezone`, updated_at_utc=now_iso WHERE telegram_user_id=`telegram_user_id`
    2. commit
    3. Вернуть result.rowcount == 1

    Constraints:
    - не валидирует зону (валидация IANA — в сценарии services)
    - не двигает уже созданные напоминания (меняется только отображение)

    Use `sqlalchemy` (update, rowcount); `conventions` (ISO 8601).
```

**CHANGE — `cancel_reminder` (Routine):**

```yaml
"cancel_reminder(session: AsyncSession, reminder_id: int, user_id: int) -> outcome: CancelOutcome":
  location: repositories.py
  annotations: |
    Отмена напоминания с различимыми исходами (scheduled→cancelled / already_sending / not_found).

    `session`: открытая AsyncSession
    `reminder_id`: идентификатор записи
    `user_id`: идентификатор Telegram-пользователя (владелец)
    `outcome`: CancelOutcome с kind cancelled / not_found / already_sending

    Algorithm:
    1. UPDATE Reminder set status='cancelled' WHERE id=`reminder_id` AND user_id=`user_id` AND status='scheduled'
    2. commit
    3. Если result.rowcount == 1 — вернуть CancelOutcome(kind='cancelled')
    4. Иначе SELECT status WHERE id AND user_id: status='sending' → 'already_sending'; иначе → 'not_found'

    Requirements:
    - фильтр (id, user_id, status='scheduled') + rowcount отличает «отменено»
    - дополнительный select distinguishes «уже отправляется» от «не найдено»

    Constraints:
    - не раскрывает существование чужих записей (возврат not_found)

    Use `sqlalchemy` (update, select, rowcount).
```

**CHANGE — Footer `Description`:**

```yaml
Description: |
  Слой данных RemindMe: декларативные модели и async-подключение к SQLite; репозиторий напоминаний
  (CRUD, статусные переходы, повторы/failed и восстановление зависших sending) с изоляцией по владельцу.
```

#### `.usages/` files

**`src/remindme/db/.usages/repositories.md` — UPDATE:** добавить блоки (после существующей секции
«Worker: выборка, захват, отметка об отправке»):

````md
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

Расписание повторов зафиксировано: `attempt_count` 1→+30с, 2→+120с, 3→+600с, 4→`failed`. `record_send_failure` на 4-й
неудаче делегирует `mark_failed`.

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
````

Также обновить строку «Базовый путь этапа 2: без повторных попыток…» → «Этап 3: повторные попытки 30/120/600, `failed`
после 4-й неудачи и восстановление зависших `sending` реализованы (см. выше).»

---

### Cell: `src/remindme/services` — MODIFIED

#### CODEMANIFEST diff

- **Header — ADD** в `Imports` из `db` типы `set_user_timezone`, `CancelOutcome`.
- **Header — ADD** `Usages` ключ `timezone` (inline).
- **Global `Annotations` — ADD** строку про `timezone`.
- **Body — CHANGE** `parse_remind_time` (DST-шаг алгоритма).
- **Body — CHANGE** `ParseError` property `kind` (+ 2 варианта).
- **Body — ADD** `set_timezone_scenario`.
- **Body — CHANGE** `cancel_reminder_scenario` (тип возврата `bool` → `CancelOutcome`).
- **Body — CHANGE** Footer `Description`.
- `ParsedReminder` — без изменений.

**Header — Imports (CHANGE):**

```yaml
Imports:
  - Types:
      - Reminder
      - create_reminder
      - cancel_reminder
      - set_user_timezone
      - CancelOutcome
    Usages:
      - repositories
    From: src/remindme/db
```

**Header — Usages (ADD `timezone`):**

```yaml
Usages:
  conventions: .goga/usages/conventions.md
  timezone: |
    Валидация и локализация времени через stdlib zoneinfo.
    IANA-зона: ZoneInfo(name); неизвестная — ZoneInfoNotFoundError/KeyError.
    DST-детекция: локализация naive datetime со сравнением fold=0/fold=1
    (расхождение utcoffset ⇒ несуществующее/неоднозначное локальное время).
```

**Global `Annotations` (ADD строку):**

```yaml
  Используй `timezone` для локализации и DST-валидации локального времени.
```

**CHANGE — `parse_remind_time`:** в Algorithm добавить шаг 4 (DST), constraint «DST до past/horizon», `Use timezone`.

```yaml
"parse_remind_time(raw: str, now: datetime, timezone: str, default_time: str) -> result: ParsedReminder | ParseError":
  location: parser.py
  annotations: |
    Чистый детерминированный парсер времени напоминания с DST-валидацией.

    `raw`: исходный текст команды/фразы
    `now`: фиксированный текущий момент (aware UTC)
    `timezone`: IANA-зона пользователя
    `default_time`: время по умолчанию «ЧЧ:ММ»
    `result`: успех (UTC-момент + очищенный текст) или типизированная ошибка

    Algorithm:
    1. Зафиксировать `now`; определить режим по `raw` (/remind split по первому '|'; фраза «напомни …»)
    2. Распознать форму времени по 5 шаблонам (re.IGNORECASE, частное→общее)
    3. Календарная проверка через strptime (31.02 → date_not_exist)
    4. Построить локальный naive datetime в `timezone`; локализовать через `timezone` со сравнением fold=0/fold=1:
       попадание в весенний провал → ParseError(kind='nonexistent_time');
       осеннее наложение → ParseError(kind='ambiguous_time'); иначе — aware UTC
    5. Проверки границ: пустой текст → empty_text; >500 → text_too_long; remind_at<=now → past;
       >365 дней → horizon_exceeded; не распознано → invalid_format
    6. Вернуть ParsedReminder(remind_at_utc, text) или ParseError(kind)

    Constraints:
    - DST-проверка выполняется до проверок past/horizon
    - горизонт > 365 дней → horizon_exceeded; remind_at <= now → past

    Use `timezone` (ZoneInfo, fold); `conventions` (kw_only, parametrize).
```

**CHANGE — `ParseError` property `kind`:**

```yaml
  properties:
    "kind -> str": |
      Вариант: invalid_format / date_not_exist / past / horizon_exceeded / empty_text / text_too_long
      / nonexistent_time / ambiguous_time.
```

**ADD — `set_timezone_scenario` (Routine):**

```yaml
"set_timezone_scenario(session: AsyncSession, telegram_user_id: int, timezone: str, now: datetime) -> updated: bool":
  location: reminders.py
  annotations: |
    Смена часового пояса: валидация IANA + запись зоны.

    `session`: открытая AsyncSession
    `telegram_user_id`: идентификатор Telegram-пользователя
    `timezone`: запрошенная IANA-зона
    `now`: текущий момент (aware UTC)
    `updated`: True, если зона изменена; False — неизвестная зона

    Algorithm:
    1. Попробовать ZoneInfo(`timezone`) (по `timezone`)
    2. При ZoneInfoNotFoundError/KeyError/ValueError — вернуть False без обращения к БД
    3. Иначе — set_user_timezone(`session`, `telegram_user_id`, `timezone`, `now`); вернуть результат

    Requirements:
    - валидация IANA через ZoneInfo до записи

    Constraints:
    - не двигает уже созданные напоминания

    Use `timezone` (ZoneInfo); `repositories` (set_user_timezone); `conventions`.
```

**CHANGE — `cancel_reminder_scenario`:**

```yaml
"cancel_reminder_scenario(session: AsyncSession, telegram_user_id: int, reminder_id: int) -> outcome: CancelOutcome":
  location: reminders.py
  annotations: |
    Сценарий отмены: делегирует репозиторию, возвращает различимый исход.

    `session`: открытая AsyncSession
    `telegram_user_id`: идентификатор Telegram-пользователя
    `reminder_id`: идентификатор записи
    `outcome`: CancelOutcome (kind cancelled/not_found/already_sending)

    Algorithm:
    1. Вернуть cancel_reminder(`session`, `reminder_id`, `telegram_user_id`)

    Use `repositories` (cancel_reminder).
```

**CHANGE — Footer `Description`:**

```yaml
Description: |
  Доменная логика напоминаний: детерминированный парсер времени (с DST-валидацией), сценарии
  создания/отмены и смены часового пояса.
```

#### `.usages/` files

**`src/remindme/services/.usages/parser.md` — UPDATE:** добавить блок:

````md
## Перевод часов (DST) — этап 3

При разборе абсолютного/«сегодня-завтра» времени парсер локализует naive datetime в зоне пользователя. В дни перевода
часов локальное время может не существовать (весенний переход «вперёд») или быть неоднозначным (осенний «назад»):

```python
result = parse_remind_time(raw="25.03 03:30", now=now, timezone="Europe/Moscow", default_time="09:00")
if isinstance(result, ParseError):
    # result.kind == "nonexistent_time"  — такого локального времени нет в этот день
    # result.kind == "ambiguous_time"    — время двусмысленно (две интерпретации UTC)
    ...
```

- `nonexistent_time` — весенний переход: локальный момент выпадает (например 02:00→03:00).
- `ambiguous_time` — осенний переход: локальный момент встречается дважды (например 03:00→02:00).
- Оба случая пользователь должен указать другое время; обработчик показывает соответствующий текст через
  `format_parse_error`.
````

**`src/remindme/services/.usages/reminders.md` — UPDATE:** добавить блок:

````md
## Смена часового пояса и исход отмены — этап 3

```python
from .services import set_timezone_scenario, cancel_reminder_scenario, CancelOutcome

# смена зоны: валидация IANA (ZoneInfo) + запись; True — зона изменена, False — неизвестный пояс
updated = await set_timezone_scenario(
    session=session, telegram_user_id=user_id, timezone="Europe/Moscow", now=now,
)

# отмена возвращает различимый исход
outcome = await cancel_reminder_scenario(
    session=session, telegram_user_id=user_id, reminder_id=reminder_id,
)
# outcome.kind ∈ {cancelled, not_found, already_sending}
```

- `set_timezone_scenario` проверяет зону через `ZoneInfo` до записи: неизвестная IANA → `False` (без обращения к БД);
  валидная → `set_user_timezone`. Уже созданные напоминания не сдвигаются — меняется только их отображение.
- `cancel_reminder_scenario` отличает отмену (`cancelled`), отсутствие/чужую запись (`not_found`) и запись, уже
  переведённую worker в `sending` (`already_sending`).
````

---

### Cell: `src/remindme/bot` — MODIFIED

#### CODEMANIFEST diff

- **Header — ADD** в `Imports` из `services` типы `set_timezone_scenario`, `cancel_reminder_scenario`.
- **Header — ADD** в `Imports` из `db` тип `CancelOutcome` (объявлен в `db`, импортируется напрямую — без реэкспорта через
  `services`).
- **Global `Annotations` — ADD** строки про errors-handler, `sqlalchemy` и `/cancel`.
- **Body — ADD** `cmd_timezone`, `handle_db_error`, `cmd_cancel`, `format_cancel_outcome`.
- **Body — CHANGE** `register_handlers` (+ регистрация `cmd_timezone`, `cmd_cancel`, errors-handler).
- **Body — CHANGE** `format_parse_error` (+ DST kind).
- **Body — CHANGE** Footer `Description`.

**Header — Imports из services (CHANGE):**

```yaml
  - Types:
      - create_reminder_scenario
      - ParseError
      - set_timezone_scenario
      - cancel_reminder_scenario
    Usages:
      - reminders
      - parser
    From: src/remindme/services
```

**Header — Imports из db (CHANGE — добавить `CancelOutcome`):**

```yaml
  - Types:
      - User
      - Reminder
      - list_reminders
      - CancelOutcome
    Usages:
      - models
      - repositories
    From: src/remindme/db
```

**Global `Annotations` (ADD строки):**

```yaml
  Используй `aiogram` для инициализации, фильтров, handlers, errors-handler и set_my_commands.
  Используйте `sqlalchemy` для типа SQLAlchemyError в централизованном errors-handler.
  Errors-handler регистрируется отдельно от message-handlers.
  /cancel отменяет напоминание текущего пользователя по id (изоляция по владельцу — в сценарии).
```

**ADD — `cmd_timezone` (Routine):**

```yaml
"cmd_timezone(message: Message)":
  location: handlers.py
  annotations: |
    Обработчик /timezone: смена часового пояса через сценарий.

    `message`: входящее сообщение (фильтр команды timezone и PrivateOnly)

    Algorithm:
    1. Открыть сессию; user = ensure_user(...)
    2. Извлечь аргумент зоны из сообщения; если пуст — ответить подсказкой формата IANA
    3. updated = set_timezone_scenario(сессия, user.telegram_user_id, аргумент, now)
    4. updated → «часовой пояс изменён на {аргумент}»; иначе → «неизвестный пояс, используйте IANA, например Europe/Moscow»

    Constraints:
    - только приватный чат
    - при неизвестной зоне запись не меняется

    Use `aiogram` (Command, message.answer); `reminders` (set_timezone_scenario); ensure_user.
```

**ADD — `handle_db_error` (Routine):**

```yaml
"handle_db_error(event: ErrorEvent)":
  location: handlers.py
  annotations: |
    Централизованный обработчик ошибок БД: ловит SQLAlchemyError и показывает единый текст.
    Откат транзакции НЕ выполняется здесь — его делает репозиторий (`create_reminder` и др.) до повторного raise;
    сессия, открытая внутри обработчика, к моменту errors-handler уже закрыта контекстным менеджером.

    `event`: событие ошибки aiogram (содержит исключение и update)

    Algorithm:
    1. Достать исключение из `event`
    2. Если это не SQLAlchemyError — вернуть (пропустить, пусть обрабатывается дальше)
    3. Лог ERROR: user_id (из update), код ошибки; без текста записи и токена
    4. Ответить пользователю «Не удалось выполнить действие. Попробуйте ещё раз немного позже.»

    Constraints:
    - реагирует только на SQLAlchemyError
    - НЕ делает rollback (откат — ответственность репозитория до re-raise)
    - без токена и текста записи в логах/текстах

    Use `sqlalchemy` (SQLAlchemyError); `aiogram` (errors-handler, message.answer).
```

**ADD — `cmd_cancel` (Routine):**

```yaml
"cmd_cancel(message: Message)":
  location: handlers.py
  annotations: |
    Обработчик /cancel: отменяет напоминание текущего пользователя по id.

    `message`: входящее сообщение (фильтр команды cancel и PrivateOnly)

    Algorithm:
    1. Открыть сессию; user = ensure_user(...)
    2. Извлечь id напоминания из аргумента команды; если аргумент пуст/нечисловой — ответить подсказкой формата
    3. outcome = cancel_reminder_scenario(сессия, user.telegram_user_id, id)
    4. message.answer(format_cancel_outcome(outcome))

    Constraints:
    - только приватный чат; отмена только своих записей (изоляция по владельцу — в сценарии)

    Use `aiogram` (Command, message.answer); `reminders` (cancel_reminder_scenario); ensure_user.
```

**ADD — `format_cancel_outcome` (Routine):**

```yaml
"format_cancel_outcome(outcome: CancelOutcome) -> text: str":
  location: formatter.py
  annotations: |
    Текст результата отмены по варианту исхода.

    `outcome`: исход отмены (CancelOutcome)
    `text`: пользовательский текст

    Algorithm:
    1. По `outcome`.kind выбрать текст: cancelled → «напоминание отменено»;
       not_found → «напоминание не найдено»;
       already_sending → «Напоминание уже отправляется и не может быть отменено.»
    2. Вернуть `text`

    Use `parser`/`reminders` (CancelOutcome kinds — импортирован из services).
```

**CHANGE — `register_handlers`:** добавить шаги регистрации `cmd_timezone`, `cmd_cancel` и errors-handler.

```yaml
    Algorithm:
    1. Шаги этапа 2 (cmd_start, cmd_help, cmd_remind, cmd_reminders, handle_unknown_command,
       handle_remind_phrase, handle_text, handle_group)
    2. Зарегистрировать cmd_timezone с PrivateOnly и фильтром команды timezone
    3. Зарегистрировать cmd_cancel с PrivateOnly и фильтром команды cancel
    4. Зарегистрировать handle_db_error как errors-handler с фильтром SQLAlchemyError
```

**CHANGE — `format_parse_error`:** расширить mapping.

```yaml
    Algorithm:
    1. По `error`.kind выбрать шаблон: существующие (invalid_format, past, horizon_exceeded,
       empty_text, date_not_exist, text_too_long) + nonexistent_time → «такого времени не существует
       в этот день (перевод часов), выберите другое»; ambiguous_time → «это время неоднозначно
       (перевод часов), выберите другое»
    2. Вернуть `text`
```

**CHANGE — Footer `Description`:**

```yaml
Description: |
  Обработчики и форматтер Telegram-бота RemindMe: команды (incl. /timezone, /cancel), фраза «напомни …»,
  централизованный errors-handler БД, форматирование ответов и уведомлений, фильтр приватных чатов.
```

#### `.usages/` files

**`src/remindme/bot/.usages/handlers.md` — UPDATE:** добавить блок:

````md
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
````

**`src/remindme/bot/.usages/formatter.md` — UPDATE:** добавить блок:

````md
## Тексты ошибок перевода часов (DST) — этап 3

`format_parse_error` покрывает новые варианты `ParseError`:

- `nonexistent_time` → «Такого времени не существует в этот день (перевод часов). Выберите другое время.»
- `ambiguous_time` → «Это время неоднозначно из-за перевода часов. Выберите другое время.»

Остальные `kind` сохраняют тексты этапа 2 (формат/пример, прошлое, горизонт, пустой текст, длина, несуществующая дата).
````

---

### Cell: `src/remindme/worker` — MODIFIED

#### CODEMANIFEST diff

- **Header — ADD** в `Imports` из `db` типы `record_send_failure`, `mark_failed`, `recover_stuck_sending`.
- **Global `Annotations` — CHANGE:** удалить оговорку «без повторов/`failed`/восстановления зависших `sending` (этап 3)»;
  описать recovery при старте, повторы 30/120/600, эскалацию до `failed` и блокировку.
- **Body — CHANGE** `run_reminder_worker` (Algorithm: startup-recovery + retry/failed/blocked).
- **Footer — CHANGE** `Description`.

**Header — Imports из db (CHANGE):**

```yaml
  - Types:
      - Reminder
      - find_due_reminders
      - claim_for_sending
      - mark_sent
      - record_send_failure
      - mark_failed
      - recover_stuck_sending
    Usages:
      - repositories
    From: src/remindme/db
```

**Global `Annotations` (CHANGE):**

```yaml
  Цикл доставки уведомлений: один фоновый процесс в одном event loop с polling. Каждая итерация открывает свою сессию.
  При старте один раз выполняется recovery зависших sending (locked_at_utc старше 60 с → scheduled). Атомарный захват
  записи гарантирует, что две итерации не заберут одно напоминание. Неудачи send_message ретраятся по расписанию
  30/120/600 с и эскалируются до failed после 4-й; блокировка бота пользователем (TelegramForbiddenError) переводит
  запись в failed немедленно. Токен не логируется.
```

**CHANGE — `run_reminder_worker`:**

```yaml
"run_reminder_worker(bot: Bot, session_factory: AsyncSessionFactory, poll_interval: int)":
  location: notifications.py
  annotations: |
    Бесконечный цикл доставки просроченных напоминаний с восстановлением при старте, повторами и failed.

    `bot`: экземпляр Bot для отправки
    `session_factory`: фабрика async-сессий
    `poll_interval`: интервал опроса, секунд

    Algorithm:
    1. Startup-recovery (один раз до цикла): открыть сессию через `session_factory`;
       recover_stuck_sending(сессия, now, 60); закрыть сессию
    2. Бесконечный цикл:
       1. asyncio.sleep(`poll_interval`)
       2. now = aware UTC текущего момента
       3. Открыть сессию через `session_factory`
       4. due = find_due_reminders(сессия, now)
       5. Для каждого reminder в due:
          1. Если claim_for_sending(сессия, reminder.id, now) — True:
             1. text = format_notification(reminder)
             2. try:
                1. `bot`.send_message(chat_id=reminder.user_id, text=text)
                2. mark_sent(сессия, reminder.id, now)
             3. except TelegramForbiddenError: mark_failed(сессия, reminder.id);
                лог WARNING (user_id, «bot blocked»)
             4. except (иная ошибка send_message): failed=record_send_failure(сессия, reminder.id, now);
                лог WARNING (user_id, attempt_count, код ошибки, длительность)
          2. Закрыть сессию

    Requirements:
    - одна итерация цикла = одна сессия; recovery ровно один раз при старте
    - блокировка бота → немедленный failed (без повторов); 4-я неудача → failed (через record_send_failure)
    - просроченные активные напоминания доставляются независимо от длительности простоя

    Constraints:
    - токен и текст записи не логируются
    - ошибка одного reminder не прерывает цикл
    - отменённые записи не отправляются (claim_for_sending фильтрует status='scheduled')

    Use `aiogram` (send_message, TelegramForbiddenError); `repositories` (find_due_reminders,
    claim_for_sending, mark_sent, record_send_failure, mark_failed, recover_stuck_sending);
    `formatter` (format_notification); `conventions` (structured logging).
```

**CHANGE — Footer `Description`:**

```yaml
Description: |
  Цикл доставки уведомлений: восстановление зависших sending при старте, опрос просроченных напоминаний,
  атомарный захват, повторы 30/120/600 с эскалацией до failed и немедленный failed при блокировке бота.
```

#### `.usages/` files

**`src/remindme/worker/.usages/running.md` — UPDATE:** заменить блок «без повторов/failed/восстановления» на:

````md
## Восстановление при старте, повторы и failed — этап 3

Worker при старте один раз возвращает зависшие записи `sending` (`locked_at_utc` старше 60 с) в `scheduled`, а
просроченные активные напоминания доставляются обычным циклом независимо от длительности простоя.

```python
asyncio.create_task(
    run_reminder_worker(bot=bot, session_factory=session_factory, poll_interval=settings.REMINDER_POLL_INTERVAL_SECONDS)
)
```

Внутри `run_reminder_worker`:
- на старте — один вызов `recover_stuck_sending(session, now, stale_after_seconds=60)`;
- на каждую итерацию — `find_due_reminders` → `claim_for_sending` → `send_message`;
- `TelegramForbiddenError` (бот заблокирован пользователем) → `mark_failed` немедленно, без повторов;
- иная ошибка `send_message` → `record_send_failure`: 1–3-я неудача назначают следующий `attempt` через 30/120/600 с,
  4-я → `failed`.

## Предусловия и побочные эффекты (обновлено)

- Recovery выполняется ровно один раз при старте, до основного цикла; `next_attempt_at_utc` не пересчитывается —
  запись остаётся due и доставляется сразу.
- Блокировка бота — постоянная ошибка: запись сразу в `failed` (не проходит расписание повторов).
- Преходящие ошибки `send_message` ретраятся at-least-once: дубли при аварии между отправкой и фиксацией статуса
  допустимы (out of scope — exactly-once не требуется).
- Логи: WARNING с `user_id`, `attempt_count`, кодом ошибки и длительностью; токен и полный текст записи никогда не
  попадают в логи и тексты; `chat_id` = `reminder.user_id`.
````

---

### Cell: `src/remindme/main` — MODIFIED

#### CODEMANIFEST diff

- **Header** — без изменений.
- **Global `Annotations` — CHANGE:** фраза про меню («на этапе 3 добавлена `/timezone`»); фраза про recovery внутри worker.
- **Body — CHANGE** `set_commands` (+ `/timezone`).
- **Body** `main()` — без изменений.
- **Footer — CHANGE** `Description`.

**Global `Annotations` (CHANGE строки):**

```yaml
  Меню команд формируется инкрементально — на этапе 3 добавлены /timezone и /cancel.
  Цикл доставки worker запускается как фоновая задача; recovery зависших sending выполняется внутри worker.
```

**CHANGE — `set_commands`:**

```yaml
"set_commands(bot: Bot)":
  location: main.py
  annotations: |
    Регистрирует меню команд, видимое пользователю. Инкрементально: на этапе 3 — /start, /help, /remind, /reminders,
    /timezone, /cancel.

    `bot`: экземпляр Bot

    Algorithm:
    1. Вызвать у `bot` set_my_commands со списком BotCommand для /start, /help, /remind, /reminders, /timezone, /cancel

    Requirements:
    - Регистрируются только команды, реализованные на текущем этапе

    Use `aiogram` (set_my_commands, BotCommand).
```

**CHANGE — Footer `Description`:**

```yaml
Description: |
  Точка входа RemindMe: сборка инфраструктуры данных, инициализация бота, регистрация обработчиков,
  запуск worker уведомлений (с восстановлением зависших при старте) и long polling.
```

#### `.usages/` files

Без изменений (домен «запуск приложения» покрыт `src/remindme/worker/.usages/running.md`, обновлённым на этапе 3).

---

## Dependency Map

Граф межклеточных Imports с учётом изменений (новые/усиленные рёбра помечены `+`):

```
        config
          ▲ (Settings, get_settings)
          │
         db ◄──────────────────────────────────────────────────┐
   (+ CancelOutcome, set_user_timezone,                          │
    + record_send_failure, mark_failed, recover_stuck_sending,   │
    ~ cancel_reminder → CancelOutcome)                           │
          ▲                                                       │
          │ (+set_user_timezone, +CancelOutcome)                  │
       services                                                   │
   (+ set_timezone_scenario, ~ cancel_reminder_scenario→CancelOutcome,
    ~ parse_remind_time +DST, ~ ParseError +kind)
          ▲                                                       │
          │ (+set_timezone_scenario, +cancel_reminder_scenario)    │
          │ (+CancelOutcome из db, см. ниже)                       │
         bot ◄──(format_notification, formatter)── worker         │
   (+ cmd_timezone, + handle_db_error,                              │ (+record_send_failure,
    + cmd_cancel, + format_cancel_outcome,                          + mark_failed,
    ~ register_handlers, ~ format_parse_error)                      + recover_stuck_sending;
          ▲                                                          ~ run_reminder_worker)
          │                                                          ▲
         main ─(register_handlers)──► bot                            │
         main ─(run_reminder_worker)────────────────────────────────►┘
         main ─(set_commands ~ +/timezone, +/cancel)
```

**Ацикличен.** Порядок зависимостей: `config → db → services → {bot, worker} → main`. `CancelOutcome` перенесён в `db`
(producer `cancel_reminder`), чтобы не создавать цикл `db ↔ services`. `services` и `bot` импортируют `CancelOutcome`
**напрямую из `db`** (объявлен там); `services` **не** реэкспортирует его — `bot` не получает его через `services`.

---

## Verification Checklist

После применения артефактов проверить:

**Синтаксис DSL (все CODEMANIFEST):**
- [ ] `goga lint` проходит без ошибок по 5 изменённым CODEMANIFEST.
- [ ] YAML case-sensitive корректен; сигнатуры Routine — вход + семантическая метка возврата.
- [ ] `location` каждого нового/изменённого типа на уровне CODEMANIFEST с расширением
      (`repositories.py`/`parser.py`/`reminders.py`/`handlers.py`/`formatter.py`/`notifications.py`/`main.py`).
- [ ] Все backtick-ссылки в annotations разрешимы в контексте файла (`conventions`, `sqlalchemy`, `aiogram`,
      `timezone`, `repositories`, `formatter`, `parser`, `reminders`, сигнатурные переменные, импортированные типы).

**Клетки и Imports:**
- [ ] Граф зависимостей ацикличен: `config → db → services → {bot, worker} → main`.
- [ ] `CancelOutcome` объявлен только в `db`; `services` и `bot` импортируют его **из `db`** (не дублируют объявление;
      `services` не реэкспортирует его в `bot`).
- [ ] Новые Imports добавлены: db→(config unchanged); services←db(+`set_user_timezone`, +`CancelOutcome`);
      bot←services(+`set_timezone_scenario`, +`cancel_reminder_scenario`); bot←db(+`CancelOutcome`);
      worker←db(+`record_send_failure`, +`mark_failed`, +`recover_stuck_sending`); main unchanged.

**Поведение (соответствие Acceptance Criteria):**
- [ ] `record_send_failure`: 1-я неудача → `attempt_count=1`, `next=now+30`; 4-я → `failed` (через `mark_failed`).
- [ ] `recover_stuck_sending`: `sending` с `locked>60с` → `scheduled`; `next_attempt_at_utc` не трогается (сразу due).
- [ ] `run_reminder_worker`: recovery один раз при старте; `TelegramForbiddenError` → `mark_failed` немедленно.
- [ ] `set_timezone_scenario`/`set_user_timezone`: `Europe/Moscow` ок, `Mars/Phobos` отклонён; напоминания не двигаются.
- [ ] `parse_remind_time`: `nonexistent_time`/`ambiguous_time` возвращаются до `past`/`horizon`.
- [ ] `handle_db_error`: только `SQLAlchemyError`; единый текст; лог без текста записи/токена; rollback НЕ выполняется
      (откат делает репозиторий до re-raise, сессия обработчика уже закрыта).
- [ ] `cancel_reminder` → `CancelOutcome(kind='already_sending')` для записи в `sending`; `cmd_cancel` +
      `format_cancel_outcome` показывают «Напоминание уже отправляется и не может быть отменено.»
- [ ] Ни в одном логе/тексте нет токена и полного текста записи (тест с перехватом логов).

**Фасады Python:**
- [ ] `__all__` каждой клетки экспортирует новые публичные идентификаторы (`record_send_failure`, `mark_failed`,
      `recover_stuck_sending`, `set_user_timezone`, `CancelOutcome`, `set_timezone_scenario`, `cmd_cancel`,
      `format_cancel_outcome`; типы возвращаемых значений согласованы caller↔callee).
- [ ] Docstrings Google-style и type hints для всех новых публичных функций (`conventions`).

**Usages:**
- [ ] 6 обновлённых `.usages/` файлов самодостаточны, без cross-ссылок и дублирования annotations.
- [ ] Каждая подключённая практика (`timezone`) ссылается хотя бы в одной annotation.
