# Запуск RemindMe

Домен: операционный запуск бота RemindMe (точка входа `main`).
Аудитория: разработчик/оператор, запускающий бота локально или через Docker Compose.

Точка входа `main()` перед long polling применяет миграции схемы (`apply_migrations` → полная схема SQLite), затем
запускает цикл доставки worker как фоновую задачу и long polling в одном event loop. Токен берётся из настроек
(`TELEGRAM_BOT_TOKEN`) и не логируется.

## Предусловия
- Python 3.10+, активированный venv с зависимостями из `pyproject.toml`
- `TELEGRAM_BOT_TOKEN` от BotFather — в `.env` или переменной окружения
- `DATABASE_URL` (по умолчанию `sqlite+aiosqlite:///data/remindme.db`); директория `data/` создаётся автоматически

## Запуск локально (venv)
`python -m remindme.main`
При старте: миграции → регистрация команд → worker → long polling. Проверка: `/start` отвечает приветствием.

## Запуск через Docker Compose
`docker compose up -d` затем `docker compose logs -f`
Compose поднимает сервис бота: применяет миграции, стартует long polling, монтирует том `data/` для SQLite.
Проверка: отправить `/start`.

## Поведение при перезапуске
- База в `data/` (том в compose) — записи сохраняются между перезапусками контейнера.
- Просроченное за время простоя напоминание доставляется после старта: worker recovery возвращает зависшие `sending`,
  а `find_due_reminders` выбирает всё просроченное по `next_attempt_at_utc`.

## Побочные эффекты
- Создаётся/обновляется `data/remindme.db` и WAL-журнал.
- Меню команд бота регистрируется через `set_my_commands` при каждом старте.
