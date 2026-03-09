"""Helpers de tiempo en UTC sin usar datetime.utcnow()."""

from __future__ import annotations

from datetime import UTC, date, datetime


def utc_now() -> datetime:
    """Retorna datetime naive en UTC para compatibilidad con el modelo actual."""
    return datetime.now(UTC).replace(tzinfo=None)


def utc_today() -> date:
    return utc_now().date()
