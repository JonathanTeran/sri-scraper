import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from utils.time import utc_now


class TipoComprobante(str, enum.Enum):
    FACTURA = "factura"
    LIQUIDACION = "liquidacion"
    NOTA_CREDITO = "nota_credito"
    NOTA_DEBITO = "nota_debito"
    RETENCION = "retencion"
    GUIA_REMISION = "guia_remision"


class Comprobante(Base):
    __tablename__ = "comprobantes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "clave_acceso"),
        Index("ix_comp_tenant_fecha", "tenant_id", "fecha_emision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True
    )

    # Autorización SRI
    estado_autorizacion: Mapped[str] = mapped_column(String(20))
    numero_autorizacion: Mapped[str] = mapped_column(String(49))
    fecha_autorizacion: Mapped[datetime | None] = mapped_column(nullable=True)
    ambiente_autorizacion: Mapped[str] = mapped_column(String(20))

    # Info tributaria del emisor
    ruc_emisor: Mapped[str] = mapped_column(String(13), index=True)
    razon_social_emisor: Mapped[str] = mapped_column(String(300))
    nombre_comercial: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    cod_doc: Mapped[str] = mapped_column(String(2))
    tipo_comprobante: Mapped[TipoComprobante] = mapped_column(index=True)
    clave_acceso: Mapped[str] = mapped_column(String(49), index=True)
    estab: Mapped[str] = mapped_column(String(3))
    pto_emi: Mapped[str] = mapped_column(String(3))
    secuencial: Mapped[str] = mapped_column(String(9))
    serie: Mapped[str] = mapped_column(String(7))
    numero_completo: Mapped[str] = mapped_column(String(17))

    # Cabecera del comprobante
    fecha_emision: Mapped[date] = mapped_column(index=True)
    dir_establecimiento: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    obligado_contabilidad: Mapped[str | None] = mapped_column(
        String(2), nullable=True
    )
    contribuyente_especial: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )

    # Receptor
    tipo_id_receptor: Mapped[str | None] = mapped_column(
        String(2), nullable=True
    )
    razon_social_receptor: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    identificacion_receptor: Mapped[str | None] = mapped_column(
        String(13), nullable=True
    )

    # Período fiscal (solo retenciones)
    periodo_fiscal: Mapped[str | None] = mapped_column(
        String(7), nullable=True
    )

    # Totales financieros
    total_sin_impuestos: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=0
    )
    total_descuento: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=0
    )
    total_iva: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    importe_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    moneda: Mapped[str] = mapped_column(String(10), default="DOLAR")
    propina: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)

    # XML completo
    xml_raw: Mapped[str] = mapped_column(Text)
    xml_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    # Metadata de procesamiento
    parse_error: Mapped[bool] = mapped_column(default=False)
    parse_error_msg: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    # Relaciones
    tenant = relationship("Tenant", back_populates="comprobantes")
    detalles: Mapped[list["DetalleComprobante"]] = relationship(
        back_populates="comprobante", cascade="all, delete-orphan"
    )
    retenciones: Mapped[list["ImpuestoRetencion"]] = relationship(
        back_populates="comprobante", cascade="all, delete-orphan"
    )
    pagos: Mapped[list["Pago"]] = relationship(
        back_populates="comprobante", cascade="all, delete-orphan"
    )
