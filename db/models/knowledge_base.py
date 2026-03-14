"""Persistent knowledge base for SRI blocking pattern learning."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from utils.time import utc_now


class PatternCategory(str, enum.Enum):
    ENGINE = "engine"
    CAPTCHA_VARIANT = "captcha_variant"
    CAPTCHA_PROVIDER = "captcha_provider"
    TIMING = "timing"
    BLOCK_SIGNAL = "block_signal"
    STEALTH = "stealth"


class KnowledgeEntry(Base):
    """Long-term knowledge about SRI blocking patterns.

    Unlike Redis adaptive stats (7-day TTL), this table persists forever
    and accumulates aggregated insights over weeks/months.
    """

    __tablename__ = "sri_knowledge_base"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    category: Mapped[PatternCategory] = mapped_column(index=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    successes: Mapped[int] = mapped_column(default=0)
    failures: Mapped[int] = mapped_column(default=0)
    blocks: Mapped[int] = mapped_column(default=0)
    total_duration_sec: Mapped[float] = mapped_column(default=0.0)
    duration_count: Mapped[int] = mapped_column(default=0)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True, onupdate=utc_now)

    __table_args__ = (
        Index("ix_kb_category_key", "category", "key", unique=True),
    )

    @property
    def total(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.5
        return self.successes / self.total

    @property
    def avg_duration(self) -> float:
        if self.duration_count == 0:
            return 0.0
        return self.total_duration_sec / self.duration_count


class BlockEvent(Base):
    """Individual block events for pattern analysis.

    Stores each blocking event with context so the system can detect
    new blocking patterns (time-based, user-agent-based, etc.).
    """

    __tablename__ = "sri_block_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    engine: Mapped[str] = mapped_column(String(50), index=True)
    error_type: Mapped[str] = mapped_column(String(100), index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    captcha_variant: Mapped[str | None] = mapped_column(String(100), nullable=True)
    captcha_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    hour_of_day: Mapped[int]
    day_of_week: Mapped[int]
    context_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    __table_args__ = (
        Index("ix_block_events_time", "created_at"),
        Index("ix_block_events_hour", "hour_of_day"),
    )
