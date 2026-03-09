"""Inyección de dependencias para FastAPI."""

from functools import lru_cache

from config.settings import Settings, get_settings
from db.session import get_db


@lru_cache
def get_settings_dep() -> Settings:
    return get_settings()
