"""Точка входа ``python -m remindme.main``: применяет миграции и запускает бота.

Запускает асинхронную :func:`~remindme.main.main.main` в свежем event loop через
:func:`asyncio.run`. Миграции применяются внутри ``main()`` до polling.
"""

import asyncio

from .main import main

if __name__ == "__main__":
    asyncio.run(main())
