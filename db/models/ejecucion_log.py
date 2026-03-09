import enum
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from utils.time import utc_now


class EstadoEjecucion(str, enum.Enum):
    INICIADO = "iniciado"
    EN_PROGRESO = "en_progreso"
    COMPLETADO = "completado"
    ERROR = "error"
    CAPTCHA_BLOQUEADO = "captcha_bloqueado"
    SRI_MANTENIMIENTO = "sri_mantenimiento"


class EjecucionLog(Base):
    __tablename__ = "ejecuciones_log"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True
    )
    periodo_anio: Mapped[int]
    periodo_mes: Mapped[int]
    tipo_comprobante: Mapped[str] = mapped_column(String(50))
    estado: Mapped[EstadoEjecucion] = mapped_column(index=True)
    total_encontrados: Mapped[int] = mapped_column(default=0)
    total_nuevos: Mapped[int] = mapped_column(default=0)
    total_errores: Mapped[int] = mapped_column(default=0)
    duracion_seg: Mapped[float | None] = mapped_column(nullable=True)
    error_mensaje: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    screenshot_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    pagina_actual: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant = relationship("Tenant", back_populates="ejecuciones")
