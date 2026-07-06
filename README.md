# RemindMe

Telegram-бот-напоминалка с заметками и задачами.

## Установка

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

## Запуск

```bash
python -m remindme.main
```

## Тесты и линтер

```bash
pytest tests/ -x
ruff check src/ tests/
ruff format --check src/ tests/
```
