"""
Scheduler de jobs con APScheduler + Celery Beat.

La ejecución diaria se programa a las 06:30 hora Ecuador (UTC-5)
para evitar el horario de mantenimiento nocturno del SRI (00:00-06:00).
"""

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import get_settings
from tasks.scrape_tasks import scrape_todos_tenants

log = structlog.get_logger()
settings = get_settings()


def crear_scheduler() -> BackgroundScheduler:
    """Crea y configura el scheduler."""
    scheduler = BackgroundScheduler(timezone="America/Guayaquil")

    scheduler.add_job(
        func=_trigger_scrape_diario,
        trigger=CronTrigger(
            hour=settings.schedule_hour,
            minute=settings.schedule_minute,
            timezone="America/Guayaquil",
        ),
        id="scrape_diario",
        name="Scraping diario de todos los tenants",
        replace_existing=True,
    )

    log.info(
        "scheduler_configurado",
        hora=f"{settings.schedule_hour}:{settings.schedule_minute:02d}",
        timezone="America/Guayaquil",
    )
    return scheduler


def _trigger_scrape_diario() -> None:
    """Dispara la tarea Celery de scraping diario."""
    log.info("scheduler_trigger_scrape_diario")
    scrape_todos_tenants.delay()


def iniciar_scheduler() -> BackgroundScheduler:
    """Crea, inicia y retorna el scheduler."""
    scheduler = crear_scheduler()
    scheduler.start()
    log.info("scheduler_iniciado")
    return scheduler
