# Архитектурный план — RemindMe, Этап 1: Основа (`foundation`)

План описывает клетки (cells), CODEMANIFEST и `.usages/`-файлы, которые нужно создать для фундамента приложения RemindMe (Этап 1 серии из пяти этапов).

План содержит **только** контракты (CODEMANIFEST) и практики (`.usages/`) — без кода реализации. Вспомогательные артефакты (миграции Alembic, тестовый `conftest`, `pyproject.toml`) перечислены как неклеточные артефакты с требованиями (без кода).

**Контекст проекта:** зелёное поле (`goga schema` → `[]`); все клетки создаются заново. Пакетный layout — src-layout с пакетом `remindme` (`src/remindme/...`). Целевой язык — Python 3.12.

---

## Topic

`foundation` — путь плана: `docs/arch/foundation.md`.

---

## Implementation Order

Клетки реализуются снизу вверх (leaves → root) — клетку можно собрать только после её зависимостей, от которых она импортирует типы/практики.

1. **`src/remindme/config`** — лист: не имеет `Imports`. Фундамент конфигурации, потребляется всеми остальными клетками.
2. **`src/remindme/db`** — зависит от `config` (`Imports.Types: Settings`). Модели нужны `bot` и миграциям; сессии нужны `bot` и `main`.
3. **`src/remindme/bot`** — зависит от `config` (`get_settings`) и `db` (`User`, `create_session_factory`). Экспортирует фасад `register_handlers`, который `main` вызывает для регистрации handlers.
4. **`src/remindme/main`** — корень: зависит от `config`, `db`, `bot`. Собирает всё в точку входа.

Параллельно с клетками (неклеточные артефакты): `pyproject.toml` (+ venv, конфиг ruff/pytest) — предусловие запуска; `alembic/` и `tests/conftest.py` — после клетки `db` (нужны `Base`/`models`).

---

## Artifacts

Все артефакты — **create anew** (существующей архитектуры нет).

### Cell: `src/remindme/config`

#### CODEMANIFEST — `src/remindme/config/CODEMANIFEST`

````yaml
Usages:
  conventions: .goga/usages/conventions.md
  pydantic-settings: .goga/usages/cooks/pydantic-settings.md

Annotations: |
  Используй `conventions` для написания правил и тестирования кода.
  Используй `pydantic-settings` для `BaseSettings`, `SecretStr` и шаблонов валидаторов.

  Все настройки загружаются из `.env` и переменных окружения (env имеет приоритет).
  Импорты внутри пакета — относительные; классы моделей — pydantic с `kw_only=True`.
  Токен хранится как `SecretStr` и не должен попадать в repr, логи и тексты ошибок.

---

"Settings()":
  location: config.py
  annotations: |
    Конфигурация приложения RemindMe: единственный источник настроек для всех клеток.
    Загружается из `.env`/окружения через `pydantic-settings`.

    `pydantic-settings` — для `BaseSettings`, `model_config`, `SecretStr` и валидаторов.
    `conventions` — для pydantic `kw_only=True` и Google-docstrings.

    Requirements:
    - `TELEGRAM_BOT_TOKEN` — обязательный `SecretStr` (токен BotFather)
    - `DEFAULT_TIMEZONE` — валидное имя IANA (по умолчанию `Europe/Moscow`)
    - `DEFAULT_REMINDER_TIME` — строка `ЧЧ:ММ` (по умолчанию `09:00`)
    - `REMINDER_POLL_INTERVAL_SECONDS` — положительное целое (по умолчанию `5`)
    - `DATABASE_URL`, `LOG_LEVEL` — имеют значения по умолчанию

    Constraints:
    - Запуск без `TELEGRAM_BOT_TOKEN`, с неизвестной зоной или неверным `DEFAULT_REMINDER_TIME`
      завершается понятной ошибкой, не содержащей значения токена
  properties:
    "TELEGRAM_BOT_TOKEN -> SecretStr": |
      Токен Telegram-бота от BotFather; обязательный, доступ только через `.get_secret_value()`.
    "DATABASE_URL -> str": |
      URL подключения к SQLite (по умолчанию `sqlite+aiosqlite:///data/remindme.db`).
    "DEFAULT_TIMEZONE -> str": |
      Часовой пояс нового пользователя (по умолчанию `Europe/Moscow`); проверяется `validate_timezone`.
    "DEFAULT_REMINDER_TIME -> str": |
      Время напоминания по умолчанию в формате `ЧЧ:ММ` (по умолчанию `09:00`); проверяется `validate_reminder_time`.
    "REMINDER_POLL_INTERVAL_SECONDS -> int": |
      Интервал опроса worker, секунд (по умолчанию `5`); проверяется `validate_poll_interval`.
    "LOG_LEVEL -> str": |
      Уровень логирования (по умолчанию `INFO`).
  methods:
    "validate_timezone(value: str) -> str": |
      Проверяет, что значение — корректное имя IANA-зоны.

      `value`: исходное значение поля `DEFAULT_TIMEZONE`

      Algorithm:
      1. Если `value` отсутствует в `available_timezones()` — поднять `ValueError("unknown IANA timezone: {value}")`
      2. Дополнительно построить `ZoneInfo(value)` для гарантии
      3. Вернуть `value`

      Constraints:
      - Текст ошибки не должен содержать значение токена

      Use `pydantic-settings` (шаблон `_valid_timezone`).
    "validate_reminder_time(value: str) -> str": |
      Проверяет, что значение — строка `ЧЧ:ММ` с корректным диапазоном.

      `value`: исходное значение поля `DEFAULT_REMINDER_TIME`

      Algorithm:
      1. Разделить `value` по `":"`
      2. Если часы не 2 цифры в `0..23` или минуты не 2 цифры в `0..59` — поднять `ValueError("DEFAULT_REMINDER_TIME must be HH:MM")`
      3. Вернуть `value`

      Use `pydantic-settings` (шаблон `_valid_time`).
    "validate_poll_interval(value: int) -> int": |
      Запрещает ноль и отрицательные значения, чтобы цикл worker не ушёл в плотный поллинг.

      `value`: исходное значение поля `REMINDER_POLL_INTERVAL_SECONDS`

      Algorithm:
      1. Если `value <= 0` — поднять `ValueError`
      2. Вернуть `value`

"get_settings() -> settings: Settings":
  location: config.py
  annotations: |
    Возвращает синглтон настроек; создание и валидация выполняются один раз при первом вызове.

    `settings`: закешированный экземпляр `Settings`

    Algorithm:
    1. При первом вызове создать экземпляр `Settings` и закешировать
    2. При последующих вызовах возвращать закешированный экземпляр

    Requirements:
    - Переменные окружения имеют приоритет над `.env`

    Use `pydantic-settings` для точки доступа к настройкам.

---

Author: Goga
CreatedAt: 29/06/26
Description: |
  Конфигурация приложения RemindMe: загрузка и валидация настроек из `.env` и окружения.
````

#### `.usages/` — `src/remindme/config/.usages/settings.md`

````md
# Настройки приложения — потребление

Домен: чтение конфигурации приложения RemindMe (токен, подключение к БД, часовой пояс, параметры worker).
Аудитория: клетки `db`, `bot`, `main` — любой модуль, которому нужны значения настроек.

Все настройки предоставляет единый синглтон `Settings` через `get_settings()`.
Напрямую конструировать `Settings()` не нужно — используйте точку доступа.

## Чтение настроек

```python
from .config import get_settings

settings = get_settings()
database_url = settings.DATABASE_URL
timezone = settings.DEFAULT_TIMEZONE
poll_interval = settings.REMINDER_POLL_INTERVAL_SECONDS
```

`get_settings()` создаёт и валидирует `Settings` один раз; повторные вызовы возвращают тот же экземпляр.
Переменные окружения имеют приоритет над `.env`.

## Доступ к токену бота

`TELEGRAM_BOT_TOKEN` — `SecretStr`. Значение получают явно; оно не печатается в repr и логах.

```python
token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
```

## Предусловия и побочные эффекты

- Первый вызов `get_settings()` читает `.env` и выполняет валидаторы; при отсутствии токена, неизвестной зоне
  или неверном формате времени поднимается исключение — вызывайте до старта event loop.
- Токен не должен попадать в логи, тексты ошибок и исключения; передавайте его только в `Bot(token=...)`.
- Значения уже валидны: часовой пояс — корректная IANA-зона, `DEFAULT_REMINDER_TIME` — `ЧЧ:ММ`,
  `REMINDER_POLL_INTERVAL_SECONDS` — положительное целое.
````

---

### Cell: `src/remindme/db`

#### CODEMANIFEST — `src/remindme/db/CODEMANIFEST`

````yaml
Imports:
  - Types:
      - Settings
    From: src/remindme/config

Usages:
  conventions: .goga/usages/conventions.md
  sqlalchemy: .goga/usages/cooks/sqlalchemy.md
  aiosqlite: .goga/usages/cooks/aiosqlite.md

Annotations: |
  Используй `conventions` для написания правил и тестирования кода.
  Используй `sqlalchemy` для async-движка, сессий, PRAGMA-listener и декларативных моделей.
  Используй `aiosqlite` для URL подключения и ограничений SQLite.

  Все timestamp-поля хранятся как `Text` в формате ISO 8601 (UTC).
  На каждом соединении активны `PRAGMA foreign_keys=ON` и `journal_mode=WAL`.
  Используется async SQLAlchemy 2.x (`Mapped`/`mapped_column`); импорты внутри пакета — относительные.

---

"Base()":
  location: models.py
  annotations: |
    Декларативный корень ORM; держит `metadata` всех моделей. Используется миграциями и созданием схемы.

    Use `sqlalchemy` (`DeclarativeBase`).
  properties:
    "metadata -> MetaData": |
      Реестр таблиц всех моделей; источник схемы для миграций.

"User()":
  location: models.py
  annotations: |
    Пользователь бота; PK `telegram_user_id`; цель FK для `Reminder`/`Note`/`Todo`.

    Requirements:
    - `telegram_user_id` — PRIMARY KEY и UNIQUE (идемпотентность регистрации при конкурентных сообщениях)
    - `timezone` — NOT NULL (String(64)), валидная IANA-зона
    - `created_at_utc`, `updated_at_utc` — NOT NULL, ISO 8601

    Constraints:
    - timestamp-поля хранятся как `Text` ISO 8601
  properties:
    "telegram_user_id -> int": |
      PRIMARY KEY и UNIQUE; идентификатор Telegram-пользователя.
    "timezone -> str": |
      IANA-зона пользователя, NOT NULL (String(64)).
    "created_at_utc -> str": |
      Время создания, ISO 8601 UTC, NOT NULL.
    "updated_at_utc -> str": |
      Время последнего обновления, ISO 8601 UTC, NOT NULL.

"Reminder()":
  location: models.py
  annotations: |
    Одноразовое напоминание; `user_id` → FK `users.telegram_user_id`.

    Requirements:
    - `id` PRIMARY KEY autoincrement; `user_id` NOT NULL FK
    - `text` NOT NULL; `remind_at_utc`, `next_attempt_at_utc`, `created_at_utc` NOT NULL ISO 8601
    - `status` NOT NULL: `scheduled|sending|sent|cancelled|failed`
    - `attempt_count` NOT NULL, default 0
    - `locked_at_utc`, `sent_at_utc` — nullable

    Constraints:
    - timestamp-поля — `Text` ISO 8601; FK обеспечивает `IntegrityError` при отсутствии `user_id`
  properties:
    "id -> int": |
      PRIMARY KEY autoincrement.
    "user_id -> int": |
      FK → `users.telegram_user_id`, NOT NULL.
    "text -> str": |
      Текст напоминания, NOT NULL.
    "remind_at_utc -> str": |
      Момент напоминания, ISO 8601 UTC, NOT NULL.
    "status -> str": |
      NOT NULL: `scheduled|sending|sent|cancelled|failed`.
    "attempt_count -> int": |
      NOT NULL, default 0.
    "next_attempt_at_utc -> str": |
      Время следующей попытки, ISO 8601 UTC, NOT NULL.
    "locked_at_utc -> str | None": |
      Время захвата worker, nullable.
    "created_at_utc -> str": |
      Время создания, ISO 8601 UTC, NOT NULL.
    "sent_at_utc -> str | None": |
      Время отправки, nullable.

"Note()":
  location: models.py
  annotations: |
    Заметка без статуса и срока; `user_id` → FK `users.telegram_user_id`.

    Requirements:
    - `id` PRIMARY KEY autoincrement; `user_id` NOT NULL FK; `text` NOT NULL; `created_at_utc` NOT NULL ISO 8601

    Constraints:
    - timestamp-поля — `Text` ISO 8601
  properties:
    "id -> int": |
      PRIMARY KEY autoincrement.
    "user_id -> int": |
      FK → `users.telegram_user_id`, NOT NULL.
    "text -> str": |
      Текст заметки, NOT NULL.
    "created_at_utc -> str": |
      Время создания, ISO 8601 UTC, NOT NULL.

"Todo()":
  location: models.py
  annotations: |
    Задача с необязательным сроком и статусом; `user_id` → FK `users.telegram_user_id`.

    Requirements:
    - `id` PRIMARY KEY autoincrement; `user_id` NOT NULL FK; `text` NOT NULL
    - `due_at_utc`, `completed_at_utc` — nullable; `created_at_utc` NOT NULL ISO 8601
    - `status` NOT NULL: `active|completed`

    Constraints:
    - timestamp-поля — `Text` ISO 8601
  properties:
    "id -> int": |
      PRIMARY KEY autoincrement.
    "user_id -> int": |
      FK → `users.telegram_user_id`, NOT NULL.
    "text -> str": |
      Текст задачи, NOT NULL.
    "due_at_utc -> str | None": |
      Срок задачи, nullable.
    "status -> str": |
      NOT NULL: `active|completed`.
    "created_at_utc -> str": |
      Время создания, ISO 8601 UTC, NOT NULL.
    "completed_at_utc -> str | None": |
      Время завершения, nullable.

"create_engine(settings: Settings) -> engine: AsyncEngine":
  location: session.py
  annotations: |
    Создаёт async-движок SQLite из `DATABASE_URL`, включает PRAGMA и обеспечивает директорию `data/`.

    `settings`: конфигурация (`DATABASE_URL`)
    `engine`: async-движок с настроенными PRAGMA

    Algorithm:
    1. Взять `DATABASE_URL` из аргумента `settings`
    2. Создать родительскую директорию файла БД (`data/`), если она отсутствует
    3. Построить async-движок через `create_async_engine`
    4. На `engine.sync_engine` зарегистрировать listener события `connect`, выполняющий
       `PRAGMA foreign_keys=ON` и `PRAGMA journal_mode=WAL`
    5. Вернуть движок

    Requirements:
    - `data/` существует к моменту первого подключения
    - PRAGMA применяются на каждом новом соединении

    Use `sqlalchemy` (`create_async_engine`, `event` listener); `aiosqlite` (URL/директория).

"create_session_factory(engine: AsyncEngine) -> factory: AsyncSessionFactory":
  location: session.py
  annotations: |
    Возвращает фабрику async-сессий поверх движка.

    `engine`: async-движок
    `factory`: вызываемая фабрика (`async_sessionmaker`)

    Algorithm:
    1. Построить фабрику сессий через `async_sessionmaker` поверх `engine` с `expire_on_commit=False`
    2. Вернуть фабрику

    Requirements:
    - `expire_on_commit=False`, чтобы объекты оставались доступны после `commit`

    Use `sqlalchemy` (`async_sessionmaker`).

---

Author: Goga
CreatedAt: 29/06/26
Description: |
  Слой данных RemindMe: декларативные модели (Base, User, Reminder, Note, Todo) и async-подключение к SQLite.
````

#### `.usages/` — `src/remindme/db/.usages/engine.md`

````md
# Движок и фабрика сессий — bootstrap

Домен: создание async-движка SQLite и фабрики сессий на старте приложения.
Аудитория: клетка `main` (точка входа) и тестовые фикстуры `tests/conftest.py`.

Движок и фабрика создаются один раз при запуске и переиспользуются всеми обработчиками.

## Создание движка и фабрики

```python
from .config import get_settings
from .db.session import create_engine, create_session_factory

settings = get_settings()
engine = create_engine(settings)
session_factory = create_session_factory(engine)
```

`create_engine` включает `PRAGMA foreign_keys=ON` и `journal_mode=WAL` на каждом соединении и создаёт директорию `data/`. `create_session_factory` возвращает вызываемую фабрику с `expire_on_commit=False`.

## Использование фабрики

```python
async with session_factory() as session:
    ...
    await session.commit()
```

Каждый вызов `session_factory()` открывает новую `AsyncSession`; контекстный менеджер закрывает её автоматически.

## Предусловия и побочные эффекты

- `create_engine` читает `settings.DATABASE_URL`; родительская директория файла БД создаётся при необходимости.
- PRAGMA применяются через listener события `connect` — на каждом новом соединении.
- В тестах используйте временную БД (`sqlite+aiosqlite:///<tmp>/test.db`) вместо `data/remindme.db`.
````

#### `.usages/` — `src/remindme/db/.usages/models.md`

````md
# Модели и сессия — ORM-операции

Домен: работа с моделями `User`, `Reminder`, `Note`, `Todo` через `AsyncSession`.
Аудитория: клетка `bot` (создание/поиск `User`) и тесты.

Все timestamp-поля — строки ISO 8601 (UTC). Внешние ключи включены, поэтому операции с несуществующим `user_id` завершаются `IntegrityError`.

## Создание записи пользователя

```python
from .db.models import User

user = User(
    telegram_user_id=telegram_id,
    timezone="Europe/Moscow",
    created_at_utc=now_iso,
    updated_at_utc=now_iso,
)
session.add(user)
await session.commit()
```

При нарушении `UNIQUE` по `telegram_user_id` (конкурентная регистрация) поднимается `IntegrityError` — используйте поиск существующей записи/upsert.

## Доступ к схеме

```python
from .db.models import Base

metadata = Base.metadata  # для миграций и create_all в тестах
```

## Предусловия и побочные эффекты

- Перед `commit` значения timestamp должны быть строками ISO 8601 UTC.
- `expire_on_commit=False`: объекты остаются доступны после `commit`.
````

---

### Cell: `src/remindme/bot`

#### CODEMANIFEST — `src/remindme/bot/CODEMANIFEST`

````yaml
Imports:
  - Types:
      - get_settings
    Usages:
      - settings
    From: src/remindme/config
  - Types:
      - User
    Usages:
      - models
    From: src/remindme/db

Usages:
  conventions: .goga/usages/conventions.md
  aiogram: .goga/usages/cooks/aiogram.md
  sqlalchemy: .goga/usages/cooks/sqlalchemy.md

Annotations: |
  Используй `conventions` для написания правил и тестирования кода.
  Используй `aiogram` для инициализации, фильтров, handlers и `set_my_commands`.

  Бот работает только в приватных чатах (`PrivateOnly`); в группах отвечает, что режим не поддерживается, и не пишет в БД.
  Порядок регистрации handlers: командные обработчики раньше общего текстового.
  Фабрика сессий передаётся точкой входа в `register_handlers` и замыкается в обработчиках; токен не попадает в логи и тексты.

---

"PrivateOnly()":
  location: handlers.py
  annotations: |
    Фильтр «только приватный чат»; отсекает сообщения из групп до любой записи в БД.

    Use `aiogram` (`BaseFilter`).

    Constraints:
    - При `chat.type != "private"` обработчик не выполняется и не пишет в БД
  methods:
    "__call__(message: Message) -> allowed: bool": |
      Возвращает `True`, только если сообщение из приватного чата.

      `message`: проверяемое сообщение
      `allowed`: признак приватного чата

      Algorithm:
      1. Вернуть `message.chat.type == "private"`

      Use `aiogram` (тип `Message`).

"ensure_user(session: AsyncSession, telegram_user_id: int, timezone: str) -> user: User":
  location: handlers.py
  annotations: |
    Идемпотентное создание или обновление `User`; общая логика регистрации для `/start` и авто-регистрации.

    `session`: открытая `AsyncSession`
    `telegram_user_id`: идентификатор Telegram-пользователя
    `timezone`: зона нового пользователя (передаётся вызывающей стороной из настроек)
    `user`: созданная либо существующая запись

    Algorithm:
    1. Найти `User` по `telegram_user_id`
    2. Если записи нет — создать с `timezone`, `created_at_utc = updated_at_utc = now_iso`, добавить и зафиксировать
    3. Если запись есть — обновить `updated_at_utc = now_iso` и зафиксировать
    4. Вернуть `user`

    Requirements:
    - Операция идемпотентна: повторный вызов с тем же `telegram_user_id` не создаёт дубль
    - При конкурентной регистрации нарушение `UNIQUE` обрабатывается повторным поиском существующей записи

    Use `models` (создание `User`); `sqlalchemy` (`add`/`commit`/`rollback`); `settings` (источник `timezone`).

"cmd_start(message: Message)":
  location: handlers.py
  annotations: |
    Обработчик `/start`: приветствие и краткая инструкция; делегирует регистрацию `ensure_user`.

    `message`: входящее сообщение (фильтруется `Command("start")` и `PrivateOnly`)

    Algorithm:
    1. Открыть сессию через переданную фабрику сессий
    2. Взять `timezone` из настроек (`get_settings`)
    3. Вызвать `ensure_user` для автора сообщения
    4. Ответить приветствием и краткой инструкцией

    Requirements:
    - Повторный `/start` не дублирует запись, обновляя `updated_at_utc`

    Use `aiogram` (`Command`, `message.answer`); `settings` (`timezone`); `ensure_user`.

"cmd_help(message: Message)":
  location: handlers.py
  annotations: |
    Обработчик `/help`: показывает список доступных команд (этап 1 — `/start`, `/help`).

    `message`: входящее сообщение (фильтруется `Command("help")` и `PrivateOnly`)

    Use `aiogram` (`Command`, `message.answer`).

"handle_unknown_command(message: Message)":
  location: handlers.py
  annotations: |
    Ответ на неизвестную команду: направляет к `/help`.

    `message`: входящее сообщение (фильтруется `PrivateOnly`)

    Constraints:
    - Не пишет в БД

"handle_text(message: Message)":
  location: handlers.py
  annotations: |
    Общий текстовый обработчик этапа 1: авто-регистрирует пользователя и отвечает подсказкой обратиться к `/help`.

    `message`: входящее свободное сообщение (фильтруется `PrivateOnly`, регистрируется после командных обработчиков)

    Algorithm:
    1. Открыть сессию через переданную фабрику сессий
    2. Взять `timezone` из настроек (`get_settings`)
    3. Вызвать `ensure_user` для автора сообщения
    4. Ответить: «напишите «напомни ...» или используйте /help»

    Constraints:
    - Не создаёт напоминания/заметки/задачи (появляются на этапе 2+); только регистрирует пользователя

    Use `aiogram` (`message.answer`); `settings` (`timezone`); `ensure_user`.

"handle_group(message: Message)":
  location: handlers.py
  annotations: |
    Ответ в неприватном чате: сообщает, что бот работает только в личных сообщениях, и не выполняет никакой записи.

    `message`: входящее сообщение из неприватного чата (перехватывается последним, после приватных обработчиков)

    Constraints:
    - Не пишет в БД
    - Отвечает фиксированным текстом о том, что режим не поддерживается

    Use `aiogram` (`message.answer`).

"register_handlers(dp: Dispatcher, session_factory: AsyncSessionFactory)":
  location: handlers.py
  annotations: |
    Фасад регистрации всех обработчиков этапа 1 в диспетчере с фильтром `PrivateOnly` и нужным порядком.

    `dp`: диспетчер aiogram
    `session_factory`: фабрика сессий, построенная точкой входа; замыкается в пишущих обработчиках для открытия сессии

    Algorithm:
    1. Зарегистрировать `cmd_start` с `PrivateOnly` и `Command("start")`
    2. Зарегистрировать `cmd_help` с `PrivateOnly` и `Command("help")`
    3. Зарегистрировать `handle_unknown_command` с `PrivateOnly` и `Command()`
    4. Зарегистрировать `handle_text` с `PrivateOnly` (последним среди приватных)
    5. Зарегистрировать `handle_group` без `PrivateOnly` самым последним: сообщения из групп, отсечённые `PrivateOnly` ранее, доходят сюда и получают ответ

    Requirements:
    - Командные обработчики регистрируются раньше общего текстового, чтобы команда не уходила в общий разбор
    - `handle_group` регистрируется последним и без `PrivateOnly`, поэтому приватные сообщения поглощаются более ранними обработчиками, а групповые доходят до него
    - `session_factory` создаётся точкой входа один раз и переиспользуется всеми пишущими обработчиками

    Use `aiogram` (`Dispatcher`, `Command`).

---

Author: Goga
CreatedAt: 29/06/26
Description: |
  Обработчики Telegram-бота RemindMe этапа 1: /start, /help, авто-регистрация пользователя, фильтр приватных чатов и ответ в группах.
````

#### `.usages/` — `src/remindme/bot/.usages/handlers.md`

````md
# Handlers — регистрация в диспетчере

Домен: подключение обработчиков клетки `bot` к aiogram `Dispatcher` через единый фасад.
Аудитория: клетка `main` (точка входа).

Все пишущие обработчики фильтруются `PrivateOnly` (только приватные чаты). Регистрация инкапсулирована в `register_handlers`: командные обработчики регистрируются раньше общего текстового, чтобы команда не уходила в общий разбор; последним без `PrivateOnly` регистрируется `handle_group`, который перехватывает сообщения из групп и отвечает, что режим не поддерживается.

## Регистрация

```python
from aiogram import Dispatcher

from .bot.handlers import register_handlers

dp = Dispatcher()
register_handlers(dp, session_factory)
```

`register_handlers` принимает фабрику сессий `session_factory`, построенную точкой входа, замыкает её в пишущих обработчиках и регистрирует `/start`, `/help`, обработчик неизвестной команды, общий текстовый обработчик (с `PrivateOnly`) и последним без `PrivateOnly` — `handle_group`.

## Предусловия и побочные эффекты

- `PrivateOnly` отсекает групповые сообщения от пишущих обработчиков; `handle_group` отвечает в группе, что режим не поддерживается, и не пишет в БД.
- Фабрика сессий `session_factory` строится точкой входа один раз и передаётся в `register_handlers`; пишущие обработчики открывают сессию через неё внутри себя.
````

#### `.usages/` — `src/remindme/bot/.usages/testing.md`

````md
# Handlers — вызов в тестах

Домен: проверка обработчиков прямым вызовом с mock Telegram API.
Аудитория: тесты `tests/unit` и `tests/integration`.

Обработчики вызываются как обычные async-функции; `Message` подменяется объектом с нужными атрибутами, отправка ответа — mock.

## Вызов обработчика

```python
from unittest.mock import AsyncMock
from .bot.handlers import cmd_start

message = AsyncMock()
message.chat.type = "private"
message.from_user.id = 123
message.answer = AsyncMock()

await cmd_start(message)
assert message.answer.await_count == 1
```

Побочные эффекты `cmd_start`/`handle_text` (создание/обновление `User`) проверяются через временную SQLite-сессию.

## Предусловия и побочные эффекты

- Текущий момент и часовой пояс передаются явно; реальный `sleep` не используется.
- Telegram Bot API (`message.answer`, `bot.send_message`) подменяется mock; проверяются аргументы вызовов.
- Для идемпотентности `/start` вызывайте обработчик дважды и проверяйте одну запись `User` и обновлённый `updated_at_utc`.
````

---

### Cell: `src/remindme/main`

#### CODEMANIFEST — `src/remindme/main/CODEMANIFEST`

````yaml
Imports:
  - Types:
      - get_settings
    Usages:
      - settings
    From: src/remindme/config
  - Types:
      - create_engine
      - create_session_factory
    Usages:
      - engine
    From: src/remindme/db
  - Types:
      - register_handlers
    Usages:
      - handlers
    From: src/remindme/bot

Usages:
  conventions: .goga/usages/conventions.md
  aiogram: .goga/usages/cooks/aiogram.md

Annotations: |
  Используй `conventions` для написания правил и тестирования кода.
  Используй `aiogram` для `Bot`, `Dispatcher` и long polling.

  Точка входа приложения: один процесс, запуск из venv.
  Токен берётся из настроек и передаётся в `Bot`; не логируется.
  Меню команд формируется инкрементально — на этапе 1 только `/start` и `/help`.

---

"set_commands(bot: Bot)":
  location: main.py
  annotations: |
    Регистрирует меню команд, видимое пользователю в интерфейсе Telegram. Инкрементально: на этапе 1 — `/start`, `/help`.

    `bot`: экземпляр `Bot`

    Algorithm:
    1. Вызвать `bot.set_my_commands` со списком `BotCommand` для `/start` и `/help`

    Requirements:
    - Регистрируются только команды, реализованные на текущем этапе

    Use `aiogram` (`set_my_commands`, `BotCommand`).

"main()":
  location: main.py
  annotations: |
    Точка входа приложения: сборка инфраструктуры данных, инициализация бота, регистрация обработчиков и запуск long polling.

    Algorithm:
    1. Получить настройки через `get_settings`
    2. Построить async-движок и фабрику сессий (`create_engine`, `create_session_factory`)
    3. Создать бота с токеном из настроек
    4. Создать диспетчер
    5. Зарегистрировать обработчики этапа 1 через `register_handlers`, передав построенную фабрику сессий
    6. Зарегистрировать меню команд через `set_commands`
    7. Сбросить отложенные обновления перед стартом
    8. Запустить long polling

    Requirements:
    - Приложение работает в одном процессе

    Constraints:
    - Токен не логируется и не попадает в тексты ошибок
    - Worker уведомлений не запускается на этапе 1 (появится на этапе 2)

    Use `aiogram` (`Bot`, `Dispatcher`, `start_polling`); `settings` (токен, `DATABASE_URL`); `engine` (`create_engine`, `create_session_factory`); `handlers` (регистрация обработчиков).

---

Author: Goga
CreatedAt: 29/06/26
Description: |
  Точка входа RemindMe: сборка инфраструктуры данных, инициализация бота, регистрация обработчиков и long polling.
````

#### `.usages/` — `src/remindme/main/.usages/running.md`

````md
# Запуск приложения

Домен: точка входа и запуск бота RemindMe (long polling).
Аудитория: оператор/разработчик, запускающий приложение локально или в Docker, и smoke-тест.

Приложение работает одним процессом: бот и (на этапе 2+) worker уведомлений.

## Запуск

```bash
# внутри venv, с подготовленным .env и применёнными миграциями
python -m remindme.main
```

`main()` собирает движок и фабрику сессий, создаёт `Bot` и `Dispatcher`, регистрирует обработчики, регистрирует меню команд и запускает long polling.

## Предусловия

- Существует `.env` с обязательным `TELEGRAM_BOT_TOKEN` (остальные переменные имеют значения по умолчанию).
- Применены миграции схемы: `alembic upgrade head`.
- Директория `data/` создаётся автоматически при первом подключении.

## Побочные эффекты

- Первый вызов `get_settings()` валидирует конфигурацию; при ошибке (нет токена, неизвестная зона, неверное время) приложение завершается понятной ошибкой без значения токена.
- При запуске регистрируется меню команд `/start`, `/help` (инкрементально; полный список — на этапе 4).
- Бот обрабатывает сообщения только в приватных чатах.
````

---

## Dependency Map

```
src/remindme/config   (leaf, 0 Imports)
   │  Types: Settings
   ▼
src/remindme/db       (Imports.Types: Settings ← config)
   │  Types: User, create_session_factory
   │  Usages: engine, models
   ▼
src/remindme/bot      (Imports ← config [get_settings, settings]; ← db [User, models])
   │  Types: register_handlers (+ PrivateOnly, ensure_user, cmd_start, cmd_help, handle_unknown_command, handle_text, handle_group)
   │  Usages: handlers
   ▼
src/remindme/main     (Imports ← config [get_settings, settings]; ← db [create_engine, create_session_factory, engine]; ← bot [register_handlers, handlers])
```

Ацикличный порядок: `config → db → bot → main`.

**Замечание по `db`:** клетка импортирует только тип `Settings` (без practice `settings`), так как параметр `create_engine(settings: Settings)` конфликтовал бы с одноимённой practice; доступ к `DATABASE_URL` описан через параметр.

---

## Verification Checklist

После реализации каждого артефакта:

**config**
- [ ] `goga lint` для `src/remindme/config/CODEMANIFEST` проходит без ошибок DSL.
- [ ] Фасад доступен: `python -c "from remindme.config import get_settings"` (из venv, с `.env`).
- [ ] `settings.py` usage-файл существует по пути `src/remindme/config/.usages/settings.md`.

**db**
- [ ] `goga lint` для `src/remindme/db/CODEMANIFEST` проходит.
- [ ] Фасад моделей: `python -c "from remindme.db.models import Base, User, Reminder, Note, Todo"`.
- [ ] Фасад сессий: `python -c "from remindme.db.session import create_engine, create_session_factory"`.
- [ ] `Base.metadata` содержит таблицы `users`, `reminders`, `notes`, `todos`.
- [ ] Usage-файлы `engine.md`, `models.md` существуют.

**bot**
- [ ] `goga lint` для `src/remindme/bot/CODEMANIFEST` проходит.
- [ ] Фасад: `python -c "from remindme.bot.handlers import cmd_start, cmd_help, handle_unknown_command, handle_text, handle_group, PrivateOnly, ensure_user"`.
- [ ] Usage-файлы `handlers.md`, `testing.md` существуют.

**main**
- [ ] `goga lint` для `src/remindme/main/CODEMANIFEST` проходит.
- [ ] Фасад: `python -c "from remindme.main import main, set_commands"`.
- [ ] Usage-файл `running.md` существует.

**Глобально (клетки)**
- [ ] `goga lint` (весь проект) проходит без ошибок AST/DSL.
- [ ] `goga schema` показывает 4 клетки с корректными зависимостями (ацикличный граф).

---

## Вспомогательные артефакты (неклеточные, без кода в плане)

Эти артефакты нужны для выполнения критериев приёмки #3 (миграции) и #7 (тесты/линт), но не являются клетками с CODEMANIFEST. Их содержимое определяется ТЗ и существующими usage-файлами (`cooks/alembic.md`, `cooks/pytest-asyncio.md`).

- **`pyproject.toml`** (+ создание venv): зависимости с минимальными версиями (aiogram 3.x, pydantic 2.x, pydantic-settings, SQLAlchemy 2.x, aiosqlite, Alembic); `[project.optional-dependencies].test` (pytest, pytest-asyncio, pytest-cov, ruff); `[tool.pytest.ini_options]` с `asyncio_mode = "auto"`; конфиг ruff. Предусловие запуска всех остальных артефактов.
- **`alembic/`** (`alembic init -t async`): `env.py` с переопределением `sqlalchemy.url` из `get_settings()` и `PRAGMA foreign_keys=ON` в `run_migrations_online`; единая начальная ревизия — 4 таблицы (`users`, `reminders`, `notes`, `todos`) + 4 индекса (`idx_reminders_delivery`, `idx_reminders_user_time`, `idx_notes_user_created`, `idx_todos_user_status_due`) + FK; `downgrade` в обратном порядке. Зависит от `db.models.Base.metadata` и `config.get_settings`.
- **`tests/conftest.py`**: фикстуры `session` (через `tmp_path` + `Base.metadata.create_all`), `fixed_now` (явный момент UTC), mock `bot` (`AsyncMock`). Зависит от `db.models.Base` и `db.session`.

---

## Соответствие критериям приёмки (Этап 1)

| Критерий | Покрытие |
|---|---|
| Старт + `/start` → `users` с `DEFAULT_TIMEZONE`; повторный не дублирует, обновляет `updated_at_utc` | `Settings.DEFAULT_TIMEZONE`; `User` (UNIQUE, `updated_at_utc`); `cmd_start`+`ensure_user` |
| Первое сообщение без `/start` → авто-регистрация; `/help` | `handle_text`→`ensure_user`; `cmd_help` |
| `alembic upgrade head` → 4 таблицы + 4 индекса + FK; `downgrade -1` без ошибок | `db`-модели + `alembic` auxiliary |
| `PRAGMA foreign_keys=ON` + `WAL`; `IntegrityError` без `user_id` | `create_engine`; `Reminder.user_id` FK |
| Нет токена / неизвестная зона / `DEFAULT_REMINDER_TIME=9` → ошибка без токена | `Settings` валидаторы + Constraints |
| Групповой чат → «режим не поддерживается», без записи в БД | `PrivateOnly` (отсекает пишущие обработчики) + `handle_group` (ответ в группе) |
| `ruff check src/` и `pytest tests/ -x` | `conventions` (config) + `conftest` auxiliary |
