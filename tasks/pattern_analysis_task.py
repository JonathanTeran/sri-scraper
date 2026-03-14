"""Celery task for periodic CAPTCHA pattern analysis."""

import structlog
import redis.asyncio as aioredis

from config.settings import get_settings
from db.session import get_session_factory
from scrapers.pattern_analyzer import analyze_and_publish_rules
from tasks.async_runner import run_async
from tasks.celery_app import celery_app

log = structlog.get_logger()


@celery_app.task(name="tasks.analizar_patrones_captcha")
def analizar_patrones_captcha() -> dict:
    """Analyze accumulated knowledge base data and publish auto-rules.

    Runs periodically (default every 6 hours) to discover patterns
    in CAPTCHA blocking and generate actionable rules consumed by
    the adaptive strategy in real time.
    """
    return run_async(_analizar_patrones_async())


async def _analizar_patrones_async() -> dict:
    settings = get_settings()
    async_session = get_session_factory()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)

    try:
        async with async_session() as session:
            async with session.begin():
                rules = await analyze_and_publish_rules(session, r)
                log.info(
                    "pattern_analysis_task_completada",
                    rules_generadas=len(rules),
                )
                return {
                    "rules_count": len(rules),
                    "rules": rules,
                }
    except Exception as exc:
        log.error("pattern_analysis_task_error", error=str(exc))
        return {"error": str(exc)}
    finally:
        await r.aclose()
