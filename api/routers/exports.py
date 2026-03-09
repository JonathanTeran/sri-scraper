"""Exportación de comprobantes a Excel."""

import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_db
from db.models.comprobante import Comprobante
from exporters.excel_exporter import exportar_comprobantes_excel

router = APIRouter()


@router.get(
    "/tenants/{tenant_id}/exportar",
    summary="Exportar comprobantes a Excel",
)
async def exportar_excel(
    tenant_id: uuid.UUID,
    desde: date | None = Query(
        default=None, description="Fecha de emisión mínima (YYYY-MM-DD)"
    ),
    hasta: date | None = Query(
        default=None, description="Fecha de emisión máxima (YYYY-MM-DD)"
    ),
    tipo: str | None = Query(
        default=None,
        description=(
            "Filtrar por tipo de comprobante (factura, nota_credito, etc.). "
            "Usar 'todos' o no enviar para incluir todos los tipos"
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    """Genera y descarga un archivo Excel (.xlsx) con los comprobantes del tenant.

    El archivo contiene 3 hojas:
    - **Comprobantes**: cabeceras con datos del emisor, receptor, totales
    - **Detalles**: líneas de detalle de cada comprobante
    - **Retenciones**: retenciones aplicadas

    Se descarga como attachment con nombre `comprobantes[_desde_YYYY-MM-DD][_hasta_YYYY-MM-DD].xlsx`.
    """
    query = (
        select(Comprobante)
        .where(Comprobante.tenant_id == tenant_id)
        .options(
            selectinload(Comprobante.detalles),
            selectinload(Comprobante.retenciones),
        )
    )

    if desde:
        query = query.where(Comprobante.fecha_emision >= desde)
    if hasta:
        query = query.where(Comprobante.fecha_emision <= hasta)
    if tipo and tipo != "todos":
        query = query.where(Comprobante.tipo_comprobante == tipo)

    query = query.order_by(Comprobante.fecha_emision.desc())
    result = await db.execute(query)
    comprobantes = result.scalars().all()

    excel_bytes = exportar_comprobantes_excel(comprobantes)

    periodo = ""
    if desde:
        periodo += f"_desde_{desde}"
    if hasta:
        periodo += f"_hasta_{hasta}"

    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="comprobantes{periodo}.xlsx"'
            )
        },
    )
