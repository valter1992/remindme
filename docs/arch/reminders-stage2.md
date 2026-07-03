# Архитектурный план — RemindMe, Этап 2: Главный сценарий (напоминания)

> Источник: `docs/tasks/remindme-02-reminders.md`. План описывает только артефакты CODEMANIFEST и `.usages/` — без кода реализации. Легенда: ✚ modified, ✦ new.

## Topic
`reminders-stage2` — `docs/arch/reminders-stage2.md`

## Implementation Order

Порядок leaves → root (каждая следующая cell импортирует только из уже спроектированных):

| # | Cell | Статус | Причина порядка |
|---|---|---|---|
| 0 | `src/remindme/config` | без изменений | leaf; предоставляет `Settings`/`get_settings` (все нужные настройки уже готовы) |
| 1 | `src/remindme/db` | ✚ modified | зависит только от `config`; новые репозиторий-функции не добавляют новых cross-cell импортов |
| 2 | `src/remindme/services` | ✦ new | зависит от `db` (`Reminder`, `create_reminder`, `cancel_reminder`) |
| 3 | `src/remindme/bot` | ✚ modified | зависит от `config`, `db`, `services` |
| 4 | `src/remindme/worker` | ✦ new | зависит от `db`, `bot` (`format_notification`) |
| 5 | `src/remindme/main` | ✚ modified | root; зависит от `config`, `db`, `bot`, `worker` |

---

## Artifacts

### Cell: `src/remindme/db` — ✚ MODIFIED

#### CODEMANIFEST (`src/remindme/db/CODEMANIFEST`) — diff

- **Header** — без изменений (`Imports`: `Settings` ← config; `Usages`: `conventions`, `sqlalchemy`, `aiosqlite`; глобальная `Annotations` без изменений).
- **Body** — ДОБАВИТЬ 6 routines (в существующий Body, после существующих типов) с `location: repositories.py`:

```yaml
"create_reminder(session: AsyncSession, telegram_user_id: int, text: str, remind_at_utc: datetime, now: datetime) -> reminder: Reminder":
  location: repositories.py
  annotations: |
    Создаёт запись Reminder со статусом scheduled для пользователя.

    `session`: открытая AsyncSession
    `telegram_user_id`: идентификатор Telegram-пользователя (владелец)
    `text`: текст напоминания
    `remind_at_utc`: момент напоминания, aware UTC datetime
    `now`: текущий момент (aware UTC) для created_at_utc
    `reminder`: созданная и зафиксированная запись

    Algorithm:
    1. Привести `remind_at_utc` и `now` к ISO 8601 UTC строкам
    2. Создать Reminder: user_id=`telegram_user_id`, text=`text`, remind_at_utc, status="scheduled", attempt_count=0, next_attempt_at_utc=remind_at_utc, created_at_utc=now
    3. session.add + commit
    4. При ошибке SQLAlchemy — rollback, залогировать ошибку (structured logging) и повторный raise
    5. Вернуть `reminder`

    Requirements:
    - timestamp-поля сохраняются как ISO 8601 UTC строки
    - начальные значения: status="scheduled", attempt_count=0, next_attempt_at_utc=remind_at_utc

    Constraints:
    - Откат транзакции при ошибке; пользовательский текст ошибки — централизованный handler этапа 3

    Use `sqlalchemy` (add/commit/rollback); `conventions` (ISO 8601, relative imports, structured logging).

"list_reminders(session: AsyncSession, user_id: int) -> reminders: list[Reminder]":
  location: repositories.py
  annotations: |
    Только активные (scheduled) напоминания текущего пользователя, отсортированные по времени.

    `session`: открытая AsyncSession
    `user_id`: идентификатор Telegram-пользователя
    `reminders`: список Reminder (не более 20)

    Algorithm:
    1. select Reminder where user_id==`user_id` and status=="scheduled", order_by remind_at_utc asc, limit 20
    2. Вернуть list(result.scalars())

    Requirements:
    - фильтр по user_id и status="scheduled"; сортировка remind_at_utc asc; лимит 20

    Constraints:
    - запрос только по id без user_id запрещён (изоляция по владельцу)

    Use `sqlalchemy` (select, scalars).

"cancel_reminder(session: AsyncSession, reminder_id: int, user_id: int) -> cancelled: bool":
  location: repositories.py
  annotations: |
    Переводит scheduled→cancelled с проверкой владельца.

    `session`: открытая AsyncSession
    `reminder_id`: идентификатор записи
    `user_id`: идентификатор Telegram-пользователя (владелец)
    `cancelled`: True, если ровно одна запись переведена в cancelled

    Algorithm:
    1. update Reminder set status="cancelled" where id==`reminder_id` and user_id==`user_id` and status=="scheduled"
    2. commit
    3. Вернуть result.rowcount == 1

    Requirements:
    - фильтр по (id, user_id, status="scheduled"); rowcount отличает «отменено» от «не найдено/не владельцу»

    Constraints:
    - не раскрывает существование чужих записей (возврат bool)

    Use `sqlalchemy` (update, rowcount).

"find_due_reminders(session: AsyncSession, now: datetime) -> reminders: list[Reminder]":
  location: repositories.py
  annotations: |
    Выбирает просроченные scheduled-напоминания для worker (без перевода статуса).

    `session`: открытая AsyncSession
    `now`: текущий момент (aware UTC)
    `reminders`: список Reminder с next_attempt_at_utc <= now

    Algorithm:
    1. Привести `now` к ISO 8601 UTC строке now_iso
    2. select Reminder where status=="scheduled" and next_attempt_at_utc <= now_iso
    3. Вернуть list(result.scalars())

    Requirements:
    - сравнение next_attempt_at_utc (ISO 8601 строка) <= now_iso лексикографически корректно при фиксированном формате ISO 8601

    Constraints:
    - не переводит статус; захват записи делает `claim_for_sending`

    Use `sqlalchemy` (select).

"claim_for_sending(session: AsyncSession, reminder_id: int, now: datetime) -> claimed: bool":
  location: repositories.py
  annotations: |
    Атомарный захват записи: scheduled→sending.

    `session`: открытая AsyncSession
    `reminder_id`: идентификатор записи
    `now`: текущий момент (aware UTC) для locked_at_utc
    `claimed`: True, если запись захвачена этой итерацией

    Algorithm:
    1. Привести `now` к ISO 8601 UTC строке now_iso
    2. update Reminder set status="sending", locked_at_utc=now_iso where id==`reminder_id` and status=="scheduled"
    3. commit
    4. Вернуть result.rowcount == 1

    Requirements:
    - проверка status=="scheduled" в WHERE + rowcount==1 гарантирует захват ровно одной итерацией worker без отдельной блокировки

    Constraints:
    - фильтр по status обязателен — иначе две итерации цикла заберут одну запись

    Use `sqlalchemy` (update, rowcount).

"mark_sent(session: AsyncSession, reminder_id: int, now: datetime)":
  location: repositories.py
  annotations: |
    Переводит запись в sent и проставляет время отправки (базовый путь доставки этапа 2).

    `session`: открытая AsyncSession
    `reminder_id`: идентификатор записи
    `now`: текущий момент (aware UTC) для sent_at_utc

    Algorithm:
    1. Привести `now` к ISO 8601 UTC строке now_iso
    2. update Reminder set status="sent", sent_at_utc=now_iso where id==`reminder_id`
    3. commit

    Requirements:
    - проставляет status="sent" и sent_at_utc

    Constraints:
    - без повторных попыток, failed и восстановления зависших (этап 3)

    Use `sqlalchemy` (update).
```

- **Footer** — `Description` расширить:
```yaml
Description: |
  Слой данных RemindMe: декларативные модели и async-подключение к SQLite; репозиторий напоминаний (CRUD и статусные переходы с изоляцией по владельцу).
```

#### `.usages/` файлы

**Файл:** `src/remindme/db/.usages/repositories.md` — ✦ NEW

```md
# Репозиторий напоминаний — операции по владельцу и статусу

Домен: CRUD и статусные переходы напоминаний через `AsyncSession` с изоляцией по владельцу.
Аудитория: клетка `services` (`create_reminder`, `cancel_reminder`), клетка `bot` (`list_reminders`), клетка `worker` (`find_due_reminders`, `claim_for_sending`, `mark_sent`).

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

cancelled = await cancel_reminder(session=session, reminder_id=reminder_id, user_id=user_id)
# True — ровно одна запись переведена scheduled→cancelled; False — не найдено/не владельцу/не scheduled
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

## Предусловия и побочные эффекты

- `session` открыта; каждая функция фиксирует транзакцию (`commit`), при ошибке откатывает и залогирует её (structured logging) перед повторным raise.
- Сравнение `next_attempt_at_utc <= now_iso` выполняется лексикографически по строкам ISO 8601 — корректно при фиксированном формате.
- `find_due_reminders` не переводит статус; захват делает `claim_for_sending`, отметку — `mark_sent`.
- Базовый путь этапа 2: без повторных попыток, статуса `failed` и восстановления зависших `sending` (этап 3).
```

`engine.md`, `models.md` — без изменений.

---

### Cell: `src/remindme/services` — ✦ NEW

#### CODEMANIFEST (`src/remindme/services/CODEMANIFEST`) — полный

```yaml
Imports:
  - Types:
      - Reminder
      - create_reminder
      - cancel_reminder
    Usages:
      - repositories
    From: src/remindme/db

Usages:
  conventions: .goga/usages/conventions.md

Annotations: |
  Используй `conventions` для написания правил и тестирования кода.
  Используй `repositories` из Imports для вызова create_reminder/cancel_reminder (изоляция по владельцу, ISO 8601, rowcount).

  Доменная логика напоминаний. Парсер `parse_remind_time` — чистая детерминированная функция без зависимостей от Telegram/БД; `now` фиксируется один раз и передаётся аргументом (datetime.now() внутри не вызывается). Сценарии оркестрируют парсер → репозиторий. DTO результата/ошибки — pydantic с kw_only=True. Импорты внутри пакета — относительные.

---

"parse_remind_time(raw: str, now: datetime, timezone: str, default_time: str) -> result: ParsedReminder | ParseError":
  location: parser.py
  annotations: |
    Чистый детерминированный парсер времени напоминания без зависимостей от Telegram/БД.

    `raw`: исходный текст (команда «/remind ...» или фраза «напомни ...»)
    `now`: фиксированный текущий момент (aware UTC)
    `timezone`: IANA-зона пользователя
    `default_time`: время по умолчанию «ЧЧ:ММ» (когда дата без времени)
    `result`: успех (UTC-момент + очищенный текст) или типизированная ошибка

    Algorithm:
    1. Зафиксировать `now` один раз
    2. Определить режим по `raw`: команда /remind — split по первому '|', левая часть = время, правая = текст; фраза «напомни [мне] …» — извлечь текст после префикса
    3. Распознать форму времени по 5 шаблонам (re.IGNORECASE, приоритет от частного к общему): абсолютная (YYYY-MM-DD HH:MM, DD.MM HH:MM), относительная («через N минут/часов/дней»), «сегодня/завтра [в HH:MM]»
    4. Календарная проверка через strptime (ловит несуществующие даты вида 31.02)
    5. Построить локальный naive datetime в `timezone` → конвертировать в aware UTC
    6. Проверки границ: пустой текст → empty_text; len(text) > 500 → text_too_long; remind_at <= now → past; remind_at - now > 365 дней → horizon_exceeded; не распознано → invalid_format; ошибка strptime → date_not_exist
    7. Вернуть ParsedReminder(remind_at_utc, text) или ParseError(kind)

    Requirements:
    - чистая функция: только аргумент `now`, без datetime.now()/Telegram/БД
    - 5 шаблонов фразы с re.IGNORECASE, приоритет частное→общее
    - режим /remind: split строго по первому '|'
    - результат — aware UTC datetime + очищенный текст
    - длина очищенного текста ≤ 500; превышение → ParseError(text_too_long)

    Constraints:
    - горизонт > 365 дней → horizon_exceeded
    - remind_at <= now → past (граница «ровно now» трактуется как прошлое — отказ)
    - `default_time` применяется только когда дата без времени (сегодня/завтра без HH:MM)
    - неподдерживаемая свободная фраза (например «в пятницу вечером») → invalid_format с допустимыми шаблонами

    Use `conventions` (kw_only для DTO, parametrize для границ парсера).

"ParsedReminder(remind_at_utc: datetime, text: str)":
  location: parser.py
  annotations: |
    Результат успеха парсера.

    Requirements:
    - pydantic с kw_only=True

    Use `conventions` (kw_only).
  properties:
    "remind_at_utc -> datetime": |
      Aware UTC момент напоминания.
    "text -> str": |
      Очищенный текст напоминания без временной части.

"ParseError(kind: str)":
  location: parser.py
  annotations: |
    Типизированная ошибка парсера.

    Requirements:
    - pydantic с kw_only=True
    - `kind` ∈ {invalid_format, date_not_exist, past, horizon_exceeded, empty_text, text_too_long}

    Use `conventions` (kw_only).
  properties:
    "kind -> str": |
      Вариант ошибки: invalid_format / date_not_exist / past / horizon_exceeded / empty_text / text_too_long.

"create_reminder_scenario(session: AsyncSession, telegram_user_id: int, raw: str, now: datetime, timezone: str, default_time: str) -> outcome: Reminder | ParseError":
  location: reminders.py
  annotations: |
    Сценарий создания напоминания: парсер → проверка границ → сохранение.

    `session`: открытая AsyncSession
    `telegram_user_id`: идентификатор Telegram-пользователя
    `raw`: исходный текст команды/фразы
    `now`: фиксированный текущий момент (aware UTC)
    `timezone`: IANA-зона пользователя
    `default_time`: время по умолчанию «ЧЧ:ММ»
    `outcome`: созданный Reminder или ParseError

    Algorithm:
    1. parse = parse_remind_time(`raw`, `now`, `timezone`, `default_time`)
    2. Если parse — ParseError → вернуть его без сохранения
    3. Если ParsedReminder → create_reminder(`session`, `telegram_user_id`, parse.text, parse.remind_at_utc, `now`)
    4. Вернуть Reminder

    Constraints:
    - при ошибке парсинга запись не создаётся
    - SQLAlchemyError из `create_reminder` откатывается в репозитории; пользовательский текст — централизованный handler этапа 3

    Use `repositories` (create_reminder); `conventions`.

"cancel_reminder_scenario(session: AsyncSession, telegram_user_id: int, reminder_id: int) -> cancelled: bool":
  location: reminders.py
  annotations: |
    Сценарий отмены: делегирует репозиторию с проверкой владельца.

    `session`: открытая AsyncSession
    `telegram_user_id`: идентификатор Telegram-пользователя (владелец)
    `reminder_id`: идентификатор записи
    `cancelled`: True, если ровно одна запись переведена в cancelled

    Algorithm:
    1. Вернуть результат cancel_reminder(`session`, `reminder_id`, `telegram_user_id`)

    Use `repositories` (cancel_reminder).

---

Author: Goga
CreatedAt: 29/06/26
Description: |
  Доменная логика напоминаний: детерминированный парсер времени и сценарии создания/отмены.
```

#### `.usages/` файлы

**Файл:** `src/remindme/services/.usages/parser.md` — ✦ NEW

```md
# Парсер времени напоминания — разбор текста в UTC-момент

Домен: детерминированный разбор времени напоминания из текста команды/фразы в aware UTC-момент.
Аудитория: клетка `bot` (обработчики) и тесты `tests/unit/test_parser.py`.

Парсер — чистая функция: не обращается к Telegram и БД, не вызывает `datetime.now()` — текущий момент передаётся аргументом `now`. Возвращает `ParsedReminder` (успех) или `ParseError` (типизированная ошибка).

## Режимы разбора

Режим определяется по содержимому `raw`:
- команда `/remind` — текст разделяется по первому `|`: слева время, справа текст напоминания;
- фраза «напомни [мне] …» — текст после префикса разбирается по 5 шаблонам (`re.IGNORECASE`, приоритет от частного к общему).

## Поддерживаемые формы времени

- абсолютная: `YYYY-MM-DD HH:MM`, `DD.MM HH:MM`;
- относительная: «через N минут/минуты/минуту/час/часа/дней/дня …» (все формы единиц);
- «сегодня [в HH:MM]», «завтра [в HH:MM]» — без времени используется `default_time`.

## Вызов

```python
from .services import parse_remind_time, ParsedReminder, ParseError

result = parse_remind_time(
    raw=message.text,
    now=fixed_now,          # aware UTC datetime
    timezone=user.timezone,
    default_time="09:00",
)
if isinstance(result, ParseError):
    # result.kind ∈ {invalid_format, date_not_exist, past, horizon_exceeded, empty_text, text_too_long}
    ...
else:  # ParsedReminder
    result.remind_at_utc    # aware UTC datetime
    result.text             # очищенный текст без временной части
```

## Предусловия и побочные эффекты

- `now` — aware UTC datetime, фиксируется один раз; реальные `sleep` в тестах не используются.
- Календарная валидация через `strptime`: `31.02` → `date_not_exist`.
- Горизонт > 365 дней → `horizon_exceeded`; момент в прошлом или ровно `now` → `past`.
- Пустой текст напоминания → `empty_text`.
- Длина текста напоминания > 500 символов → `text_too_long`.
- Неподдерживаемая свободная фраза («в пятницу вечером») → `invalid_format`.
```

**Файл:** `src/remindme/services/.usages/reminders.md` — ✦ NEW

```md
# Сценарии напоминаний — создание и отмена

Домен: бизнес-сценарии напоминаний поверх парсера и репозитория.
Аудитория: клетка `bot` (`cmd_remind`, `handle_remind_phrase`) и тесты (`cancel_reminder_scenario`); UI отмены (кнопка «Удалить») — этап 4.

Сценарии оркестрируют парсер → репозиторий. Принимают открытую `AsyncSession` и `now` (aware UTC). Не знают о Telegram.

## Создание напоминания

Возвращает созданный `Reminder` либо `ParseError` (без сохранения записи при ошибке парсинга).

```python
from .services import create_reminder_scenario, ParseError

outcome = await create_reminder_scenario(
    session=session,
    telegram_user_id=user_id,
    raw=message.text,
    now=fixed_now,
    timezone=user.timezone,
    default_time="09:00",
)
if isinstance(outcome, ParseError):
    # показать формат и пример; запись не создана
    ...
else:
    reminder = outcome  # Reminder ORM (status="scheduled")
```

## Отмена напоминания

Делегирует репозиторию с проверкой владельца; возвращает признак успеха.

```python
from .services import cancel_reminder_scenario

cancelled = await cancel_reminder_scenario(
    session=session,
    telegram_user_id=user_id,
    reminder_id=reminder_id,
)
# True — ровно одна запись scheduled→cancelled; False — не найдено/не владельцу/не scheduled
```

## Предусловия и побочные эффекты

- `session` открыта; сценарий создания фиксирует транзакцию через репозиторий, откат при `SQLAlchemyError` — в репозитории.
- При `ParseError` запись не создаётся.
- `now` передаётся явно (aware UTC); `datetime.now()` внутри не вызывается.
```

---

### Cell: `src/remindme/bot` — ✚ MODIFIED

#### CODEMANIFEST (`src/remindme/bot/CODEMANIFEST`) — diff

- **Header `Imports`** — ИЗМЕНИТЬ:

```yaml
Imports:
  - Types:
      - get_settings
    Usages:
      - settings
    From: src/remindme/config
  - Types:
      - User
      - Reminder
      - list_reminders
    Usages:
      - models
      - repositories
    From: src/remindme/db
  - Types:
      - create_reminder_scenario
      - ParseError
    Usages:
      - reminders
      - parser
    From: src/remindme/services
```
(к существующему импорту из `config` и `db` добавлены `Reminder`, `list_reminders`, `repositories`; добавлен импорт из `services`. `cancel_reminder_scenario` НЕ импортируется в bot на этапе 2 — UI отмены появляется на этапе 4; тип остаётся в `services` для тестов.)

- **Header `Usages`** — без изменений (`conventions`, `aiogram`, `sqlalchemy`).
- **Header `Annotations`** — заменить на:

```yaml
Annotations: |
  Используй `conventions` для написания правил и тестирования кода.
  Используй `aiogram` для инициализации, фильтров, handlers и set_my_commands.
  Используй `settings`, `models`, `repositories`, `reminders`, `parser` из Imports для контекста потребления.

  Бот работает только в приватных чатах (`PrivateOnly`); в группах отвечает, что режим не поддерживается, и не пишет в БД.
  Порядок регистрации handlers: командные → обработчик фразы «напомни …» → общий текстовый (`handle_text`) → `handle_group` (без фильтра) последним.
  Фабрика сессий передаётся точкой входа в `register_handlers` и замыкается в обработчиках; `now` — aware UTC на момент вызова; токен не попадает в логи и тексты.
  Форматирование ответов и уведомлений — в `formatter.py` с единой helper-функцией «aware UTC → локальная строка пользователя».
```

- **Body** — ДОБАВИТЬ (существующие типы `PrivateOnly`/`ensure_user`/`cmd_start`/`cmd_help`/`handle_unknown_command`/`handle_text`/`handle_group`/`register_handlers` — без изменений, КРОМЕ `register_handlers`: его `Algorithm` обновляется до 8-шагового порядка `cmd_start` → `cmd_help` → `cmd_remind` → `cmd_reminders` → `handle_unknown_command` → `handle_remind_phrase` → `handle_text` → `handle_group`; полный текст — в `.usages/handlers.md`):

```yaml
"cmd_remind(message: Message)":
  location: handlers.py
  annotations: |
    Обработчик /remind: создаёт напоминание через сценарий создания.

    `message`: входящее сообщение (фильтр команды remind и `PrivateOnly`)

    Algorithm:
    1. Открыть сессию через session_factory
    2. user = ensure_user(сессия, message.from_user.id, `settings`.DEFAULT_TIMEZONE)
    3. Если длина `message`.text превышает 1000 — ответить об ограничении и выйти
    4. now = aware UTC текущего момента
    5. outcome = create_reminder_scenario(сессия, user.telegram_user_id, message.text, now, user.timezone, `settings`.DEFAULT_REMINDER_TIME)
    6. Если outcome — ParseError → message.answer(`format_parse_error`(outcome))
    7. Иначе → message.answer(`format_reminder_confirmation`(outcome, user.timezone))

    Constraints:
    - при ParseError запись не создаётся
    - лимит длины сообщения 1000; текст напоминания 500 проверяется в парсере

    Use `aiogram` (Command, message.answer); `settings` (DEFAULT_REMINDER_TIME, DEFAULT_TIMEZONE); `reminders` (create_reminder_scenario); `parser` (ParseError); ensure_user.

"cmd_reminders(message: Message)":
  location: handlers.py
  annotations: |
    Обработчик /reminders: показывает активные напоминания текущего пользователя.

    `message`: входящее сообщение (фильтр команды reminders и `PrivateOnly`)

    Algorithm:
    1. Открыть сессию через session_factory
    2. user = ensure_user(сессия, message.from_user.id, `settings`.DEFAULT_TIMEZONE)
    3. reminders = list_reminders(сессия, user.telegram_user_id)
    4. message.answer(`format_reminder_list`(reminders, user.timezone))

    Requirements:
    - список содержит только записи текущего пользователя

    Use `repositories` (list_reminders); `aiogram` (Command, message.answer); ensure_user.

"handle_remind_phrase(message: Message)":
  location: handlers.py
  annotations: |
    Обработчик фразы «напомни [мне] …»; регистрируется перед `handle_text`.

    `message`: свободное сообщение, начинающееся с «напомни» (фильтр `PrivateOnly`, re.IGNORECASE)

    Algorithm:
    1. Если `message`.text не начинается с «напомни» (re.IGNORECASE) — пропустить
    2. Открыть сессию; user = ensure_user(…)
    3. Если длина `message`.text превышает 1000 — ответить об ограничение
    4. now = aware UTC; outcome = create_reminder_scenario(сессия, user.telegram_user_id, message.text, now, user.timezone, `settings`.DEFAULT_REMINDER_TIME)
    5. ParseError → `format_parse_error`; иначе → `format_reminder_confirmation`

    Constraints:
    - регистрируется перед `handle_text`, чтобы фраза не уходила в общий разбор
    - неподдерживаемая форма («в пятницу вечером») → ParseError(invalid_format) → `format_parse_error` с допустимыми шаблонами

    Use `aiogram` (message.answer); `settings`; `reminders`; `parser`; ensure_user.

"format_parse_error(error: ParseError) -> text: str":
  location: formatter.py
  annotations: |
    Текст отказа по варианту ошибки парсера: шаблон и пример.

    `error`: ошибка парсера
    `text`: пользовательский текст (формат + пример / просьба будущего времени / и т.п.)

    Algorithm:
    1. По `error`.kind выбрать шаблон: invalid_format → формат и пример; past → просьба указать будущее; horizon_exceeded → про предел 365 дней; empty_text → про пустой текст; date_not_exist → про несуществующую дату; text_too_long → про превышение лимита 500 символов
    2. Вернуть `text`

    Use `parser` (ParseError kinds).

"format_reminder_confirmation(reminder: Reminder, timezone: str) -> text: str":
  location: formatter.py
  annotations: |
    Подтверждение создания в локальном времени.

    `reminder`: созданное напоминание
    `timezone`: IANA-зона пользователя
    `text`: подтверждение с локальным временем и текстом напоминания

    Algorithm:
    1. local = `to_local_string`(reminder.remind_at_utc, `timezone`)
    2. Вернуть подтверждение с local и reminder.text

    Use `models` (Reminder).

"format_reminder_list(reminders: list[Reminder], timezone: str) -> text: str":
  location: formatter.py
  annotations: |
    Список активных напоминаний в локальном времени.

    `reminders`: список Reminder текущего пользователя
    `timezone`: IANA-зона пользователя
    `text`: пронумерованный список (локальное время + текст) либо «нет напоминаний»

    Algorithm:
    1. Если `reminders` пуст — вернуть текст об отсутствии напоминаний
    2. Для каждого reminder собрать строку: `to_local_string`(reminder.remind_at_utc, `timezone`) + reminder.text
    3. Вернуть объединённый список

    Use `models` (Reminder).

"format_notification(reminder: Reminder) -> text: str":
  location: formatter.py
  annotations: |
    Текст уведомления пользователю.

    `reminder`: отправляемое напоминание
    `text`: строка вида «Напоминание\n<reminder.text>»

    Algorithm:
    1. Вернуть «Напоминание» + перенос строки + reminder.text

    Use `models` (Reminder).

"to_local_string(utc_iso: str, timezone: str) -> text: str":
  location: formatter.py
  annotations: |
    Helper: aware UTC (ISO 8601 строка из БД) → локальная строка пользователя.

    `utc_iso`: момент в формате ISO 8601 UTC
    `timezone`: IANA-зона пользователя
    `text`: локальная строка даты/времени

    Algorithm:
    1. Распарсить `utc_iso` в aware UTC datetime
    2. Конвертировать в ZoneInfo(`timezone`)
    3. Отформатировать в локальную строку

    Constraints:
    - работает со строкой ISO 8601, как хранится в БД (reminder.remind_at_utc)
    - фиксированный человекочитаемый формат локальной строки (например «DD.MM.YYYY HH:MM»), единый для всех форматтеров клетки `bot`
```

- **Footer** — `Description` расширить:
```yaml
Description: |
  Обработчики и форматтер Telegram-бота RemindMe: /start, /help, /remind, /reminders, фраза «напомни …», форматирование ответов и уведомлений, фильтр приватных чатов.
```

#### `.usages/` файлы

**Файл:** `src/remindme/bot/.usages/handlers.md` — ✚ MODIFIED (полное обновлённое содержимое)

```md
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
```

**Файл:** `src/remindme/bot/.usages/formatter.md` — ✦ NEW

```md
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
```

**Файл:** `src/remindme/bot/.usages/testing.md` — ✚ MODIFIED (полное обновлённое содержимое)

```md
# Handlers и formatter — вызов в тестах

Домен: проверка обработчиков и форматеров прямым вызовом с mock Telegram API.
Аудитория: тесты `tests/unit` и `tests/integration`.

Обработчики вызываются как async-функции; `Message` подменяется объектом с нужными атрибутами, отправка ответа — mock. Текущий момент и часовой пояс передаются явно; реальный `sleep` не используется.

## /remind и фраза «напомни»

```python
from unittest.mock import AsyncMock
from .bot.handlers import cmd_remind, handle_remind_phrase

message = AsyncMock()
message.chat.type = "private"
message.from_user.id = 123
message.text = "/remind 2026-06-24 18:00 | Купить продукты"
message.answer = AsyncMock()

await cmd_remind(message)
assert message.answer.await_count == 1
```

## Formatter (прямой вызов)

```python
from .bot import format_notification, to_local_string

assert format_notification(reminder) == "Напоминание\nКупить продукты"
assert to_local_string("2026-06-24T15:00:00+00:00", "Europe/Moscow")  # локальная строка
```

## Предусловия и побочные эффекты

- Telegram Bot API (`message.answer`, `bot.send_message`) подменяется mock; проверяются аргументы вызовов.
- Побочные эффекты `/remind`/`handle_remind_phrase` (создание `Reminder`) проверяются через временную SQLite-сессию.
- `now` и часовой пояс передаются явно; `datetime.now()` внутри логики не вызывается.
```

---

### Cell: `src/remindme/worker` — ✦ NEW

#### CODEMANIFEST (`src/remindme/worker/CODEMANIFEST`) — полный

```yaml
Imports:
  - Types:
      - Reminder
      - find_due_reminders
      - claim_for_sending
      - mark_sent
    Usages:
      - repositories
    From: src/remindme/db
  - Types:
      - format_notification
    Usages:
      - formatter
    From: src/remindme/bot

Usages:
  conventions: .goga/usages/conventions.md
  aiogram: .goga/usages/cooks/aiogram.md

Annotations: |
  Используй `conventions` для написания правил и тестирования кода.
  Используй `aiogram` для Bot и send_message.
  Используй `repositories`, `formatter` из Imports для доступа к данным и текста уведомления.

  Цикл доставки уведомлений: один фоновый процесс в одном event loop с polling. Каждая итерация открывает свою сессию через `session_factory`. Атомарный захват записи гарантирует, что две итерации не заберут одно напоминание. Токен не логируется.

---

"run_reminder_worker(bot: Bot, session_factory: AsyncSessionFactory, poll_interval: int)":
  location: notifications.py
  annotations: |
    Бесконечный цикл доставки просроченных напоминаний.

    `bot`: экземпляр Bot для отправки
    `session_factory`: фабрика async-сессий (от точки входа)
    `poll_interval`: интервал опроса, секунд

    Algorithm:
    1. Бесконечный цикл:
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
             3. except (ошибка send_message): залогировать (structured logging, ERROR, без токена); запись остаётся в 'sending'; продолжить к следующему reminder
       6. Закрыть сессию (транзакция уже зафиксирована в claim_for_sending / mark_sent)

    Requirements:
    - одна итерация цикла = одна сессия
    - атомарный захват через `claim_for_sending` (status='scheduled' → 'sending') перед отправкой
    - ошибка отправки одного reminder не прерывает цикл: перехватывается, логируется, запись остаётся в 'sending'

    Constraints:
    - базовый путь этапа 2: успешная отправка → 'sent'; без повторных попыток, статуса 'failed' и восстановления зависших 'sending' (этап 3)
    - ошибка `send_message` в этапе 2 изолируется per-reminder (try/except + логирование): запись остаётся в 'sending', цикл продолжается к следующему напоминанию; без перевода в 'failed', без повторных попыток и без восстановления зависших 'sending' (этап 3)
    - отменённые записи не отправляются: `claim_for_sending` фильтрует status='scheduled', а отмена переводит в 'cancelled'

    Use `aiogram` (send_message); `repositories` (find_due_reminders, claim_for_sending, mark_sent); `formatter` (format_notification).

---

Author: Goga
CreatedAt: 29/06/26
Description: |
  Цикл доставки уведомлений: опрос просроченных напоминаний, атомарный захват и отправка в одном event loop с polling.
```

#### `.usages/` файлы

**Файл:** `src/remindme/worker/.usages/running.md` — ✦ NEW

```md
# Worker уведомлений — запуск цикла доставки

Домен: запуск бесконечного цикла доставки просроченных напоминаний в одном event loop с long polling.
Аудитория: клетка `main` (точка входа).

Worker работает как фоновая задача рядом с polling. Каждую итерацию открывает свою сессию через `session_factory`, выбирает просроченные `scheduled`-напоминания, атомарно захватывает каждое (`scheduled → sending`), отправляет текст и отмечает отправку (`→ sent`).

## Запуск

```python
import asyncio
from .worker import run_reminder_worker

asyncio.create_task(
    run_reminder_worker(bot=bot, session_factory=session_factory, poll_interval=settings.REMINDER_POLL_INTERVAL_SECONDS)
)
await dp.start_polling(bot)
```

`poll_interval` берётся из настроек (`REMINDER_POLL_INTERVAL_SECONDS`, положительное целое). `session_factory` и `bot` строятся точкой входа один раз.

## Предусловия и побочные эффекты

- Запускается как asyncio-задача в том же event loop, что и polling; `session_factory` и `bot` уже построены.
- Атомарный захват (`claim_for_sending`, `UPDATE … WHERE status='scheduled'` + `rowcount`) гарантирует, что две итерации не заберут одну запись.
- Отменённые записи (`cancelled`) не отправляются — `claim_for_sending` захватывает только `scheduled`.
- Базовый путь этапа 2: без повторных попыток, `failed` и восстановления зависших `sending` (этап 3).
- Ошибка `send_message` в этапе 2 изолируется per-reminder (try/except + логирование, без токена): запись остаётся в `sending`, цикл продолжается к следующему напоминанию; без перевода в `failed`, без повторных попыток и без восстановления зависших `sending` (этап 3).
- Токен не логируется и не попадает в тексты; `chat_id` = `reminder.user_id` (бот работает только в личных чатах).
```

---

### Cell: `src/remindme/main` — ✚ MODIFIED

#### CODEMANIFEST (`src/remindme/main/CODEMANIFEST`) — diff

- **Header `Imports`** — ДОБАВИТЬ импорт из worker (существующие импорты из config/db/bot без изменений):

```yaml
  - Types:
      - run_reminder_worker
    Usages:
      - running
    From: src/remindme/worker
```

- **Header `Usages`** — без изменений (`conventions`, `aiogram`).
- **Header `Annotations`** — заменить на:

```yaml
Annotations: |
  Используй `conventions` для написания правил и тестирования кода.
  Используй `aiogram` для Bot, Dispatcher и long polling.

  Точка входа приложения: один процесс, запуск из venv. Токен берётся из настроек и передаётся в Bot; не логируется. Меню команд формируется инкрементально — на этапе 2 добавлены /remind и /reminders. Цикл доставки worker запускается как фоновая задача в одном event loop с polling.
```

- **Body** — ИЗМЕНИТЬ существующие routine (новых типов нет):

`set_commands(bot: Bot)`:
```yaml
"set_commands(bot: Bot)":
  location: main.py
  annotations: |
    Регистрирует меню команд, видимое пользователю. Инкрементально: на этапе 2 — /start, /help, /remind, /reminders.

    `bot`: экземпляр Bot

    Algorithm:
    1. Вызвать у `bot` set_my_commands со списком BotCommand для /start, /help, /remind, /reminders

    Requirements:
    - Регистрируются только команды, реализованные на текущем этапе

    Use `aiogram` (set_my_commands, BotCommand).
```

`main()`:
```yaml
"main()":
  location: main.py
  annotations: |
    Точка входа приложения: сборка инфраструктуры, инициализация бота, регистрация обработчиков, запуск worker и long polling.

    Algorithm:
    1. Получить настройки через `get_settings`
    2. Построить async-движок и фабрику сессий (`create_engine`, `create_session_factory`)
    3. Создать бота с токеном из настроек
    4. Создать диспетчер
    5. Зарегистрировать обработчики через `register_handlers`, передав построенную фабрику сессий
    6. Зарегистрировать меню команд через `set_commands`
    7. Запустить цикл доставки как asyncio-задачу: `run_reminder_worker`(bot, session_factory, settings.REMINDER_POLL_INTERVAL_SECONDS)
    8. Сбросить отложенные обновления перед стартом
    9. Запустить long polling (worker работает параллельно в том же event loop)

    Requirements:
    - Приложение работает в одном процессе

    Constraints:
    - Токен не логируется и не попадает в тексты ошибок
    - Worker и polling работают в одном event loop

    Use `aiogram` (Bot, Dispatcher, start_polling); `settings`; `engine`; `handlers`; `running` (run_reminder_worker).
```

- **Footer** — `Description` расширить:
```yaml
Description: |
  Точка входа RemindMe: сборка инфраструктуры данных, инициализация бота, регистрация обработчиков, запуск worker уведомлений и long polling.
```

#### `.usages/` файлы

**Файл:** `src/remindme/main/.usages/running.md` — ✚ MODIFIED (полное обновлённое содержимое)

```md
# Запуск приложения

Домен: точка входа и запуск бота RemindMe (long polling + worker уведомлений).
Аудитория: оператор/разработчик, запускающий приложение локально или в Docker, и smoke-тест.

Приложение работает одним процессом: бот и worker уведомлений в одном event loop.

## Запуск

```bash
# внутри venv, с подготовленным .env и применёнными миграциями
python -m remindme.main
```

`main()` собирает движок и фабрику сессий, создаёт `Bot` и `Dispatcher`, регистрирует обработчики, регистрирует меню команд, запускает цикл доставки worker как asyncio-задачу (`run_reminder_worker(bot, session_factory, REMINDER_POLL_INTERVAL_SECONDS)`) и запускает long polling (worker работает параллельно в том же event loop).

## Предусловия

- Существует `.env` с обязательным `TELEGRAM_BOT_TOKEN` (остальные переменные имеют значения по умолчанию).
- Применены миграции схемы: `alembic upgrade head`.
- Директория `data/` создаётся автоматически при первом подключении.

## Побочные эффекты

- Первый вызов `get_settings()` валидирует конфигурацию; при ошибке (нет токена, неизвестная зона, неверное время) приложение завершается понятной ошибкой без значения токена.
- При запуске регистрируется меню команд `/start`, `/help`, `/remind`, `/reminders` (инкрементально; полный список — на этапе 4).
- Бот обрабатывает сообщения только в приватных чатах.
- Worker уведомлений опрашивает просроченные напоминания каждые `REMINDER_POLL_INTERVAL_SECONDS`; отправленные переводятся в `sent`.
```

---

## Dependency Map

```
config ───────────────────────────────────── (leaf, без изменений)
   ▲ (Settings)
   │
   db (✚) ─────────────────────────────────── ← config
   ▲ ▲
   │ │ (Reminder, create_reminder, cancel_reminder, repositories)
   │ │
   │ └────────────► services (✦) ──────────── ← db
   │                       ▲
   │ (Reminder, find_due, │ (create_reminder_scenario, cancel_reminder_scenario,
   │  claim, mark_sent,    │  ParseError, repositories)
   │  repositories)        │
   │         ┌─────────────┴────────── bot (✚) ◄─────── (format_notification, formatter)
   │         │                            ▲
   │         │                            │
   │     worker (✦) ◄─────────────────────┘  ← db, bot
   │         ▲
   │         │ (run_reminder_worker, running)
   │         │
   └────── main (✚) ──────────────────────── ← config, db, bot, worker (root)
```

Импортные связи (Source → Target: типы / usage):
- services → db: `Reminder`, `create_reminder`, `cancel_reminder` / `repositories`
- worker → db: `Reminder`, `find_due_reminders`, `claim_for_sending`, `mark_sent` / `repositories`
- worker → bot: `format_notification` / `formatter`
- bot → config: `get_settings` / `settings`
- bot → db: `User`, `Reminder`, `list_reminders` / `models`, `repositories`
- bot → services: `create_reminder_scenario`, `ParseError` / `reminders`, `parser`
- main → config: `get_settings` / `settings`
- main → db: `create_engine`, `create_session_factory` / `engine`
- main → bot: `register_handlers` / `handlers`
- main → worker: `run_reminder_worker` / `running`

Циклов нет. Топопорядок: config → db → services → bot → worker → main.

---

## Verification Checklist

После реализации каждого артефакта:

**config** (без изменений):
- [ ] `Settings` экспортирует `DEFAULT_TIMEZONE`, `DEFAULT_REMINDER_TIME`, `REMINDER_POLL_INTERVAL_SECONDS` (валидаторы готовы).

**db** (`repositories.py` ✚ + `repositories.md` ✦):
- [ ] 6 routines в `repositories.py` соответствуют сигнатурам CODEMANIFEST.
- [ ] Изоляция `(id, user_id)` во всех изменениях/выборках; `list_reminders` только scheduled + limit 20 + order_by.
- [ ] Атомарный захват `claim_for_sending` через `UPDATE … WHERE status='scheduled'` + `rowcount`.
- [ ] Откат транзакции + re-raise при SQLAlchemyError.
- [ ] ISO 8601 UTC на границе datetime↔str.
- [ ] `goga lint src/remindme/db` проходит.

**services** (`CODEMANIFEST` ✦ + `parser.md`, `reminders.md` ✦):
- [ ] `parse_remind_time` — чистая (без `datetime.now()`/Telegram/БД); `now` фиксируется один раз.
- [ ] 5 шаблонов фразы, `re.IGNORECASE`, приоритет частное→общее; режим `/remind` — split по первому `|`.
- [ ] Календарная проверка `strptime` (`31.02` → `date_not_exist`).
- [ ] Границы: `past` (включая ровно `now`), `horizon_exceeded` (>365д), `empty_text`, `invalid_format`, `text_too_long`.
- [ ] `ParsedReminder`/`ParseError` — pydantic `kw_only=True`.
- [ ] `create_reminder_scenario` → `Reminder | ParseError` (без сохранения при ошибке парсинга).
- [ ] `cancel_reminder_scenario` → `bool` (делегирует `cancel_reminder` с проверкой владельца; покрыт тестом наравне с созданием).
- [ ] Параметризованные unit-тесты парсера (все формы единиц, `мне`/без, регистр, `|`/без, `00:00`/`23:59`, `31.02`, прошлое, ровно `now`, граница 365 дней, 1/500/501 символ).
- [ ] `goga lint src/remindme/services` проходит.

**bot** (`handlers.py`, `formatter.py` ✚ + `handlers.md`, `testing.md` ✚, `formatter.md` ✦):
- [ ] Импорт `Reminder`, `list_reminders` из db + импорт сценариев/`ParseError` из services.
- [ ] `register_handlers` порядок: `cmd_*` → `handle_remind_phrase` → `handle_text` → `handle_group`.
- [ ] `cmd_remind`/`handle_remind_phrase`: лимит сообщения 1000; ветвление `Reminder|ParseError`.
- [ ] `cmd_reminders`: только записи текущего пользователя (`list_reminders(user_id)`).
- [ ] `format_notification` = `«Напоминание\n<text>»`; `to_local_string` принимает ISO 8601 str.
- [ ] `format_parse_error` по `kind` (шаблон + пример).
- [ ] `setMyCommands` (через main) добавит `/remind`, `/reminders`.
- [ ] `goga lint src/remindme/bot` проходит.

**worker** (`CODEMANIFEST` ✦ + `running.md` ✦):
- [ ] `run_reminder_worker`: одна сессия на итерацию; `find_due`→`claim`→`send_message`→`mark_sent`.
- [ ] Ошибка `send_message` изолируется per-reminder (try/except + логирование): цикл продолжается, запись остаётся `sending`.
- [ ] `claim_for_sending` gate перед отправкой; отменённые не отправляются.
- [ ] `chat_id=reminder.user_id`; токен не логируется.
- [ ] Без повторных попыток/`failed`/восстановления (этап 3).
- [ ] `goga lint src/remindme/worker` проходит.

**main** (`main.py` ✚ + `running.md` ✚):
- [ ] Импорт `run_reminder_worker` из worker.
- [ ] `main()` запускает worker как asyncio-задачу в одном event loop с `start_polling`.
- [ ] `set_commands` регистрирует `/start`, `/help`, `/remind`, `/reminders`.
- [ ] `goga lint src/remindme/main` проходит.

**Сквозные проверки**:
- [ ] `goga schema` показывает связи без циклов.
- [ ] `goga contract` для каждой cell — контракт соответствует реализации.
- [ ] Acceptance-критерии 1–10 (см. PRIMARY_ANALYSIS) выполняются.
