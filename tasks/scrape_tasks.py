"""
Tareas Celery para scraping del SRI.

Manejo de errores diferenciado por tipo de excepción SRI.
"""

import asyncio
import os
import uuid
from datetime import datetime

import structlog
from celery import group
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config.settings import get_settings
from db.session import get_session_factory
from db.models.comprobante import Comprobante, TipoComprobante
from db.models.detalle import DetalleComprobante
from db.models.ejecucion_log import EjecucionLog, EstadoEjecucion
from db.models.pago import Pago
from db.models.retencion import ImpuestoRetencion
from db.models.tenant import Tenant
from parsers.xml_parser import parse_comprobante_sri, comprobante_to_dict
from scrapers.engine import SRIScraperEngine
from scrapers.exceptions import (
    SRICaptchaError,
    SRILoginError,
    SRIMaintenanceError,
    SRITimeoutError,
)
from tasks.celery_app import celery_app
from tasks.constants import TIPOS_SCRAPING, TIPO_MAP
from utils.crypto import decrypt
from utils.time import utc_now, utc_today
from utils.xml_storage import build_xml_storage_path

log = structlog.get_logger()

settings = get_settings()


def _run_async(coro):
    """Ejecuta una corrutina desde contexto sync de Celery."""
    return asyncio.run(coro)


def _get_async_session() -> async_sessionmaker[AsyncSession]:
    return get_session_factory()


async def _actualizar_ejecucion_estado(
    async_session: async_sessionmaker[AsyncSession],
    log_id: uuid.UUID,
    *,
    estado: EstadoEjecucion,
    error_mensaje: str | None = None,
    finished_at: datetime | None = None,
) -> None:
    """Actualiza el estado de una ejecución existente."""
    async with async_session() as session:
        result = await session.execute(
            select(EjecucionLog).where(EjecucionLog.id == log_id)
        )
        ej_log = result.scalar_one_or_none()
        if not ej_log:
            return

        ej_log.estado = estado
        ej_log.error_mensaje = (
            error_mensaje[:500] if error_mensaje else None
        )
        ej_log.finished_at = finished_at
        await session.commit()


async def _guardar_comprobante(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    tenant_ruc: str,
    periodo_anio: int,
    periodo_mes: int,
    xml_bytes: bytes,
    xml_raw_str: str,
    clave_hint: str | None = None,
) -> bool:
    """Parsea y guarda un comprobante en BD. Retorna True si es nuevo."""
    try:
        comp = parse_comprobante_sri(xml_bytes)
        data = comprobante_to_dict(comp)
    except Exception as e:
        # Guardar con parse_error
        log.error("parse_error", error=str(e))
        comp_obj = Comprobante(
            tenant_id=tenant_id,
            estado_autorizacion="DESCONOCIDO",
            numero_autorizacion="",
            ambiente_autorizacion="",
            ruc_emisor="",
            razon_social_emisor="",
            cod_doc="",
            tipo_comprobante=TipoComprobante.FACTURA,
            clave_acceso=clave_hint or str(uuid.uuid4())[:49],
            estab="",
            pto_emi="",
            secuencial="",
            serie="",
            numero_completo="",
            fecha_emision=utc_today(),
            xml_raw=xml_raw_str,
            parse_error=True,
            parse_error_msg=str(e)[:500],
        )
        session.add(comp_obj)
        return True

    # Verificar si ya existe
    clave = data["clave_acceso"]
    existing = await session.execute(
        select(Comprobante.id).where(
            Comprobante.tenant_id == tenant_id,
            Comprobante.clave_acceso == clave,
        )
    )
    if existing.scalar_one_or_none():
        return False

    # Crear comprobante
    comp_obj = Comprobante(
        tenant_id=tenant_id,
        **data,
    )
    session.add(comp_obj)
    await session.flush()

    # Guardar detalles
    for det in comp.detalles:
        session.add(DetalleComprobante(
            comprobante_id=comp_obj.id,
            tenant_id=tenant_id,
            orden=det.orden,
            codigo_principal=det.codigo_principal or None,
            codigo_auxiliar=det.codigo_auxiliar or None,
            descripcion=det.descripcion,
            cantidad=det.cantidad,
            precio_unitario=det.precio_unitario,
            descuento=det.descuento,
            precio_total_sin_impuesto=det.precio_total_sin_impuesto,
            iva_codigo=det.iva_codigo or None,
            iva_codigo_porcentaje=det.iva_codigo_porcentaje or None,
            iva_tarifa=det.iva_tarifa,
            iva_base_imponible=det.iva_base_imponible,
            iva_valor=det.iva_valor,
        ))

    # Guardar retenciones
    for ret in comp.retenciones:
        session.add(ImpuestoRetencion(
            comprobante_id=comp_obj.id,
            tenant_id=tenant_id,
            codigo=ret.codigo,
            tipo_tributo=ret.tipo_tributo,
            codigo_retencion=ret.codigo_retencion,
            base_imponible=ret.base_imponible,
            porcentaje_retener=ret.porcentaje_retener,
            valor_retenido=ret.valor_retenido,
            cod_doc_sustento=ret.cod_doc_sustento or None,
            num_doc_sustento=ret.num_doc_sustento or None,
            fecha_emision_doc_sustento=ret.fecha_emision_doc_sustento or None,
        ))

    # Guardar pagos
    for pag in comp.pagos:
        session.add(Pago(
            comprobante_id=comp_obj.id,
            tenant_id=tenant_id,
            forma_pago=pag.forma_pago,
            forma_pago_desc=pag.forma_pago_desc,
            total=pag.total,
            plazo=pag.plazo or None,
            unidad_tiempo=pag.unidad_tiempo or None,
        ))

    # Guardar XML en filesystem
    xml_path = build_xml_storage_path(
        settings.xml_storage_path,
        tenant_ruc,
        periodo_anio,
        periodo_mes,
        clave,
    )
    os.makedirs(xml_path.parent, exist_ok=True)
    with open(xml_path, "wb") as f:
        f.write(xml_bytes)

    comp_obj.xml_path = str(xml_path)
    return True


async def _comprobante_ya_guardado(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    clave_acceso: str,
) -> bool:
    result = await session.execute(
        select(Comprobante.id).where(
            Comprobante.tenant_id == tenant_id,
            Comprobante.clave_acceso == clave_acceso,
        )
    )
    return result.scalar_one_or_none() is not None


@celery_app.task(
    bind=True,
    name="tasks.scrape_tenant_periodo",
    max_retries=5,
)
def scrape_tenant_periodo(
    self,
    tenant_id: str,
    anio: int,
    mes: int,
    tipo_comprobante: str,
    log_id: str | None = None,
) -> dict:
    """Tarea principal de scraping para un tenant/período/tipo."""
    return _run_async(
        _scrape_tenant_periodo_async(
            self, tenant_id, anio, mes, tipo_comprobante, log_id
        )
    )


async def _scrape_tenant_periodo_async(
    task,
    tenant_id: str,
    anio: int,
    mes: int,
    tipo_comprobante: str,
    log_id: str | None,
) -> dict:
    """Implementación async de la tarea de scraping."""
    async_session = _get_async_session()

    async with async_session() as session:
        # Cargar tenant
        result = await session.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            return {"error": f"Tenant {tenant_id} no encontrado"}
        tenant_uuid = tenant.id
        tenant_ruc = tenant.ruc

        # Desencriptar credenciales
        usuario = decrypt(tenant.sri_usuario_enc, settings.secret_key)
        password = decrypt(tenant.sri_password_enc, settings.secret_key)

        # Crear o recargar EjecucionLog
        if log_id:
            result = await session.execute(
                select(EjecucionLog).where(
                    EjecucionLog.id == uuid.UUID(log_id)
                )
            )
            ej_log = result.scalar_one_or_none()
        else:
            ej_log = None

        if not ej_log:
            ej_log = EjecucionLog(
                tenant_id=tenant_uuid,
                periodo_anio=anio,
                periodo_mes=mes,
                tipo_comprobante=TIPO_MAP.get(tipo_comprobante, tipo_comprobante),
                estado=EstadoEjecucion.INICIADO,
            )
            session.add(ej_log)
            await session.flush()
            ej_log.total_encontrados = 0
            ej_log.total_nuevos = 0
            ej_log.total_errores = 0

        ej_log_id = ej_log.id
        pagina_inicio = ej_log.pagina_actual
        ej_log.estado = EstadoEjecucion.EN_PROGRESO
        ej_log.error_mensaje = None
        ej_log.finished_at = None
        ej_log.duracion_seg = None
        await session.commit()

    # Ejecutar scraper
    try:
        claves_cache: set[str] = set()

        async def _should_skip_download(clave_acceso: str) -> bool:
            if not clave_acceso:
                return False
            if clave_acceso in claves_cache:
                return True

            async with async_session() as session:
                exists = await _comprobante_ya_guardado(
                    session,
                    tenant_uuid,
                    clave_acceso,
                )
            if exists:
                claves_cache.add(clave_acceso)
            return exists

        async def _persistir_pagina(
            pagina: int,
            filas: list[dict],
        ) -> None:
            nuevos = 0
            errores = 0

            async with async_session() as session:
                async with session.begin():
                    result = await session.execute(
                        select(EjecucionLog).where(
                            EjecucionLog.id == ej_log_id
                        )
                    )
                    progress_log = result.scalar_one()

                    for fila in filas:
                        clave_acceso = fila.get("clave_acceso")
                        if clave_acceso:
                            claves_cache.add(clave_acceso)

                        if fila.get("omitido_existente"):
                            continue

                        xml_bytes = fila.get("xml_bytes")
                        if not xml_bytes:
                            errores += 1
                            continue

                        es_nuevo = await _guardar_comprobante(
                            session,
                            tenant_uuid,
                            tenant_ruc,
                            anio,
                            mes,
                            xml_bytes,
                            xml_bytes.decode("utf-8", errors="replace"),
                            clave_hint=clave_acceso,
                        )
                        if es_nuevo:
                            nuevos += 1

                    progress_log.pagina_actual = pagina + 1
                    progress_log.total_nuevos += nuevos
                    progress_log.total_errores += errores

        engine = SRIScraperEngine(
            tenant_ruc=tenant_ruc,
            tenant_usuario=usuario,
            tenant_password=password,
            periodo_anio=anio,
            periodo_mes=mes,
            tipo_comprobante=tipo_comprobante,
            settings=settings,
            pagina_inicio=pagina_inicio,
            on_page_processed=_persistir_pagina,
            should_skip_download=_should_skip_download,
            collect_results=False,
        )
        resultado = await engine.ejecutar()

        async with async_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(EjecucionLog).where(EjecucionLog.id == ej_log_id)
                )
                ej_log = result.scalar_one()
                ej_log.estado = EstadoEjecucion.COMPLETADO
                ej_log.total_encontrados = resultado.total_encontrados
                ej_log.duracion_seg = resultado.duracion_seg
                ej_log.finished_at = utc_now()
                ej_log.pagina_actual = resultado.pagina_final + 1

            total_nuevos = ej_log.total_nuevos
            total_errores = ej_log.total_errores

        return {
            "tenant_id": tenant_id,
            "periodo": f"{anio}-{mes:02d}",
            "tipo": tipo_comprobante,
            "total_encontrados": resultado.total_encontrados,
            "total_nuevos": total_nuevos,
            "total_errores": total_errores,
        }

    except SRILoginError as e:
        await _actualizar_ejecucion_estado(
            async_session,
            ej_log_id,
            estado=EstadoEjecucion.ERROR,
            error_mensaje=str(e),
            finished_at=utc_now(),
        )
        raise  # NO retry

    except SRICaptchaError as e:
        await _actualizar_ejecucion_estado(
            async_session,
            ej_log_id,
            estado=EstadoEjecucion.CAPTCHA_BLOQUEADO,
            error_mensaje=str(e),
            finished_at=utc_now(),
        )
        raise task.retry(
            exc=e,
            kwargs={"log_id": str(ej_log_id)},
            countdown=300,
        )

    except SRIMaintenanceError as e:
        await _actualizar_ejecucion_estado(
            async_session,
            ej_log_id,
            estado=EstadoEjecucion.SRI_MANTENIMIENTO,
            error_mensaje=str(e),
            finished_at=utc_now(),
        )
        raise task.retry(
            exc=e,
            kwargs={"log_id": str(ej_log_id)},
            countdown=1800,
        )  # 30 min

    except SRITimeoutError as e:
        await _actualizar_ejecucion_estado(
            async_session,
            ej_log_id,
            estado=EstadoEjecucion.ERROR,
            error_mensaje=str(e),
            finished_at=utc_now(),
        )
        raise task.retry(
            exc=e,
            kwargs={"log_id": str(ej_log_id)},
            countdown=60 * (2 ** task.request.retries),
        )

    except Exception as e:
        await _actualizar_ejecucion_estado(
            async_session,
            ej_log_id,
            estado=EstadoEjecucion.ERROR,
            error_mensaje=str(e),
            finished_at=utc_now(),
        )
        raise task.retry(
            exc=e,
            kwargs={"log_id": str(ej_log_id)},
            countdown=120,
        )


@celery_app.task(name="tasks.scrape_todos_tenants")
def scrape_todos_tenants(
    anio: int | None = None, mes: int | None = None
) -> dict:
    """Orquestador diario: lanza scraping para todos los tenants activos."""
    return _run_async(
        _scrape_todos_async(anio, mes)
    )


async def _scrape_todos_async(
    anio: int | None, mes: int | None
) -> dict:
    now = utc_now()
    if anio is None:
        anio = now.year
    if mes is None:
        mes = now.month

    # Periodos: mes actual y mes anterior
    periodos = [(anio, mes)]
    if mes == 1:
        periodos.append((anio - 1, 12))
    else:
        periodos.append((anio, mes - 1))

    async_session = _get_async_session()
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.activo == True)  # noqa: E712
        )
        tenants = result.scalars().all()

    if not tenants:
        return {"message": "No hay tenants activos"}

    # Crear grupo de tareas
    tasks = []
    for tenant in tenants:
        for p_anio, p_mes in periodos:
            for tipo in TIPOS_SCRAPING:
                tasks.append(
                    scrape_tenant_periodo.s(
                        str(tenant.id), p_anio, p_mes, tipo
                    )
                )

    # Ejecutar en grupos limitados
    batch_size = settings.max_concurrent_tenants
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i : i + batch_size]
        job = group(batch)
        job.apply_async()

    return {
        "tenants": len(tenants),
        "periodos": periodos,
        "tareas_encoladas": len(tasks),
    }


@celery_app.task(name="tasks.verificar_circuit_breaker")
def verificar_circuit_breaker() -> dict:
    """Verifica el estado del circuit breaker."""
    return _run_async(
        _verificar_cb_async()
    )


async def _verificar_cb_async() -> dict:
    import redis.asyncio as aioredis

    r = aioredis.from_url(settings.redis_url)

    try:
        failures = await r.get("sri:circuit:failures")
        failures = int(failures) if failures else 0
        state = await r.get("sri:circuit:state")
        state = state.decode() if state else "closed"

        if failures >= settings.circuit_breaker_threshold and state != "open":
            await r.setex(
                "sri:circuit:state",
                settings.circuit_breaker_timeout_min * 60,
                "open",
            )
            log.critical(
                "circuit_breaker_activado", failures=failures
            )
            return {"state": "open", "failures": failures}

        return {"state": state, "failures": failures}
    finally:
        await r.aclose()
