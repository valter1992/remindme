# Deployment — упаковка и запуск RemindMe в Docker

Домен: упаковка приложения RemindMe в образ и запуск через Docker Compose.
Аудитория: сопровождение Dockerfile/docker-compose.yml и CI.

RemindMe — однопроцессное приложение. Образ содержит код и зависимости; секреты и база данных пробрасываются снаружи.

## Dockerfile
- Базовый образ Python 3.10+ slim; зависимости из `pyproject.toml` в venv.
- Копируются `src/`, `alembic/`, `alembic.ini`, `pyproject.toml`.
- Рабочая директория — корень проекта; точка входа `python -m remindme.main` (модуль `main` сам применяет миграции через `apply_migrations`).
- `data/` НЕ создаётся в образе — монтируется томом; `.env` НЕ копируется в образ (`COPY .env` запрещён).

## docker-compose.yml
- Один сервис бота; `build: .`; `restart: unless-stopped`.
- Том для `data/` (SQLite + WAL переживает перезапуск контейнера).
- `TELEGRAM_BOT_TOKEN` и настройки — через `env_file: .env` и/или `environment:`; никогда не вносятся в образ.
- Портов наружу не выставляется (long polling; исходящие соединения к Telegram API).

## Порядок запуска (миграции → бот)
Точка входа `main()` сначала вызывает `apply_migrations()` (`alembic upgrade head`), затем запускает worker и long polling.
Отдельный shell-entrypoint для миграций не требуется — миграции применяются в том же процессе.

## Безопасность
- `TELEGRAM_BOT_TOKEN` хранится как `SecretStr`; в логи и тексты ошибок не попадает.
- `.env` и `data/` в `.gitignore`; образ собирается без секретов и без данных.

## Проверка
- `docker compose up` → бот отвечает на `/start`.
- `docker compose restart` → записи сохранены, просроченное напоминание доставляется.
