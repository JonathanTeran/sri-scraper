"""CRUD de tenants + trigger manual de scraping."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_settings_dep
from config.settings import Settings
from db.models.tenant import Tenant
from scrapers.credential_validator import validar_credenciales_sri
from utils.crypto import decrypt, encrypt

router = APIRouter()


# ── Schemas ─────────────────────────────────────────────────────────────────

class TenantCreate(BaseModel):
    """Datos requeridos para registrar un nuevo contribuyente."""

    nombre: str = Field(
        ..., max_length=200, description="Razón social o nombre del contribuyente"
    )
    ruc: str = Field(
        ...,
        min_length=13,
        max_length=13,
        description="RUC del contribuyente (13 dígitos). Debe ser único en el sistema",
    )
    sri_usuario: str = Field(
        ..., description="Usuario de acceso al portal SRI en Línea"
    )
    sri_password: str = Field(
        ..., description="Contraseña del portal SRI en Línea. Se almacena encriptada"
    )
    config: dict = Field(
        default_factory=dict,
        description="Configuración adicional del tenant (ej: página inicial, filtros)",
    )
    validar_credenciales: bool = Field(
        default=False,
        description="Si es true, intenta validar el login del SRI antes de guardar",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "nombre": "Mi Empresa S.A.",
                    "ruc": "0916429921001",
                    "sri_usuario": "usuario@email.com",
                    "sri_password": "mi_password",
                    "config": {},
                }
            ]
        }
    }


class TenantUpdate(BaseModel):
    """Campos actualizables de un tenant. Solo se modifican los campos enviados."""

    nombre: str | None = Field(None, description="Nueva razón social")
    sri_usuario: str | None = Field(
        None, description="Nuevo usuario SRI. Se re-encripta al guardar"
    )
    sri_password: str | None = Field(
        None, description="Nueva contraseña SRI. Se re-encripta al guardar"
    )
    activo: bool | None = Field(
        None, description="Activar/desactivar el tenant para scraping automático"
    )
    config: dict | None = Field(None, description="Nueva configuración adicional")
    validar_credenciales: bool = Field(
        default=False,
        description="Si es true, intenta validar las nuevas credenciales antes de guardar",
    )


class TenantCredentialCheck(BaseModel):
    ruc: str = Field(..., min_length=13, max_length=13)
    sri_usuario: str = Field(...)
    sri_password: str = Field(...)


class TenantCredentialCheckResponse(BaseModel):
    ok: bool
    message: str


class TenantResponse(BaseModel):
    """Representación de un tenant en las respuestas de la API."""

    id: uuid.UUID = Field(description="Identificador único del tenant")
    nombre: str = Field(description="Razón social o nombre del contribuyente")
    ruc: str = Field(description="RUC del contribuyente (13 dígitos)")
    activo: bool = Field(description="Indica si el tenant está activo para scraping")
    config: dict = Field(description="Configuración adicional del tenant")
    created_at: datetime = Field(description="Fecha de creación del registro")
    updated_at: datetime | None = Field(
        description="Fecha de última actualización"
    )

    model_config = {"from_attributes": True}


class EjecutarRequest(BaseModel):
    """Parámetros para lanzar una ejecución manual de scraping."""

    anio: int = Field(..., description="Año del periodo a consultar (ej: 2026)")
    mes: int = Field(
        ..., ge=1, le=12, description="Mes del periodo a consultar (1-12)"
    )
    tipo_comprobante: str = Field(
        default="Factura",
        description=(
            "Tipo de comprobante a descargar. Valores: "
            "Factura, Liquidación de compra, Notas de Crédito, "
            "Notas de Débito, Comprobante de Retención"
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "anio": 2026,
                    "mes": 3,
                    "tipo_comprobante": "Factura",
                }
            ]
        }
    }


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get(
    "/tenants",
    response_model=list[TenantResponse],
    summary="Listar todos los tenants",
)
async def listar_tenants(db: AsyncSession = Depends(get_db)):
    """Devuelve todos los tenants registrados, ordenados alfabéticamente por nombre."""
    result = await db.execute(select(Tenant).order_by(Tenant.nombre))
    return result.scalars().all()


@router.post(
    "/tenants",
    response_model=TenantResponse,
    status_code=201,
    summary="Crear tenant",
    responses={400: {"description": "Ya existe un tenant con ese RUC"}},
)
async def crear_tenant(
    data: TenantCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    """Registra un nuevo contribuyente en el sistema.

    Las credenciales del SRI se almacenan encriptadas con Fernet.
    El RUC debe ser único — si ya existe, retorna error 400.
    """
    # Verificar RUC único
    existing = await db.execute(
        select(Tenant).where(Tenant.ruc == data.ruc)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Ya existe un tenant con RUC {data.ruc}")

    if data.validar_credenciales:
        validation = await validar_credenciales_sri(
            ruc=data.ruc,
            usuario=data.sri_usuario,
            password=data.sri_password,
            settings=settings,
        )
        if not validation.ok:
            raise HTTPException(400, validation.message)

    tenant = Tenant(
        nombre=data.nombre,
        ruc=data.ruc,
        sri_usuario_enc=encrypt(data.sri_usuario, settings.secret_key),
        sri_password_enc=encrypt(data.sri_password, settings.secret_key),
        config=data.config,
    )
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)
    return tenant


@router.get(
    "/tenants/{tenant_id}",
    response_model=TenantResponse,
    summary="Obtener detalle de un tenant",
    responses={404: {"description": "Tenant no encontrado"}},
)
async def detalle_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Devuelve los datos de un tenant específico por su ID."""
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")
    return tenant


@router.patch(
    "/tenants/{tenant_id}",
    response_model=TenantResponse,
    summary="Actualizar tenant",
    responses={404: {"description": "Tenant no encontrado"}},
)
async def actualizar_tenant(
    tenant_id: uuid.UUID,
    data: TenantUpdate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    """Actualiza parcialmente un tenant. Solo se modifican los campos enviados.

    Si se actualizan credenciales (sri_usuario o sri_password),
    se re-encriptan antes de guardar.
    """
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")

    nuevo_usuario = data.sri_usuario or decrypt(
        tenant.sri_usuario_enc,
        settings.secret_key,
    )
    nuevo_password = data.sri_password or decrypt(
        tenant.sri_password_enc,
        settings.secret_key,
    )
    if data.validar_credenciales:
        validation = await validar_credenciales_sri(
            ruc=tenant.ruc,
            usuario=nuevo_usuario,
            password=nuevo_password,
            settings=settings,
        )
        if not validation.ok:
            raise HTTPException(400, validation.message)

    if data.nombre is not None:
        tenant.nombre = data.nombre
    if data.sri_usuario is not None:
        tenant.sri_usuario_enc = encrypt(
            data.sri_usuario, settings.secret_key
        )
    if data.sri_password is not None:
        tenant.sri_password_enc = encrypt(
            data.sri_password, settings.secret_key
        )
    if data.activo is not None:
        tenant.activo = data.activo
    if data.config is not None:
        tenant.config = data.config

    await db.flush()
    await db.refresh(tenant)
    return tenant


@router.post(
    "/tenants/validate-credentials",
    response_model=TenantCredentialCheckResponse,
    summary="Validar credenciales SRI sin guardar el tenant",
)
async def validar_credenciales(
    data: TenantCredentialCheck,
    settings: Settings = Depends(get_settings_dep),
):
    result = await validar_credenciales_sri(
        ruc=data.ruc,
        usuario=data.sri_usuario,
        password=data.sri_password,
        settings=settings,
    )
    if not result.ok:
        raise HTTPException(400, result.message)
    return TenantCredentialCheckResponse(
        ok=result.ok,
        message=result.message,
    )


@router.delete(
    "/tenants/{tenant_id}",
    summary="Desactivar tenant (soft delete)",
    responses={404: {"description": "Tenant no encontrado"}},
)
async def desactivar_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    """Desactiva un tenant (soft delete). No elimina datos, solo marca `activo=False`.

    El tenant dejará de incluirse en las ejecuciones automáticas de scraping.
    """
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")

    tenant.activo = False
    return {"message": "Tenant desactivado"}


@router.post(
    "/tenants/{tenant_id}/ejecutar",
    summary="Lanzar scraping manual",
    responses={
        404: {"description": "Tenant no encontrado"},
        400: {"description": "Tenant desactivado"},
    },
)
async def ejecutar_scraping(
    tenant_id: uuid.UUID,
    data: EjecutarRequest,
    db: AsyncSession = Depends(get_db),
):
    """Encola una tarea de scraping para un tenant y periodo específico.

    La tarea se ejecuta en segundo plano con Celery. Devuelve el `task_id`
    para poder rastrear su progreso en el endpoint de ejecuciones.
    """
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")
    if not tenant.activo:
        raise HTTPException(400, "Tenant desactivado")

    from tasks.scrape_tasks import scrape_tenant_periodo

    task = scrape_tenant_periodo.delay(
        str(tenant_id),
        data.anio,
        data.mes,
        data.tipo_comprobante,
    )
    return {
        "message": "Scraping encolado",
        "task_id": task.id,
        "tenant_ruc": tenant.ruc,
        "periodo": f"{data.anio}-{data.mes:02d}",
    }
