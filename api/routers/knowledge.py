"""Endpoints para consultar la base de conocimiento del SRI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db
from db.models.knowledge_base import (
    BlockEvent,
    KnowledgeEntry,
    PatternCategory,
)
from scrapers.knowledge_base import SRIKnowledgeBase

router = APIRouter(prefix="/knowledge")


@router.get("")
async def get_knowledge_summary(
    db: AsyncSession = Depends(get_db),
):
    """Resumen completo de la base de conocimiento persistente."""
    kb = SRIKnowledgeBase(db)
    return await kb.get_full_summary()


@router.get("/engines")
async def get_engine_stats(
    db: AsyncSession = Depends(get_db),
):
    """Estadísticas por motor de scraping."""
    stmt = select(KnowledgeEntry).where(
        KnowledgeEntry.category == PatternCategory.ENGINE
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()
    return [
        {
            "engine": e.key,
            "successes": e.successes,
            "failures": e.failures,
            "blocks": e.blocks,
            "success_rate": round(e.success_rate, 3),
            "avg_duration": round(e.avg_duration, 1),
            "total": e.total,
            "last_success_at": e.last_success_at,
            "last_failure_at": e.last_failure_at,
        }
        for e in entries
    ]


@router.get("/variants")
async def get_variant_stats(
    db: AsyncSession = Depends(get_db),
):
    """Estadísticas por variante CAPTCHA."""
    stmt = select(KnowledgeEntry).where(
        KnowledgeEntry.category == PatternCategory.CAPTCHA_VARIANT
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()
    return sorted(
        [
            {
                "variant": e.key,
                "successes": e.successes,
                "failures": e.failures,
                "blocks": e.blocks,
                "success_rate": round(e.success_rate, 3),
                "total": e.total,
                "blacklisted": e.success_rate < 0.20 and e.total >= 15,
            }
            for e in entries
        ],
        key=lambda x: x["success_rate"],
        reverse=True,
    )


@router.get("/providers")
async def get_provider_stats(
    db: AsyncSession = Depends(get_db),
):
    """Ranking de proveedores CAPTCHA por fiabilidad."""
    kb = SRIKnowledgeBase(db)
    return await kb.get_provider_ranking()


@router.get("/timing")
async def get_timing_stats(
    db: AsyncSession = Depends(get_db),
):
    """Estadísticas por hora del día."""
    stmt = (
        select(KnowledgeEntry)
        .where(KnowledgeEntry.category == PatternCategory.TIMING)
        .order_by(KnowledgeEntry.key)
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()

    kb = SRIKnowledgeBase(db)
    dangerous = await kb.get_dangerous_hours()

    def _parse_hour(key: str) -> int | None:
        if key.startswith("hour_"):
            try:
                return int(key[5:])
            except ValueError:
                return None
        return None

    return {
        "hours": [
            {
                "hour": e.key,
                "successes": e.successes,
                "failures": e.failures,
                "success_rate": round(e.success_rate, 3),
                "dangerous": _parse_hour(e.key) in dangerous,
            }
            for e in entries
        ],
        "dangerous_hours": dangerous,
    }


@router.get("/blocks")
async def get_block_patterns(
    db: AsyncSession = Depends(get_db),
):
    """Análisis de patrones de bloqueo (últimos 14 días)."""
    kb = SRIKnowledgeBase(db)
    return await kb.detect_block_patterns()


@router.get("/blocks/recent")
async def get_recent_blocks(
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Eventos de bloqueo más recientes."""
    stmt = (
        select(BlockEvent)
        .order_by(BlockEvent.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    events = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "engine": e.engine,
            "error_type": e.error_type,
            "error_message": e.error_message,
            "captcha_variant": e.captcha_variant,
            "captcha_provider": e.captcha_provider,
            "hour_of_day": e.hour_of_day,
            "day_of_week": e.day_of_week,
            "context": e.context_json,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
