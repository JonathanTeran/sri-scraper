"""Tareas de reporte y notificación post-ejecución."""

import asyncio
import smtplib
from email.mime.text import MIMEText

import structlog

from config.settings import get_settings
from tasks.celery_app import celery_app

log = structlog.get_logger()
settings = get_settings()


@celery_app.task(name="tasks.enviar_reporte")
def enviar_reporte(
    tenant_ruc: str,
    periodo: str,
    total_nuevos: int,
    total_errores: int,
) -> dict:
    """Envía reporte por email después de una ejecución."""
    if not settings.smtp_host or not settings.alert_email:
        return {"status": "skipped", "reason": "SMTP no configurado"}

    asunto = f"SRI Scraper - {tenant_ruc} - {periodo}"
    cuerpo = (
        f"Reporte de ejecución SRI Scraper\n"
        f"================================\n\n"
        f"Tenant RUC: {tenant_ruc}\n"
        f"Período: {periodo}\n"
        f"Nuevos comprobantes: {total_nuevos}\n"
        f"Errores: {total_errores}\n"
    )

    try:
        msg = MIMEText(cuerpo)
        msg["Subject"] = asunto
        msg["From"] = settings.smtp_user
        msg["To"] = settings.alert_email

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_pass:
                server.login(settings.smtp_user, settings.smtp_pass)
            server.send_message(msg)

        log.info("reporte_enviado", email=settings.alert_email)
        return {"status": "sent"}

    except Exception as e:
        log.error("reporte_error", error=str(e))
        return {"status": "error", "error": str(e)}
