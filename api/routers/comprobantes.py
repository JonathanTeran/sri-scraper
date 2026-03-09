"""Consulta de comprobantes con filtros y descarga XML."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_db
from db.models.comprobante import Comprobante, TipoComprobante

router = APIRouter()


# ── Schemas ─────────────────────────────────────────────────────────────────

class ComprobanteListItem(BaseModel):
    """Resumen de un comprobante para listados."""

    id: uuid.UUID = Field(description="Identificador único del comprobante")
    ruc_emisor: str = Field(description="RUC del emisor del comprobante")
    razon_social_emisor: str = Field(description="Razón social del emisor")
    tipo_comprobante: str = Field(
        description="Tipo de comprobante (factura, nota_credito, retencion, etc.)"
    )
    numero_completo: str = Field(
        description="Número completo del comprobante (ej: 001-001-000000123)"
    )
    fecha_emision: date = Field(description="Fecha de emisión del comprobante")
    importe_total: Decimal = Field(description="Importe total del comprobante (con IVA)")
    estado_autorizacion: str = Field(
        description="Estado de autorización en el SRI (ej: AUTORIZADO)"
    )
    created_at: str = Field(description="Fecha en que se descargó el comprobante (ISO 8601)")

    model_config = {"from_attributes": True}


class DetalleResponse(BaseModel):
    """Línea de detalle (ítem) de un comprobante."""

    id: uuid.UUID = Field(description="Identificador único del detalle")
    orden: int = Field(description="Número de orden de la línea dentro del comprobante")
    descripcion: str = Field(description="Descripción del producto o servicio")
    cantidad: Decimal = Field(description="Cantidad")
    precio_unitario: Decimal = Field(description="Precio unitario sin impuestos")
    precio_total_sin_impuesto: Decimal = Field(
        description="Subtotal de la línea (cantidad × precio unitario - descuento)"
    )
    iva_valor: Decimal = Field(description="Valor del IVA aplicado a esta línea")

    model_config = {"from_attributes": True}


class RetencionResponse(BaseModel):
    """Impuesto de retención aplicado a un comprobante."""

    id: uuid.UUID = Field(description="Identificador único de la retención")
    codigo: str = Field(description="Código del impuesto")
    tipo_tributo: str = Field(
        description="Tipo de tributo: renta, iva o isd"
    )
    codigo_retencion: str = Field(description="Código de retención del SRI")
    base_imponible: Decimal = Field(description="Base imponible sobre la que se calcula la retención")
    porcentaje_retener: Decimal = Field(description="Porcentaje de retención aplicado")
    valor_retenido: Decimal = Field(description="Valor retenido (base × porcentaje)")

    model_config = {"from_attributes": True}


class PagoResponse(BaseModel):
    """Forma de pago registrada en un comprobante."""

    id: uuid.UUID = Field(description="Identificador único del pago")
    forma_pago: str = Field(description="Código SRI de la forma de pago")
    forma_pago_desc: str = Field(
        description="Descripción de la forma de pago (ej: Transferencia, Efectivo)"
    )
    total: Decimal = Field(description="Monto pagado con esta forma de pago")

    model_config = {"from_attributes": True}


class ComprobanteDetalleResponse(BaseModel):
    """Detalle completo de un comprobante con sus líneas, retenciones y pagos."""

    id: uuid.UUID = Field(description="Identificador único del comprobante")
    tenant_id: uuid.UUID = Field(description="ID del tenant al que pertenece")
    estado_autorizacion: str = Field(
        description="Estado de autorización en el SRI"
    )
    numero_autorizacion: str = Field(
        description="Número de autorización emitido por el SRI"
    )
    ruc_emisor: str = Field(description="RUC del emisor")
    razon_social_emisor: str = Field(description="Razón social del emisor")
    tipo_comprobante: str = Field(description="Tipo de comprobante electrónico")
    clave_acceso: str = Field(
        description="Clave de acceso de 49 dígitos que identifica al comprobante en el SRI"
    )
    numero_completo: str = Field(
        description="Número completo (ej: 001-001-000000123)"
    )
    fecha_emision: date = Field(description="Fecha de emisión")
    total_sin_impuestos: Decimal = Field(description="Total antes de impuestos")
    total_iva: Decimal = Field(description="Total del IVA")
    importe_total: Decimal = Field(description="Importe total del comprobante")
    razon_social_receptor: str | None = Field(
        description="Razón social del receptor (comprador)"
    )
    identificacion_receptor: str | None = Field(
        description="Cédula o RUC del receptor"
    )
    periodo_fiscal: str | None = Field(
        description="Periodo fiscal (solo en retenciones, ej: 03/2026)"
    )
    detalles: list[DetalleResponse] = Field(
        description="Líneas de detalle del comprobante"
    )
    retenciones: list[RetencionResponse] = Field(
        description="Retenciones aplicadas (solo en comprobantes de retención)"
    )
    pagos: list[PagoResponse] = Field(
        description="Formas de pago del comprobante"
    )

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    """Respuesta paginada para listados de comprobantes."""

    items: list[ComprobanteListItem] = Field(
        description="Lista de comprobantes de la página actual"
    )
    total: int = Field(description="Total de comprobantes que coinciden con los filtros")
    page: int = Field(description="Número de página actual")
    page_size: int = Field(description="Cantidad de ítems por página")
    pages: int = Field(description="Total de páginas disponibles")


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get(
    "/tenants/{tenant_id}/comprobantes",
    response_model=PaginatedResponse,
    summary="Listar comprobantes de un tenant",
)
async def listar_comprobantes(
    tenant_id: uuid.UUID,
    tipo: str | None = Query(
        default=None,
        description="Filtrar por tipo de comprobante (factura, nota_credito, retencion, etc.)",
    ),
    desde: date | None = Query(
        default=None, description="Fecha de emisión mínima (YYYY-MM-DD)"
    ),
    hasta: date | None = Query(
        default=None, description="Fecha de emisión máxima (YYYY-MM-DD)"
    ),
    ruc_emisor: str | None = Query(
        default=None, description="Filtrar por RUC del emisor (13 dígitos)"
    ),
    page: int = Query(default=1, ge=1, description="Número de página (desde 1)"),
    page_size: int = Query(
        default=50, ge=1, le=200, description="Cantidad de resultados por página (máx. 200)"
    ),
    db: AsyncSession = Depends(get_db),
):
    """Devuelve los comprobantes de un tenant con filtros opcionales y paginación.

    Los resultados se ordenan por fecha de emisión descendente (más recientes primero).
    """
    query = select(Comprobante).where(
        Comprobante.tenant_id == tenant_id
    )

    if tipo:
        query = query.where(Comprobante.tipo_comprobante == tipo)
    if desde:
        query = query.where(Comprobante.fecha_emision >= desde)
    if hasta:
        query = query.where(Comprobante.fecha_emision <= hasta)
    if ruc_emisor:
        query = query.where(Comprobante.ruc_emisor == ruc_emisor)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginar
    query = (
        query.order_by(Comprobante.fecha_emision.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    items = result.scalars().all()

    return PaginatedResponse(
        items=[
            ComprobanteListItem(
                id=c.id,
                ruc_emisor=c.ruc_emisor,
                razon_social_emisor=c.razon_social_emisor,
                tipo_comprobante=c.tipo_comprobante.value
                if isinstance(c.tipo_comprobante, TipoComprobante)
                else c.tipo_comprobante,
                numero_completo=c.numero_completo,
                fecha_emision=c.fecha_emision,
                importe_total=c.importe_total,
                estado_autorizacion=c.estado_autorizacion,
                created_at=c.created_at.isoformat(),
            )
            for c in items
        ],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if total > 0 else 0,
    )


@router.get(
    "/comprobantes/{comprobante_id}",
    response_model=ComprobanteDetalleResponse,
    summary="Obtener detalle completo de un comprobante",
    responses={404: {"description": "Comprobante no encontrado"}},
)
async def detalle_comprobante(
    comprobante_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Devuelve un comprobante con toda su información: datos del emisor/receptor,
    totales, líneas de detalle, retenciones y formas de pago.
    """
    result = await db.execute(
        select(Comprobante)
        .where(Comprobante.id == comprobante_id)
        .options(
            selectinload(Comprobante.detalles),
            selectinload(Comprobante.retenciones),
            selectinload(Comprobante.pagos),
        )
    )
    comp = result.scalar_one_or_none()
    if not comp:
        raise HTTPException(404, "Comprobante no encontrado")
    return comp


@router.get(
    "/comprobantes/{comprobante_id}/xml",
    summary="Descargar XML original del comprobante",
    responses={404: {"description": "Comprobante no encontrado"}},
)
async def descargar_xml(
    comprobante_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Descarga el archivo XML original del comprobante tal como fue emitido por el SRI.

    El archivo se devuelve como attachment con nombre `{clave_acceso}.xml`.
    """
    result = await db.execute(
        select(Comprobante.xml_raw, Comprobante.clave_acceso).where(
            Comprobante.id == comprobante_id
        )
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(404, "Comprobante no encontrado")

    xml_raw, clave = row
    return Response(
        content=xml_raw.encode("utf-8"),
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{clave}.xml"'
        },
    )
