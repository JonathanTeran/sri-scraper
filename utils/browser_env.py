"""Utilidades para detectar un ejecutable Chromium/Chrome utilizable."""

from __future__ import annotations

import glob
import os
from pathlib import Path


def _expand_existing_path(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path).expanduser()
    if candidate.exists():
        return str(candidate)
    return None


def _default_browser_patterns() -> list[str]:
    home = Path.home()
    local_app_data = os.environ.get("LOCALAPPDATA", "")

    patterns = [
        str(home / ".cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        str(home / ".cache/ms-playwright/chromium-*/chrome-linux/headless_shell"),
        str(
            home
            / "Library/Caches/ms-playwright/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"
        ),
        str(
            home
            / "Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        ),
        str(
            home
            / "Library/Caches/ms-playwright/chromium-*/chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        ),
        str(
            Path(local_app_data)
            / "ms-playwright/chromium-*/chrome-win/chrome.exe"
        ) if local_app_data else "",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    ]
    return [pattern for pattern in patterns if pattern]


def find_browser_executable(preferred_path: str | None = None) -> str | None:
    """Busca un binario Chrome/Chromium en rutas conocidas."""
    env_candidates = [
        preferred_path,
        os.environ.get("PLAYWRIGHT_BROWSER_PATH"),
        os.environ.get("CHROME_PATH"),
        os.environ.get("GOOGLE_CHROME_SHIM"),
    ]
    for candidate in env_candidates:
        resolved = _expand_existing_path(candidate)
        if resolved:
            return resolved

    for pattern in _default_browser_patterns():
        if "*" in pattern:
            matches = sorted(glob.glob(pattern))
            if matches:
                return matches[0]
            continue
        resolved = _expand_existing_path(pattern)
        if resolved:
            return resolved

    return None
