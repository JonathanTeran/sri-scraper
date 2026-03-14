"""
Tareas Celery para scraping del SRI.

Manejo de errores diferenciado por tipo de excepción SRI.
"""

import os
import uuid
from datetime import datetime
from datetime import timezone

import structlog
from celery import group
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from config.settings import get_settings
from db.session import get_session_factory
from db.models.comprobante import Comprobante, TipoComprobante
from db.models.detalle import DetalleComprobante
from db.models.ejecucion_log import EjecucionLog, EstadoEjecucion
from db.models.pago import Pago
from db.models.retencion import ImpuestoRetencion
from db.models.tenant import Tenant
from parsers.xml_parser import parse_comprobante_sri, comprobante_to_dict
import redis.asyncio as aioredis

from scrapers.adaptive_strategy import AdaptiveStrategyTracker
from scrapers.engine import SRIScraperEngine
from scrapers.knowledge_base import SRIKnowledgeBase
from scrapers.nodriver_engine import SRINodriverEngine
from scrapers.pattern_analyzer import get_active_rules
from scrapers.proxy_pool import ProxyPool
from scrapers.exceptions import (
    SRIBaseError,
    SRICaptchaError,
    SRILoginError,
    SRIMaintenanceError,
    SRITimeoutError,
)
from db.models.knowledge_base import PatternCategory
from tasks.async_runner import run_async
from tasks.celery_app import celery_app
from tasks.constants import (
    TIPOS_SCRAPING,
    TIPO_MAP,
    normalize_tipo_comprobante,
)
from utils.crypto import decrypt
from utils.time import utc_now, utc_today
from utils.xml_storage import build_xml_storage_path

log = structlog.get_logger()

settings = get_settings()

TIPOS_CON_DETALLES = {
    TipoComprobante.FACTURA.value,
    TipoComprobante.LIQUIDACION.value,
    TipoComprobante.NOTA_CREDITO.value,
    TipoComprobante.NOTA_DEBITO.value,
    TipoComprobante.GUIA_REMISION.value,
}


def _run_async(coro):
    """Ejecuta una corrutina desde contexto sync de Celery."""
    return run_async(coro)


def _get_async_session() -> async_sessionmaker[AsyncSession]:
    return get_session_factory()


def _tipo_value(tipo: TipoComprobante | str | None) -> str:
    if isinstance(tipo, TipoComprobante):
        return tipo.value
    return str(tipo or "").strip().lower()


def _normalize_db_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


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
) -> str:
    """
    Parsea y guarda un comprobante completo en BD.

    Retorna:
        - "created": se creó un comprobante nuevo
        - "updated": se reparó/actualizó un comprobante existente
        - "parse_error": el XML no se pudo parsear
    """
    try:
        comp = parse_comprobante_sri(xml_bytes)
        data = comprobante_to_dict(comp)
        data["fecha_autorizacion"] = _normalize_db_datetime(
            data.get("fecha_autorizacion")
        )
    except Exception as e:
        log.error("parse_error", error=str(e), clave_hint=clave_hint or "")
        clave_fallback = (clave_hint or str(uuid.uuid4()))[:49]
        existing_result = await session.execute(
            select(Comprobante).where(
                Comprobante.tenant_id == tenant_id,
                Comprobante.clave_acceso == clave_fallback,
            )
        )
        comp_obj = existing_result.scalar_one_or_none()
        if comp_obj is None:
            comp_obj = Comprobante(
                tenant_id=tenant_id,
                estado_autorizacion="DESCONOCIDO",
                numero_autorizacion="",
                ambiente_autorizacion="",
                ruc_emisor="",
                razon_social_emisor="",
                cod_doc="",
                tipo_comprobante=TipoComprobante.FACTURA,
                clave_acceso=clave_fallback,
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
        elif comp_obj.parse_error:
            comp_obj.xml_raw = xml_raw_str
            comp_obj.parse_error = True
            comp_obj.parse_error_msg = str(e)[:500]
        return "parse_error"

    clave = data["clave_acceso"]
    existing_result = await session.execute(
        select(Comprobante)
        .where(
            Comprobante.tenant_id == tenant_id,
            Comprobante.clave_acceso == clave,
        )
        .options(
            selectinload(Comprobante.detalles),
            selectinload(Comprobante.retenciones),
            selectinload(Comprobante.pagos),
        )
    )
    comp_obj = existing_result.scalar_one_or_none()
    es_nuevo = comp_obj is None

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

    if es_nuevo:
        comp_obj = Comprobante(
            tenant_id=tenant_id,
            **data,
        )
        session.add(comp_obj)
        await session.flush()
    else:
        for key, value in data.items():
            setattr(comp_obj, key, value)
        comp_obj.parse_error = False
        comp_obj.parse_error_msg = None
        comp_obj.detalles.clear()
        comp_obj.retenciones.clear()
        comp_obj.pagos.clear()
        await session.flush()

    for det in comp.detalles:
        comp_obj.detalles.append(DetalleComprobante(
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

    for ret in comp.retenciones:
        comp_obj.retenciones.append(ImpuestoRetencion(
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

    for pag in comp.pagos:
        comp_obj.pagos.append(Pago(
            tenant_id=tenant_id,
            forma_pago=pag.forma_pago,
            forma_pago_desc=pag.forma_pago_desc,
            total=pag.total,
            plazo=pag.plazo or None,
            unidad_tiempo=pag.unidad_tiempo or None,
        ))

    comp_obj.xml_raw = xml_raw_str
    comp_obj.xml_path = str(xml_path)
    comp_obj.parse_error = False
    comp_obj.parse_error_msg = None
    return "created" if es_nuevo else "updated"


async def _comprobante_guardado_completo(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    clave_acceso: str,
) -> bool:
    result = await session.execute(
        select(Comprobante)
        .where(
            Comprobante.tenant_id == tenant_id,
            Comprobante.clave_acceso == clave_acceso,
        )
        .options(
            selectinload(Comprobante.detalles),
            selectinload(Comprobante.retenciones),
            selectinload(Comprobante.pagos),
        )
    )
    comp = result.scalar_one_or_none()
    if comp is None:
        return False
    if comp.parse_error or not comp.xml_raw or not comp.xml_path:
        return False

    tipo = _tipo_value(comp.tipo_comprobante)
    if tipo in TIPOS_CON_DETALLES and len(comp.detalles) == 0:
        return False
    if tipo == TipoComprobante.RETENCION.value and len(comp.retenciones) == 0:
        return False
    return True


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
    tipo_canonico = normalize_tipo_comprobante(tipo_comprobante)

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
                tipo_comprobante=TIPO_MAP.get(tipo_canonico, tipo_canonico),
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
                exists = await _comprobante_guardado_completo(
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

                        estado_guardado = await _guardar_comprobante(
                            session,
                            tenant_uuid,
                            tenant_ruc,
                            anio,
                            mes,
                            xml_bytes,
                            xml_bytes.decode("utf-8", errors="replace"),
                            clave_hint=clave_acceso,
                        )
                        if estado_guardado == "created":
                            nuevos += 1
                        elif estado_guardado == "parse_error":
                            errores += 1

                    progress_log.pagina_actual = pagina + 1
                    progress_log.total_nuevos += nuevos
                    progress_log.total_errores += errores

        engine_kwargs = dict(
            tenant_ruc=tenant_ruc,
            tenant_usuario=usuario,
            tenant_password=password,
            periodo_anio=anio,
            periodo_mes=mes,
            tipo_comprobante=tipo_canonico,
            settings=settings,
            pagina_inicio=pagina_inicio,
            on_page_processed=_persistir_pagina,
            should_skip_download=_should_skip_download,
            collect_results=False,
        )

        # ── Adaptive engine selection ──────────────────────────────────
        r = aioredis.from_url(settings.redis_url)
        tracker = AdaptiveStrategyTracker(
            r,
            kb_session_factory=async_session,
            stats_ttl=settings.adaptive_stats_ttl_days * 86400,
            block_ttl=settings.adaptive_block_ttl_hours * 3600,
        )
        try:
            # Load auto-generated rules from pattern analysis
            auto_rules = []
            if settings.pattern_analysis_enabled:
                try:
                    auto_rules = await get_active_rules(r)
                    if auto_rules:
                        log.info("auto_rules_cargadas", count=len(auto_rules))
                except Exception as rules_exc:
                    log.debug("auto_rules_load_error", error=str(rules_exc))

            # Smart proxy rotation
            proxy_pool = None
            if settings.proxy_rotation and settings.proxy_pool_urls:
                proxy_pool = ProxyPool.from_config(r, settings.proxy_pool_urls)
                best_proxy = await proxy_pool.get_best_proxy()
                if best_proxy:
                    engine_kwargs["settings"] = settings.model_copy(
                        update={
                            "browser_proxy_server": best_proxy.server,
                            "browser_proxy_username": best_proxy.username,
                            "browser_proxy_password": best_proxy.password,
                        }
                    )
                    log.info(
                        "proxy_rotacion_aplicada",
                        proxy=best_proxy.server,
                        label=best_proxy.label,
                    )

            # Let the tracker decide based on historical success
            default_engine = "nodriver" if settings.browser_prefer_nodriver else "playwright"
            best_engine = await tracker.get_best_engine(default=default_engine)

            # Check cooldown — if best engine is blocked too much, switch
            in_cooldown, cooldown_sec = await tracker.should_cooldown_engine(best_engine)
            if in_cooldown:
                log.warning(
                    "motor_en_cooldown_adaptativo",
                    motor=best_engine,
                    cooldown_sec=cooldown_sec,
                )
                best_engine = "playwright" if best_engine == "nodriver" else "nodriver"

            # Apply auto-rules: global delay adjustments
            rules_delay_mult = 1.0
            for rule in auto_rules:
                if rule.get("type") == "global_strategy_adjustment":
                    rules_delay_mult = rule.get("multiplier", 1.0)
                    log.info(
                        "auto_rule_delay_adjustment",
                        trend=rule.get("trend"),
                        multiplier=rules_delay_mult,
                    )

            # Adaptive delay multiplier — slow down if SRI is blocking
            delay_mult = await tracker.get_recommended_delay_multiplier()
            delay_mult = max(delay_mult, rules_delay_mult)
            if delay_mult > 1.0:
                engine_kwargs["settings"] = settings.model_copy(
                    update={
                        "delay_min_ms": int(settings.delay_min_ms * delay_mult),
                        "delay_max_ms": int(settings.delay_max_ms * delay_mult),
                        "delay_between_pages_ms": int(settings.delay_between_pages_ms * delay_mult),
                    }
                )
                log.info(
                    "adaptive_delays_ajustados",
                    multiplicador=delay_mult,
                    delay_min=engine_kwargs["settings"].delay_min_ms,
                    delay_max=engine_kwargs["settings"].delay_max_ms,
                )

            engine_map = {
                "nodriver": (SRINodriverEngine, SRIScraperEngine),
                "playwright": (SRIScraperEngine, SRINodriverEngine),
            }
            primary_cls, fallback_cls = engine_map[best_engine]
            primary_name = best_engine
            fallback_name = "playwright" if best_engine == "nodriver" else "nodriver"

            log.info(
                "motor_adaptativo_seleccionado",
                motor=primary_name,
                fallback=fallback_name,
                delay_mult=delay_mult,
                tenant_ruc=tenant_ruc,
                periodo=f"{anio}-{mes:02d}",
            )

            # Record current hour for timing analysis
            current_hour = utc_now().hour

            # Knowledge base for persistent learning
            async def _record_to_kb(
                engine_name: str,
                *,
                success: bool,
                blocked: bool = False,
                duration_sec: float = 0.0,
                error: Exception | None = None,
                variant: str | None = None,
                provider: str | None = None,
            ) -> None:
                """Record result to both Redis (short-term) and PostgreSQL (long-term)."""
                try:
                    async with async_session() as kb_session:
                        async with kb_session.begin():
                            kb = SRIKnowledgeBase(kb_session)
                            await kb.record_result(
                                PatternCategory.ENGINE,
                                engine_name,
                                success=success,
                                blocked=blocked,
                                duration_sec=duration_sec,
                            )
                            await kb.record_result(
                                PatternCategory.TIMING,
                                f"hour_{current_hour:02d}",
                                success=success,
                                blocked=blocked,
                            )
                            if blocked and error:
                                await kb.record_block_event(
                                    engine=engine_name,
                                    error_type=type(error).__name__,
                                    error_message=str(error),
                                    captcha_variant=variant,
                                    captcha_provider=provider,
                                    context={
                                        "tenant_ruc": tenant_ruc,
                                        "periodo": f"{anio}-{mes:02d}",
                                        "tipo": tipo_canonico,
                                    },
                                )
                except Exception as kb_exc:
                    log.warning("knowledge_base_write_error", error=str(kb_exc))

            inicio_engine = utc_now()
            try:
                engine = primary_cls(**engine_kwargs)
                if hasattr(engine, '_adaptive_tracker'):
                    engine._adaptive_tracker = tracker
                resultado = await engine.ejecutar()

                dur = (utc_now() - inicio_engine).total_seconds()
                await tracker.record_engine_result(
                    primary_name, success=True, duration_sec=dur,
                )
                await tracker.record_timing(current_hour, success=True)
                await _record_to_kb(primary_name, success=True, duration_sec=dur)

            except SRILoginError:
                await tracker.record_engine_result(primary_name, success=False)
                await _record_to_kb(primary_name, success=False)
                raise

            except (SRICaptchaError, SRITimeoutError) as primary_exc:
                dur = (utc_now() - inicio_engine).total_seconds()
                is_block = isinstance(primary_exc, SRICaptchaError)
                await tracker.record_engine_result(
                    primary_name, success=False, duration_sec=dur, blocked=is_block,
                )
                await tracker.record_timing(current_hour, success=False)
                await _record_to_kb(
                    primary_name, success=False, blocked=is_block,
                    duration_sec=dur, error=primary_exc,
                )

                log.warning(
                    "motor_primario_bloqueado_intentando_fallback",
                    motor_primario=primary_name,
                    motor_fallback=fallback_name,
                    blocked=is_block,
                    error=str(primary_exc),
                    tenant_ruc=tenant_ruc,
                )

                inicio_fallback = utc_now()
                try:
                    engine = fallback_cls(**engine_kwargs)
                    if hasattr(engine, '_adaptive_tracker'):
                        engine._adaptive_tracker = tracker
                    resultado = await engine.ejecutar()

                    dur_fb = (utc_now() - inicio_fallback).total_seconds()
                    await tracker.record_engine_result(
                        fallback_name, success=True, duration_sec=dur_fb,
                    )
                    await _record_to_kb(fallback_name, success=True, duration_sec=dur_fb)
                    log.info(
                        "motor_fallback_exitoso",
                        motor=fallback_name,
                        tenant_ruc=tenant_ruc,
                    )
                except Exception as fallback_exc:
                    dur_fb = (utc_now() - inicio_fallback).total_seconds()
                    fb_block = isinstance(fallback_exc, SRICaptchaError)
                    await tracker.record_engine_result(
                        fallback_name, success=False, duration_sec=dur_fb, blocked=fb_block,
                    )
                    await _record_to_kb(
                        fallback_name, success=False, blocked=fb_block,
                        duration_sec=dur_fb, error=fallback_exc,
                    )
                    log.error(
                        "ambos_motores_fallaron",
                        motor_primario=primary_name,
                        error_primario=str(primary_exc),
                        motor_fallback=fallback_name,
                        error_fallback=str(fallback_exc),
                        tenant_ruc=tenant_ruc,
                    )
                    raise primary_exc

            except Exception as primary_exc:
                dur = (utc_now() - inicio_engine).total_seconds()
                await tracker.record_engine_result(
                    primary_name, success=False, duration_sec=dur,
                )
                await _record_to_kb(
                    primary_name, success=False, duration_sec=dur, error=primary_exc,
                )

                log.warning(
                    "motor_primario_fallo_intentando_fallback",
                    motor_primario=primary_name,
                    motor_fallback=fallback_name,
                    error=str(primary_exc),
                    tenant_ruc=tenant_ruc,
                )
                inicio_fallback = utc_now()
                try:
                    engine = fallback_cls(**engine_kwargs)
                    if hasattr(engine, '_adaptive_tracker'):
                        engine._adaptive_tracker = tracker
                    resultado = await engine.ejecutar()

                    dur_fb = (utc_now() - inicio_fallback).total_seconds()
                    await tracker.record_engine_result(
                        fallback_name, success=True, duration_sec=dur_fb,
                    )
                    await _record_to_kb(fallback_name, success=True, duration_sec=dur_fb)
                    log.info("motor_fallback_exitoso", motor=fallback_name)
                except Exception as fallback_exc:
                    dur_fb = (utc_now() - inicio_fallback).total_seconds()
                    await tracker.record_engine_result(
                        fallback_name, success=False, duration_sec=dur_fb,
                    )
                    await _record_to_kb(
                        fallback_name, success=False, duration_sec=dur_fb, error=fallback_exc,
                    )
                    log.error(
                        "ambos_motores_fallaron",
                        error_primario=str(primary_exc),
                        error_fallback=str(fallback_exc),
                    )
                    raise primary_exc
        finally:
            await r.aclose()

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
            "tipo": tipo_canonico,
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
