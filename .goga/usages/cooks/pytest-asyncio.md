# pytest-asyncio — асинхронное тестирование

Домен: запуск и организация асинхронных тестов RemindMe.
Аудитория: авторы тестов в `tests/unit/` и `tests/integration/`.

Тесты покрывают парсер, работу со временем, formatter, сервисы сущностей, репозитории SQLite и worker уведомлений. Telegram Bot API подменяется mock-клиентом, кроме ручного end-to-end smoke-сценария. Текущий момент времени передаётся явно; реальные `sleep` не используются.

## Режим auto

В `pyproject.toml` включается автоопределение асинхронных тестов, чтобы не украшать каждый тест декоратором.

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

## Временная база данных

Файл SQLite создаётся во временной директории и удаляется вместе с ней.

```python
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

@pytest.fixture
async def session(tmp_path) -> AsyncSession:
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()
```

## Параметризация границ парсера

Для порогов и диапазонов используется таблица значений, включающая каждую границу: формы единиц, `|` и без него, прошлое и ровно `now`, горизонт 365 дней.

```python
@pytest.mark.parametrize("phrase, expected_minutes", [
    ("через 1 минуту проверить", 1),
    ("через 15 минут проверить", 15),
    ("через 2 часа | сделать перерыв", 120),
    ("через 3 дня оплатить", 3 * 24 * 60),
])
async def test_parse_relative(phrase, expected_minutes, fixed_now, tz):
    moment = parse_remind_time(phrase, now=fixed_now, timezone=tz, default_time="09:00")
    assert moment == fixed_now.astimezone(timezone.utc) + timedelta(minutes=expected_minutes)
```

## Явное «сейчас»

Текущий момент фиксируется в фикстуре и передаётся в парсер и сервисы аргументом; внутри логики `datetime.now()` не вызывается.

```python
@pytest.fixture
def fixed_now():
    return datetime(2026, 6, 24, 12, 0, tzinfo=ZoneInfo("UTC"))
```

## Mock Telegram API

В тестах handlers и worker клиент aiogram подменяется mock-объектом; проверяются аргументы вызовов `send_message`, `edit_message_text`, `answer_callback_query`.

```python
from unittest.mock import AsyncMock

@pytest.fixture
def bot():
    return AsyncMock()
```

## Маркировка интеграционных тестов

Тесты, обращающиеся к БД или worker, выносятся в `tests/integration/` и при необходимости пропускаются при недоступности окружения через `pytest.mark.skipif`.

## Запуск

```bash
pytest tests/ -x
pytest tests/unit/test_parser.py -v
```

Покрытие собирается через `pytest-cov`; выполнение — внутри виртуального окружения.
