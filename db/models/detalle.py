import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class DetalleComprobante(Base):
    __tablename__ = "detalles_comprobante"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    comprobante_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comprobantes.id"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    orden: Mapped[int] = mapped_column(default=0)

    codigo_principal: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    codigo_auxiliar: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    descripcion: Mapped[str] = mapped_column(Text)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    precio_unitario: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    descuento: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    precio_total_sin_impuesto: Mapped[Decimal] = mapped_column(
        Numeric(14, 2)
    )
    iva_codigo: Mapped[str | None] = mapped_column(
        String(2), nullable=True
    )
    iva_codigo_porcentaje: Mapped[str | None] = mapped_column(
        String(2), nullable=True
    )
    iva_tarifa: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    iva_base_imponible: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=0
    )
    iva_valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)

    comprobante = relationship(
        "Comprobante", back_populates="detalles"
    )
