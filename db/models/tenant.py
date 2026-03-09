import uuid
from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    nombre: Mapped[str] = mapped_column(String(200))
    ruc: Mapped[str] = mapped_column(String(13), unique=True, index=True)
    sri_usuario_enc: Mapped[str] = mapped_column(Text)
    sri_password_enc: Mapped[str] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(default=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True, onupdate=datetime.utcnow
    )

    comprobantes = relationship("Comprobante", back_populates="tenant")
    ejecuciones = relationship("EjecucionLog", back_populates="tenant")
