"""
Automated pattern analysis engine.

Periodically analyzes accumulated knowledge base data to generate
actionable rules that the adaptive strategy can consume dynamically.

Discovers patterns like:
- "enterprise_v3_high works 80% on Tuesdays 2-4pm with CapSolver"
- "After 3 consecutive blocks, waiting 45min beats engine switching"
- "Block rate spikes at hour 14 every weekday"
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select, func, case, and_
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from db.models.knowledge_base import (
    BlockEvent,
    KnowledgeEntry,
    PatternCategory,
)
from utils.time import utc_now

log = structlog.get_logger()

_REDIS_RULES_KEY = "sri:adaptive:auto_rules"
_RULES_TTL = 24 * 3600  # 24 hours


async def analyze_patterns(session: AsyncSession) -> dict:
    """Run full pattern analysis and return discovered rules.

    Should be called periodically (e.g., every 6 hours) from a Celery task.
    """
    log.info("pattern_analysis_iniciando")
    results = {}

    results["time_patterns"] = await _analyze_time_patterns(session)
    results["variant_provider_matrix"] = await _analyze_variant_provider_combos(session)
    results["block_sequences"] = await _analyze_block_sequences(session)
    results["engine_time_correlation"] = await _analyze_engine_time_correlation(session)
    results["decay_analysis"] = await _analyze_temporal_decay(session)

    # Generate actionable rules
    rules = _generate_rules(results)
    results["generated_rules"] = rules

    log.info(
        "pattern_analysis_completado",
        rules_count=len(rules),
        patterns=list(results.keys()),
    )
    return results


async def analyze_and_publish_rules(session: AsyncSession, redis_client) -> list[dict]:
    """Analyze patterns and publish rules to Redis for real-time consumption."""
    analysis = await analyze_patterns(session)
    rules = analysis.get("generated_rules", [])

    if rules and redis_client:
        await redis_client.setex(
            _REDIS_RULES_KEY,
            _RULES_TTL,
            json.dumps(rules, default=str),
        )
        log.info("auto_rules_publicadas", count=len(rules))

    return rules


async def get_active_rules(redis_client) -> list[dict]:
    """Retrieve currently active auto-generated rules from Redis."""
    raw = await redis_client.get(_REDIS_RULES_KEY)
    if not raw:
        return []
    return json.loads(raw)


# ── Analysis functions ────────────────────────────────────────────────


async def _analyze_time_patterns(session: AsyncSession) -> dict:
    """Analyze success/failure patterns by hour and day of week."""
    cutoff = utc_now() - timedelta(days=30)

    # Block events by hour and day
    stmt = (
        select(
            BlockEvent.hour_of_day,
            BlockEvent.day_of_week,
            func.count().label("block_count"),
        )
        .where(BlockEvent.created_at >= cutoff)
        .group_by(BlockEvent.hour_of_day, BlockEvent.day_of_week)
        .order_by(func.count().desc())
    )
    result = await session.execute(stmt)
    rows = result.all()

    # Build heatmap
    heatmap: dict[str, int] = {}
    for hour, day, count in rows:
        key = f"d{day}_h{hour:02d}"
        heatmap[key] = count

    # Find dangerous time slots (top 20% by block count)
    if heatmap:
        counts = sorted(heatmap.values(), reverse=True)
        threshold = counts[max(0, len(counts) // 5)]
        dangerous_slots = [k for k, v in heatmap.items() if v >= threshold]
    else:
        dangerous_slots = []

    # Find safe windows (hours with zero or minimal blocks)
    all_hours = set(range(24))
    blocked_hours = {hour for hour, _, _ in rows}
    safe_hours = sorted(all_hours - blocked_hours)

    return {
        "heatmap": heatmap,
        "dangerous_slots": dangerous_slots,
        "safe_hours": safe_hours,
        "total_blocks_analyzed": sum(heatmap.values()),
    }


async def _analyze_variant_provider_combos(session: AsyncSession) -> dict:
    """Analyze which variant+provider combinations work best."""
    # Get variant stats
    stmt = select(KnowledgeEntry).where(
        KnowledgeEntry.category == PatternCategory.CAPTCHA_VARIANT
    )
    result = await session.execute(stmt)
    variants = result.scalars().all()

    provider_stmt = select(KnowledgeEntry).where(
        KnowledgeEntry.category == PatternCategory.CAPTCHA_PROVIDER
    )
    provider_result = await session.execute(provider_stmt)
    providers = provider_result.scalars().all()

    # Cross-reference with block events to find variant+provider combos
    cutoff = utc_now() - timedelta(days=14)
    combo_stmt = (
        select(
            BlockEvent.captcha_variant,
            BlockEvent.captcha_provider,
            func.count().label("block_count"),
        )
        .where(
            BlockEvent.created_at >= cutoff,
            BlockEvent.captcha_variant.isnot(None),
            BlockEvent.captcha_provider.isnot(None),
        )
        .group_by(BlockEvent.captcha_variant, BlockEvent.captcha_provider)
        .order_by(func.count().desc())
    )
    combo_result = await session.execute(combo_stmt)
    bad_combos = [
        {"variant": row[0], "provider": row[1], "blocks": row[2]}
        for row in combo_result.all()
    ]

    return {
        "variants": {
            v.key: {
                "success_rate": round(v.success_rate, 3),
                "total": v.total,
                "blocks": v.blocks,
            }
            for v in variants
            if v.total >= 5
        },
        "providers": {
            p.key: {
                "success_rate": round(p.success_rate, 3),
                "total": p.total,
            }
            for p in providers
            if p.total >= 5
        },
        "bad_combos": bad_combos[:10],
    }


async def _analyze_block_sequences(session: AsyncSession) -> dict:
    """Analyze consecutive block sequences to find optimal recovery strategies."""
    cutoff = utc_now() - timedelta(days=14)

    # Get recent block events ordered by time
    stmt = (
        select(
            BlockEvent.engine,
            BlockEvent.error_type,
            BlockEvent.created_at,
        )
        .where(BlockEvent.created_at >= cutoff)
        .order_by(BlockEvent.created_at)
    )
    result = await session.execute(stmt)
    events = result.all()

    # Find consecutive block sequences
    sequences: list[dict] = []
    current_seq: list = []
    for engine, error_type, created_at in events:
        if current_seq:
            last_time = current_seq[-1]["time"]
            gap = (created_at - last_time).total_seconds()
            if gap < 300:  # within 5 minutes = same sequence
                current_seq.append({"engine": engine, "error": error_type, "time": created_at})
                continue
            else:
                if len(current_seq) >= 3:
                    sequences.append({
                        "length": len(current_seq),
                        "engines": list(set(e["engine"] for e in current_seq)),
                        "errors": list(set(e["error"] for e in current_seq)),
                        "duration_sec": (current_seq[-1]["time"] - current_seq[0]["time"]).total_seconds(),
                    })
                current_seq = []
        current_seq.append({"engine": engine, "error": error_type, "time": created_at})

    if len(current_seq) >= 3:
        sequences.append({
            "length": len(current_seq),
            "engines": list(set(e["engine"] for e in current_seq)),
            "errors": list(set(e["error"] for e in current_seq)),
            "duration_sec": (current_seq[-1]["time"] - current_seq[0]["time"]).total_seconds(),
        })

    # Statistics
    avg_sequence_length = (
        sum(s["length"] for s in sequences) / len(sequences) if sequences else 0
    )

    return {
        "total_sequences": len(sequences),
        "avg_sequence_length": round(avg_sequence_length, 1),
        "longest_sequence": max((s["length"] for s in sequences), default=0),
        "sequences": sequences[:20],
    }


async def _analyze_engine_time_correlation(session: AsyncSession) -> dict:
    """Correlate engine performance with time of day."""
    timing_entries = (
        await session.execute(
            select(KnowledgeEntry).where(
                KnowledgeEntry.category == PatternCategory.TIMING
            )
        )
    ).scalars().all()

    engine_entries = (
        await session.execute(
            select(KnowledgeEntry).where(
                KnowledgeEntry.category == PatternCategory.ENGINE
            )
        )
    ).scalars().all()

    return {
        "timing_by_hour": {
            e.key: {
                "success_rate": round(e.success_rate, 3),
                "total": e.total,
                "avg_duration": round(e.avg_duration, 1),
            }
            for e in timing_entries
            if e.total >= 3
        },
        "engine_overall": {
            e.key: {
                "success_rate": round(e.success_rate, 3),
                "total": e.total,
                "blocks": e.blocks,
            }
            for e in engine_entries
        },
    }


async def _analyze_temporal_decay(session: AsyncSession) -> dict:
    """Analyze how patterns change over time (trend detection).

    Compares recent performance (last 7 days) vs older (7-30 days)
    to detect improving or degrading trends.
    """
    now = utc_now()
    recent_cutoff = now - timedelta(days=7)
    older_cutoff = now - timedelta(days=30)

    # Recent blocks
    recent_blocks = (
        await session.execute(
            select(func.count())
            .select_from(BlockEvent)
            .where(BlockEvent.created_at >= recent_cutoff)
        )
    ).scalar() or 0

    # Older blocks (7-30 days)
    older_blocks = (
        await session.execute(
            select(func.count())
            .select_from(BlockEvent)
            .where(
                BlockEvent.created_at >= older_cutoff,
                BlockEvent.created_at < recent_cutoff,
            )
        )
    ).scalar() or 0

    # Normalize to per-day rates
    recent_daily = recent_blocks / 7 if recent_blocks else 0
    older_daily = older_blocks / 23 if older_blocks else 0  # 30-7 = 23 days

    trend = "stable"
    if older_daily > 0:
        change_pct = (recent_daily - older_daily) / older_daily
        if change_pct > 0.3:
            trend = "worsening"
        elif change_pct < -0.3:
            trend = "improving"

    return {
        "recent_blocks_7d": recent_blocks,
        "older_blocks_23d": older_blocks,
        "recent_daily_rate": round(recent_daily, 2),
        "older_daily_rate": round(older_daily, 2),
        "trend": trend,
    }


# ── Rule generation ──────────────────────────────────────────────────


def _generate_rules(analysis: dict) -> list[dict]:
    """Convert analysis results into actionable rules."""
    rules: list[dict] = []

    # Time-based rules
    time_data = analysis.get("time_patterns", {})
    for slot in time_data.get("dangerous_slots", []):
        rules.append({
            "type": "avoid_time_slot",
            "slot": slot,
            "action": "increase_delay",
            "multiplier": 2.0,
        })

    safe_hours = time_data.get("safe_hours", [])
    if safe_hours:
        rules.append({
            "type": "prefer_safe_hours",
            "hours": safe_hours[:6],
            "action": "normal_delay",
        })

    # Variant rules
    matrix = analysis.get("variant_provider_matrix", {})
    for combo in matrix.get("bad_combos", []):
        if combo.get("blocks", 0) >= 5:
            rules.append({
                "type": "avoid_combo",
                "variant": combo["variant"],
                "provider": combo["provider"],
                "blocks": combo["blocks"],
                "action": "skip_variant",
            })

    # Sequence rules
    seq_data = analysis.get("block_sequences", {})
    avg_len = seq_data.get("avg_sequence_length", 0)
    if avg_len >= 4:
        rules.append({
            "type": "consecutive_block_strategy",
            "avg_sequence_length": avg_len,
            "action": "cooldown_after_3_blocks",
            "cooldown_minutes": 45,
        })

    # Trend rules
    decay = analysis.get("decay_analysis", {})
    if decay.get("trend") == "worsening":
        rules.append({
            "type": "global_strategy_adjustment",
            "trend": "worsening",
            "action": "increase_delays_globally",
            "multiplier": 1.5,
        })
    elif decay.get("trend") == "improving":
        rules.append({
            "type": "global_strategy_adjustment",
            "trend": "improving",
            "action": "normalize_delays",
            "multiplier": 1.0,
        })

    return rules
