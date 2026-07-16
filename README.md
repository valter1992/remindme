# RemindMe

Telegram-бот-напоминалка с заметками и задачами. Работает только в личных
сообщениях; данные хранятся в SQLite (WAL), миграции применяются при старте.

## Возможности

- Напоминания: команда `/remind` и свободная фраза «напомни …»; повторы доставки
  30/120/600 c, эскалация до `failed` на 4-й неудаче.
- Заметки: `/note`, `/notes`.
- Задачи: `/todo` (опциональный срок через `|`), `/todos`, `/completed`.
- Часовой пояс пользователя: `/timezone <IANA>`.
- Inline-кнопки для завершения/удаления записей со списков.

## Установка

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

## Конфигурация

Скопируйте `.env.example` в `.env` и заполните. Переменные окружения имеют
приоритет над `.env`. Минимум — `TELEGRAM_BOT_TOKEN` (получите у @BotFather);
без него бот не стартует (понятная ошибка валидации).

- `TELEGRAM_BOT_TOKEN` — токен бота (обязательный).
- `DATABASE_URL` — подключение к БД, по умолчанию
  `sqlite+aiosqlite:///data/remindme.db`.
- `DEFAULT_TIMEZONE` — IANA-зона новых пользователей, по умолчанию
  `Europe/Moscow`.
- `DEFAULT_REMINDER_TIME` — `ЧЧ:ММ` для «сегодня/завтра» без явного времени, по
  умолчанию `09:00`.
- `REMINDER_POLL_INTERVAL_SECONDS` — интервал опроса worker, `> 0`, по умолчанию
  `5`.
- `LOG_LEVEL` — уровень логирования, по умолчанию `INFO`.

## Команды

```
/remind <когда> | <текст>        — напоминание
напомни <когда> <текст>          — напоминание фразой
/reminders                       — мои напоминания
/cancel <id>                     — отменить напоминание по id
/note <текст>                    — заметка
/notes                           — мои заметки
/todo [<срок>] | <текст>         — задача (срок опционален)
/todos                           — мои задачи
/completed                       — выполненные задачи
/timezone <IANA>                 — сменить часовой пояс
```

Примеры времени: «через 10 минут», «завтра в 09:00», «сегодня 18:00»,
«31.12.2026 в 18:00», «2026-12-31 18:00».

## Запуск

```bash
python -m remindme.main
```

При старте применяются миграции (`alembic upgrade head` in-process), затем в
одном процессе запускаются цикл доставки worker и long polling.

## Запуск через Docker

Заполните `.env`, затем:

```bash
docker compose up -d
docker compose logs -f
```

Том `./data` хранит SQLite (WAL переживает `restart` контейнера). Порты наружу
не выставляются — бот использует long polling и сам инициирует исходящие
соединения к Telegram API.

## Тесты и линтер

```bash
pytest tests/ -x
ruff check src/ tests/
ruff format --check src/ tests/
```

## Деплой на VPS через CI

Сборка и публикация Docker-образа происходят автоматически при пуше в `main`
(GitHub Actions, см. `.github/workflows/ci-cd.yml`). VPS забирает готовый образ
из GitHub Container Registry и обновляется через Watchtower. Секрет
(`TELEGRAM_BOT_TOKEN`) живёт **только на VPS** — CI его не видит и в образ не
кладёт.

### Шаг 1. Первый билд

Пуш в `main` запускает workflow: прогоняются `ruff` и `pytest`, затем образ
собирается и публикуется в `ghcr.io/valter1992/remindme` с тегами `:latest`,
`:sha-<короткий-SHA>` и `:main`.

### Шаг 2. Видимость пакета и PAT

1. Откройте `https://github.com/users/valter1992/packages/container/remindme` →
   **Package settings → Change visibility → Private**. Образ не содержит
   секретов, но приватный доступ уменьшает поверхность атаки.
2. Создайте Personal Access Token (classic) со scope `read:packages` — он нужен
   VPS для `docker pull`: **Settings → Developer settings → Personal access
   tokens → Tokens (classic) → Generate new token**. Срок действия — 90 дней.

### Шаг 3. Настройка VPS

```bash
# 1. Установите Docker (если ещё нет).
# 2. Авторизуйтесь в GHCR (вставьте PAT из шага 2):
docker login ghcr.io -u valter1992 --password-stdin <<< "ВАШ_PAT"
# Теперь ~/./.docker/config.json содержит credentials — watchtower будет
# использовать их для pull приватного образа.

# 3. Каталог приложения:
mkdir -p /opt/remindme/data && cd /opt/remindme

# 4. Положите сюда docker-compose.prod.yml из репозитория.
# 5. Создайте .env:
cat > .env <<'EOF'
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
DATABASE_URL=sqlite+aiosqlite:///data/remindme.db
DEFAULT_TIMEZONE=Europe/Moscow
EOF
chmod 600 .env

# 6. Поднимите стек:
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f bot
```

> Если Docker запущен не от `root`, замените `/root/.docker/config.json` в
> `docker-compose.prod.yml` на абсолютный путь вашего `~/.docker/config.json`.

### Шаг 4. Защита ветки `main` (рекомендуется)

**Settings → Branches → Branch protection rules → `main`:**

- ✅ Require status checks to pass — выберите `Lint & Test`.
- ✅ Require pull request reviews before merging (минимум 1 approval).
- ✅ Require branches to be up to date before merging.

### Шаг 5. Проверка end-to-end

1. Вкладка **Actions** — workflow зелёный (оба job'а).
2. `docker ps` на VPS — контейнеры `remindme` и `watchtower` запущены.
3. `docker compose -f docker-compose.prod.yml logs bot` — логи aiogram
   (polling started), без ошибок `Unauthorized` и миграций.
4. Напишите боту `/start` в Telegram — должен ответить.

### Шаг 6. Откат

```bash
cd /opt/remindme
# Переключитесь на конкретный неизменяемый SHA-тег (из вкладки Packages):
sed -i 's|remindme:latest|remindme:sha-abc1234|' docker-compose.prod.yml
docker compose -f docker-compose.prod.yml up -d
```
