"""Historial de ejecuciones de scraping."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from db.models.ejecucion_log import EjecucionLog

router = APIRouter()


class EjecucionResponse(BaseModel):
    """Registro de una ejecución de scraping."""

    id: uuid.UUID = Field(description="Identificador único de la ejecución")
    tenant_id: uuid.UUID = Field(description="ID del tenant asociado")
    periodo_anio: int = Field(description="Año del periodo consultado")
    periodo_mes: int = Field(description="Mes del periodo consultado (1-12)")
    tipo_comprobante: str = Field(
        description="Tipo de comprobante consultado (ej: Factura)"
    )
    estado: str = Field(
        description=(
            "Estado de la ejecución: iniciado, en_progreso, completado, "
            "error, captcha_bloqueado, sri_mantenimiento"
        )
    )
    total_encontrados: int = Field(
        description="Cantidad de comprobantes encontrados en el SRI para el periodo"
    )
    total_nuevos: int = Field(
        description="Cantidad de comprobantes nuevos descargados (no existían en BD)"
    )
    total_errores: int = Field(
        description="Cantidad de comprobantes que fallaron al descargar o parsear"
    )
    duracion_seg: float | None = Field(
        description="Duración total de la ejecución en segundos"
    )
    error_mensaje: str | None = Field(
        description="Mensaje de error si la ejecución falló"
    )
    pagina_actual: int = Field(
        description="Última página procesada (útil para retomar ejecuciones interrumpidas)"
    )
    created_at: datetime = Field(description="Fecha/hora de inicio de la ejecución")
    finished_at: datetime | None = Field(
        description="Fecha/hora de finalización de la ejecución"
    )

    model_config = {"from_attributes": True}


@router.get(
    "/tenants/{tenant_id}/ejecuciones",
    response_model=list[EjecucionResponse],
    summary="Listar historial de ejecuciones",
)
async def listar_ejecuciones(
    tenant_id: uuid.UUID,
    limit: int = Query(
        default=50, ge=1, le=200,
        description="Cantidad máxima de ejecuciones a devolver (máx. 200)",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Devuelve el historial de ejecuciones de scraping de un tenant,
    ordenado por fecha de inicio descendente (más recientes primero).
    """
    result = await db.execute(
        select(EjecucionLog)
        .where(EjecucionLog.tenant_id == tenant_id)
        .order_by(EjecucionLog.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.get(
    "/ejecuciones/{ejecucion_id}",
    response_model=EjecucionResponse,
    summary="Obtener detalle de una ejecución",
    responses={404: {"description": "Ejecución no encontrada"}},
)
async def detalle_ejecucion(
    ejecucion_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Devuelve los datos completos de una ejecución de scraping específica."""
    result = await db.execute(
        select(EjecucionLog).where(EjecucionLog.id == ejecucion_id)
    )
    ej = result.scalar_one_or_none()
    if not ej:
        raise HTTPException(404, "Ejecución no encontrada")
    return ej
