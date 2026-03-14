"""Factory de Celery con configuración Redis."""

from celery import Celery
from celery.schedules import crontab

from config.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "sri_scraper",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
    include=[
        "tasks.scrape_tasks",
        "tasks.report_tasks",
        "tasks.pattern_analysis_task",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Guayaquil",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
    broker_connection_retry_on_startup=True,
)

# Celery Beat schedule
celery_app.conf.beat_schedule = {
    "scrape-diario": {
        "task": "tasks.scrape_todos_tenants",
        "schedule": crontab(
            hour=settings.schedule_hour,
            minute=settings.schedule_minute,
        ),
    },
    "verificar-circuit-breaker": {
        "task": "tasks.verificar_circuit_breaker",
        "schedule": crontab(minute="*/5"),
    },
}

if settings.pattern_analysis_enabled:
    celery_app.conf.beat_schedule["analizar-patrones"] = {
        "task": "tasks.analizar_patrones_captcha",
        "schedule": crontab(
            minute="0",
            hour=f"*/{settings.pattern_analysis_interval_hours}",
        ),
    }
