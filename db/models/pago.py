import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class Pago(Base):
    __tablename__ = "pagos"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    comprobante_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comprobantes.id"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    forma_pago: Mapped[str] = mapped_column(String(2))
    forma_pago_desc: Mapped[str] = mapped_column(String(100))
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    plazo: Mapped[str | None] = mapped_column(String(10), nullable=True)
    unidad_tiempo: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )

    comprobante = relationship("Comprobante", back_populates="pagos")
