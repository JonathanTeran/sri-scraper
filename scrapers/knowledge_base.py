"""
Persistent knowledge base for SRI blocking pattern learning.

This module complements the Redis-based adaptive_strategy.py (short-term, 7-day TTL)
with a PostgreSQL-backed knowledge base that accumulates patterns forever.

The knowledge base:
- Aggregates long-term success/failure stats per engine, variant, provider, hour
- Records every block event with full context for pattern mining
- Detects dangerous hours, unreliable variants, and best strategies
- Feeds recommendations back into the adaptive strategy
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from db.models.knowledge_base import (
    BlockEvent,
    KnowledgeEntry,
    PatternCategory,
)
from utils.time import utc_now

log = structlog.get_logger()


class SRIKnowledgeBase:
    """Persistent knowledge base that learns SRI blocking patterns over time."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ── Recording ─────────────────────────────────────────────────────

    async def record_result(
        self,
        category: PatternCategory,
        key: str,
        *,
        success: bool,
        blocked: bool = False,
        duration_sec: float = 0.0,
        metadata: dict | None = None,
    ) -> None:
        """Upsert a result into the knowledge base."""
        now = utc_now()
        stmt = pg_insert(KnowledgeEntry).values(
            category=category,
            key=key,
            successes=1 if success else 0,
            failures=0 if success else 1,
            blocks=1 if blocked else 0,
            total_duration_sec=duration_sec,
            duration_count=1 if duration_sec > 0 else 0,
            metadata_json=metadata,
            last_success_at=now if success else None,
            last_failure_at=now if not success else None,
            created_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["category", "key"],
            set_={
                "successes": KnowledgeEntry.successes + (1 if success else 0),
                "failures": KnowledgeEntry.failures + (0 if success else 1),
                "blocks": KnowledgeEntry.blocks + (1 if blocked else 0),
                "total_duration_sec": KnowledgeEntry.total_duration_sec + duration_sec,
                "duration_count": KnowledgeEntry.duration_count + (1 if duration_sec > 0 else 0),
                "last_success_at": now if success else KnowledgeEntry.last_success_at,
                "last_failure_at": now if not success else KnowledgeEntry.last_failure_at,
                "updated_at": now,
            },
        )
        await self._session.execute(stmt)

    async def record_block_event(
        self,
        *,
        engine: str,
        error_type: str,
        error_message: str | None = None,
        captcha_variant: str | None = None,
        captcha_provider: str | None = None,
        context: dict | None = None,
    ) -> None:
        """Record an individual block event for pattern analysis."""
        now = utc_now()
        event = BlockEvent(
            engine=engine,
            error_type=error_type,
            error_message=error_message[:500] if error_message else None,
            captcha_variant=captcha_variant,
            captcha_provider=captcha_provider,
            hour_of_day=now.hour,
            day_of_week=now.weekday(),
            context_json=context,
        )
        self._session.add(event)

    # ── Querying knowledge ────────────────────────────────────────────

    async def get_best_engine(self) -> str | None:
        """Return the engine with best long-term success rate (min 10 attempts)."""
        stmt = (
            select(KnowledgeEntry)
            .where(KnowledgeEntry.category == PatternCategory.ENGINE)
            .where((KnowledgeEntry.successes + KnowledgeEntry.failures) >= 10)
        )
        result = await self._session.execute(stmt)
        entries = result.scalars().all()
        if not entries:
            return None
        best = max(entries, key=lambda e: e.success_rate)
        return best.key

    async def get_dangerous_hours(self) -> list[int]:
        """Return hours with high block rates (>40% failure in last 30 days)."""
        cutoff = utc_now() - timedelta(days=30)
        stmt = (
            select(
                BlockEvent.hour_of_day,
                func.count().label("block_count"),
            )
            .where(BlockEvent.created_at >= cutoff)
            .group_by(BlockEvent.hour_of_day)
            .having(func.count() >= 5)
            .order_by(func.count().desc())
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        # Get total attempts per hour from knowledge base
        dangerous = []
        for hour, block_count in rows:
            hour_key = f"hour_{hour:02d}"
            entry_stmt = select(KnowledgeEntry).where(
                KnowledgeEntry.category == PatternCategory.TIMING,
                KnowledgeEntry.key == hour_key,
            )
            entry_result = await self._session.execute(entry_stmt)
            entry = entry_result.scalar_one_or_none()
            if entry and entry.total > 0:
                block_rate = block_count / entry.total
                if block_rate > 0.4:
                    dangerous.append(hour)
            elif block_count >= 10:
                dangerous.append(hour)

        return dangerous

    async def get_variant_blacklist(self) -> list[str]:
        """Return CAPTCHA variants with historically very low success (<20%)."""
        stmt = (
            select(KnowledgeEntry)
            .where(KnowledgeEntry.category == PatternCategory.CAPTCHA_VARIANT)
            .where((KnowledgeEntry.successes + KnowledgeEntry.failures) >= 15)
        )
        result = await self._session.execute(stmt)
        entries = result.scalars().all()
        return [e.key for e in entries if e.success_rate < 0.20]

    async def get_provider_ranking(self) -> list[tuple[str, float]]:
        """Return providers ranked by long-term success rate."""
        stmt = (
            select(KnowledgeEntry)
            .where(KnowledgeEntry.category == PatternCategory.CAPTCHA_PROVIDER)
            .where((KnowledgeEntry.successes + KnowledgeEntry.failures) >= 5)
        )
        result = await self._session.execute(stmt)
        entries = result.scalars().all()
        ranked = sorted(entries, key=lambda e: e.success_rate, reverse=True)
        return [(e.key, round(e.success_rate, 3)) for e in ranked]

    async def detect_block_patterns(self) -> dict:
        """Analyze recent block events and detect patterns.

        Returns insights like:
        - Most common error types
        - Most blocked hours/days
        - Correlation between engine+variant and blocks
        """
        cutoff = utc_now() - timedelta(days=14)

        # Top error types
        error_stmt = (
            select(
                BlockEvent.error_type,
                func.count().label("cnt"),
            )
            .where(BlockEvent.created_at >= cutoff)
            .group_by(BlockEvent.error_type)
            .order_by(func.count().desc())
            .limit(10)
        )
        error_result = await self._session.execute(error_stmt)
        top_errors = [{"error": row[0], "count": row[1]} for row in error_result.all()]

        # Block distribution by hour
        hour_stmt = (
            select(
                BlockEvent.hour_of_day,
                func.count().label("cnt"),
            )
            .where(BlockEvent.created_at >= cutoff)
            .group_by(BlockEvent.hour_of_day)
            .order_by(func.count().desc())
        )
        hour_result = await self._session.execute(hour_stmt)
        hour_dist = {row[0]: row[1] for row in hour_result.all()}

        # Block distribution by day of week
        day_stmt = (
            select(
                BlockEvent.day_of_week,
                func.count().label("cnt"),
            )
            .where(BlockEvent.created_at >= cutoff)
            .group_by(BlockEvent.day_of_week)
            .order_by(func.count().desc())
        )
        day_result = await self._session.execute(day_stmt)
        day_dist = {row[0]: row[1] for row in day_result.all()}

        # Engine+variant combos most blocked
        combo_stmt = (
            select(
                BlockEvent.engine,
                BlockEvent.captcha_variant,
                func.count().label("cnt"),
            )
            .where(BlockEvent.created_at >= cutoff)
            .where(BlockEvent.captcha_variant.isnot(None))
            .group_by(BlockEvent.engine, BlockEvent.captcha_variant)
            .order_by(func.count().desc())
            .limit(10)
        )
        combo_result = await self._session.execute(combo_stmt)
        bad_combos = [
            {"engine": row[0], "variant": row[1], "blocks": row[2]}
            for row in combo_result.all()
        ]

        total_blocks_stmt = (
            select(func.count())
            .select_from(BlockEvent)
            .where(BlockEvent.created_at >= cutoff)
        )
        total = (await self._session.execute(total_blocks_stmt)).scalar() or 0

        return {
            "period_days": 14,
            "total_blocks": total,
            "top_errors": top_errors,
            "blocks_by_hour": hour_dist,
            "blocks_by_day_of_week": day_dist,
            "worst_engine_variant_combos": bad_combos,
        }

    async def get_full_summary(self) -> dict:
        """Complete knowledge base summary for health/monitoring."""
        engines_stmt = select(KnowledgeEntry).where(
            KnowledgeEntry.category == PatternCategory.ENGINE
        )
        engines_result = await self._session.execute(engines_stmt)
        engines = {
            e.key: {
                "success_rate": round(e.success_rate, 3),
                "total": e.total,
                "blocks": e.blocks,
                "avg_duration": round(e.avg_duration, 1),
            }
            for e in engines_result.scalars().all()
        }

        return {
            "engines": engines,
            "best_engine": await self.get_best_engine(),
            "dangerous_hours": await self.get_dangerous_hours(),
            "variant_blacklist": await self.get_variant_blacklist(),
            "provider_ranking": await self.get_provider_ranking(),
            "block_patterns": await self.detect_block_patterns(),
        }
