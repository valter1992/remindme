# syntax=docker/dockerfile:1
# RemindMe — однопроцессный образ бота.
#
# В образ попадают только код и зависимости. Секреты (``.env``) и данные
# (``data/``) пробрасываются снаружи (см. ``.dockerignore`` и
# ``docker-compose.yml``) и никогда не вносятся в образ. Миграции применяются
# в том же процессе — ``main()`` сначала вызывает ``apply_migrations()``
# (``alembic upgrade head``), затем запускает worker и long polling.

FROM python:3.12-slim

# Не писать ``.pyc`` и не буферизовать stdout/stderr — логи видны сразу.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Установка пакета и зависимостей из ``pyproject.toml``.
# ``README.md`` нужен ``setuptools`` (``readme`` в ``pyproject.toml``).
COPY pyproject.toml README.md alembic.ini ./
COPY src/ ./src/
COPY alembic/ ./alembic/

RUN pip install .

# Точка входа: ``apply_migrations`` → worker ∥ polling.
# ``data/`` НЕ создаётся в образе — монтируется томом; родительский каталог
# файла БД создаётся ``create_engine`` при первом подключении.
CMD ["python", "-m", "remindme.main"]
