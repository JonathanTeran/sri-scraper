import enum
import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class TipoTributo(str, enum.Enum):
    RENTA = "renta"
    IVA = "iva"
    ISD = "isd"


class ImpuestoRetencion(Base):
    __tablename__ = "impuestos_retencion"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    comprobante_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comprobantes.id"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))

    codigo: Mapped[str] = mapped_column(String(1))
    tipo_tributo: Mapped[TipoTributo]
    codigo_retencion: Mapped[str] = mapped_column(String(10))
    base_imponible: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    porcentaje_retener: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    valor_retenido: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    cod_doc_sustento: Mapped[str | None] = mapped_column(
        String(2), nullable=True
    )
    num_doc_sustento: Mapped[str | None] = mapped_column(
        String(17), nullable=True
    )
    fecha_emision_doc_sustento: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )

    comprobante = relationship(
        "Comprobante", back_populates="retenciones"
    )
