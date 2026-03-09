"""Preparacion de directorios de runtime."""

from __future__ import annotations

from pathlib import Path

from config.settings import Settings


def ensure_runtime_directories(settings: Settings) -> list[str]:
    paths = [
        settings.xml_storage_path,
        settings.screenshot_path,
        settings.browser_profile_path,
        "sessions",
    ]
    created: list[str] = []
    for path in paths:
        resolved = Path(path)
        resolved.mkdir(parents=True, exist_ok=True)
        created.append(str(resolved))
    return created
