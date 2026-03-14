"""Health checks operativos de la plataforma."""

from __future__ import annotations

import os
import importlib.util
from datetime import datetime, UTC

from sqlalchemy import text

try:
    import redis.asyncio as aioredis
except Exception:  # pragma: no cover - optional in partial envs/tests
    aioredis = None

from config.settings import Settings
from db.session import get_engine
from utils.browser_env import find_browser_executable


async def _check_database() -> dict:
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "detail": "database reachable"}
    except Exception as exc:  # pragma: no cover - depends on external infra
        return {"status": "error", "detail": str(exc)}


async def _check_redis(settings: Settings) -> dict:
    if aioredis is None:
        return {"status": "error", "detail": "redis package not installed"}
    redis = aioredis.from_url(settings.redis_url)
    try:
        await redis.ping()
        return {"status": "ok", "detail": "redis reachable"}
    except Exception as exc:  # pragma: no cover - depends on external infra
        return {"status": "error", "detail": str(exc)}
    finally:
        await redis.aclose()


def _check_storage(settings: Settings) -> dict:
    xml_dir = settings.xml_storage_path
    screenshot_dir = settings.screenshot_path
    paths = {
        "xml_storage_path": xml_dir,
        "screenshot_path": screenshot_dir,
        "browser_profile_path": settings.browser_profile_path,
    }
    missing = [path for path in paths.values() if not os.path.isdir(path)]
    unwritable = [path for path in paths.values() if os.path.isdir(path) and not os.access(path, os.W_OK)]
    status = "ok" if not missing and not unwritable else "error"
    detail_parts = []
    if missing:
        detail_parts.append(f"missing={missing}")
    if unwritable:
        detail_parts.append(f"unwritable={unwritable}")
    return {
        "status": status,
        "detail": ", ".join(detail_parts) if detail_parts else "storage ready",
        **paths,
    }


def _check_captcha(settings: Settings) -> dict:
    configured = settings.configured_captcha_providers()
    if not configured:
        return {
            "status": "error",
            "detail": "no CAPTCHA providers configured",
            "preferred": settings.captcha_provider,
            "configured": configured,
        }
    preferred_ready = settings.captcha_provider in configured
    return {
        "status": "ok" if preferred_ready else "warning",
        "detail": "preferred provider configured" if preferred_ready else "fallback provider only",
        "preferred": settings.captcha_provider,
        "configured": configured,
    }


def _check_browser(settings: Settings) -> dict:
    executable = find_browser_executable(settings.browser_executable_path)
    return {
        "status": "ok" if executable else "warning",
        "detail": executable or "using Playwright managed browser",
        "executable_path": executable,
        "channel": settings.browser_channel or None,
        "profile_path": settings.browser_profile_path,
        "nodriver_installed": importlib.util.find_spec("nodriver") is not None,
        "proxy_configured": bool(settings.browser_proxy_server),
    }


async def _check_adaptive(settings: Settings) -> dict:
    """Return adaptive learning strategy summary."""
    if aioredis is None:
        return {"status": "unavailable"}
    r = aioredis.from_url(settings.redis_url)
    try:
        from scrapers.adaptive_strategy import AdaptiveStrategyTracker
        tracker = AdaptiveStrategyTracker(r)
        return await tracker.get_strategy_summary()
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
    finally:
        await r.aclose()


async def _check_knowledge_base() -> dict:
    """Return persistent knowledge base summary."""
    try:
        from scrapers.knowledge_base import SRIKnowledgeBase
        from db.session import get_session_factory
        async with get_session_factory()() as session:
            kb = SRIKnowledgeBase(session)
            return await kb.get_full_summary()
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


async def build_health_report(settings: Settings) -> dict:
    checks = {
        "database": await _check_database(),
        "redis": await _check_redis(settings),
        "storage": _check_storage(settings),
        "captcha": _check_captcha(settings),
        "browser": _check_browser(settings),
        "adaptive": await _check_adaptive(settings),
        "knowledge_base": await _check_knowledge_base(),
    }
    critical_checks = ("database", "redis", "storage", "captcha")
    overall = "ok"
    for key in critical_checks:
        if checks[key]["status"] == "error":
            overall = "degraded"
            break

    return {
        "status": overall,
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": checks,
    }
