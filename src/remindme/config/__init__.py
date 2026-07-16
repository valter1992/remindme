"""Клетка config — настройки приложения RemindMe.

Фасадный модуль клетки экспонирует собственные сущности: ``Settings``
(pydantic-settings, валидаторы) и ``get_settings`` (ленивый синглтон).
"""

from .config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
