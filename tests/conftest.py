"""Fixtures compartidas para tests."""

import os
import sys

import pytest

# Agregar raíz del proyecto al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setear variables de entorno para tests
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://sri:test@localhost:5432/sri_scraper_test",
)
os.environ.setdefault("SECRET_KEY", "test_secret_key_for_testing_only_32chars")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("TWOCAPTCHA_API_KEY", "")
os.environ.setdefault("PLAYWRIGHT_HEADLESS", "true")
