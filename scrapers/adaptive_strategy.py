"""
Adaptive strategy tracker for SRI bot detection evasion.

Learns from success/failure patterns and auto-adjusts:
- Engine preference (nodriver vs playwright)
- CAPTCHA variant ordering (which variants succeed more)
- Optimal delays and timing windows
- Provider reliability scores

State is persisted in Redis with TTLs so stale data expires.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

# Redis key prefixes
_PREFIX = "sri:adaptive"
_ENGINE_KEY = f"{_PREFIX}:engine"          # per-engine stats
_VARIANT_KEY = f"{_PREFIX}:variant"        # per-captcha-variant stats
_PROVIDER_KEY = f"{_PREFIX}:provider"      # per-provider stats
_TIMING_KEY = f"{_PREFIX}:timing"          # best time windows
_BLOCK_KEY = f"{_PREFIX}:blocks"           # recent block events
# Defaults — overridable via Settings.adaptive_stats_ttl_days / adaptive_block_ttl_hours
_STATS_TTL = 7 * 24 * 3600                # 7 days
_BLOCK_TTL = 2 * 3600                     # 2 hours for block cooldowns


@dataclass
class StrategyScore:
    """Aggregated score for a strategy (engine, variant, or provider)."""
    name: str
    successes: int = 0
    failures: int = 0
    blocks: int = 0
    last_success_ts: float = 0.0
    last_failure_ts: float = 0.0
    avg_duration_sec: float = 0.0

    @property
    def total(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.5  # neutral default
        return self.successes / self.total

    @property
    def weight(self) -> float:
        """Weighted score: success rate + recency bonus + block penalty."""
        now = time.time()
        rate = self.success_rate

        # Recency bonus: recent successes boost score
        recency = 0.0
        if self.last_success_ts > 0:
            hours_ago = (now - self.last_success_ts) / 3600
            recency = max(0, 0.15 - hours_ago * 0.01)  # decays over 15h

        # Block penalty: recent blocks heavily penalize
        block_penalty = 0.0
        if self.last_failure_ts > 0:
            hours_since_block = (now - self.last_failure_ts) / 3600
            if hours_since_block < 1:
                block_penalty = 0.4  # very recent block
            elif hours_since_block < 3:
                block_penalty = 0.2
            elif hours_since_block < 6:
                block_penalty = 0.1

        # Consecutive block penalty
        if self.blocks > 3:
            block_penalty += 0.15 * min(self.blocks - 3, 5)

        return max(0.0, rate + recency - block_penalty)


class AdaptiveStrategyTracker:
    """
    Tracks and learns from SRI interactions to optimize strategy.

    Two-tier learning:
    - Short-term (Redis, 7-day TTL): fast reaction to recent patterns
    - Long-term (PostgreSQL knowledge base): permanent pattern accumulation

    Usage:
        tracker = AdaptiveStrategyTracker(redis)
        best_engine = await tracker.get_best_engine()
        ordered_variants = await tracker.get_ordered_variants()
        await tracker.record_engine_result("nodriver", success=True)
        await tracker.record_variant_result("enterprise_v3_high", "capsolver", success=False, blocked=True)
    """

    def __init__(
        self,
        redis_client,
        *,
        kb_session_factory=None,
        stats_ttl: int | None = None,
        block_ttl: int | None = None,
    ):
        self._redis = redis_client
        self._kb_session_factory = kb_session_factory  # optional async session factory for long-term KB
        self._stats_ttl = stats_ttl or _STATS_TTL
        self._block_ttl = block_ttl or _BLOCK_TTL

    # ── Recording results ──────────────────────────────────────────────

    async def record_engine_result(
        self,
        engine: str,
        *,
        success: bool,
        duration_sec: float = 0.0,
        blocked: bool = False,
    ) -> None:
        """Record an engine execution result."""
        key = f"{_ENGINE_KEY}:{engine}"
        await self._increment_stats(key, success=success, blocked=blocked, duration_sec=duration_sec)

        if blocked:
            await self._record_block_event(engine, "engine")

        log.debug(
            "adaptive_engine_recorded",
            engine=engine,
            success=success,
            blocked=blocked,
        )

    async def record_variant_result(
        self,
        variant: str,
        provider: str,
        *,
        success: bool,
        blocked: bool = False,
    ) -> None:
        """Record a CAPTCHA variant attempt result."""
        variant_key = f"{_VARIANT_KEY}:{variant}"
        provider_key = f"{_PROVIDER_KEY}:{provider}"

        await self._increment_stats(variant_key, success=success, blocked=blocked)
        await self._increment_stats(provider_key, success=success, blocked=blocked)

        if blocked:
            await self._record_block_event(f"{provider}:{variant}", "captcha")

        log.debug(
            "adaptive_variant_recorded",
            variant=variant,
            provider=provider,
            success=success,
            blocked=blocked,
        )

    async def record_timing(self, hour: int, success: bool) -> None:
        """Record success/failure by hour of day for timing optimization."""
        if not 0 <= hour <= 23:
            log.warning("adaptive_invalid_hour", hour=hour)
            return
        key = f"{_TIMING_KEY}:{hour:02d}"
        await self._increment_stats(key, success=success)

    # ── Querying best strategies ───────────────────────────────────────

    async def get_best_engine(self, default: str = "nodriver") -> str:
        """Return the engine with the highest adaptive score."""
        engines = {}
        for engine_name in ("nodriver", "playwright"):
            score = await self._get_score(f"{_ENGINE_KEY}:{engine_name}", engine_name)
            engines[engine_name] = score

        # If no data, return default
        if all(s.total == 0 for s in engines.values()):
            return default

        best = max(engines.values(), key=lambda s: s.weight)

        log.info(
            "adaptive_engine_seleccion",
            best=best.name,
            scores={
                name: {"weight": round(s.weight, 3), "rate": round(s.success_rate, 3), "total": s.total, "blocks": s.blocks}
                for name, s in engines.items()
            },
        )
        return best.name

    async def get_ordered_variants(
        self,
        variants: list[dict],
    ) -> list[dict]:
        """Reorder CAPTCHA variants by adaptive score (best first).

        Uses both short-term Redis stats and long-term knowledge base blacklist.
        """
        if not variants:
            return variants

        # Consult long-term knowledge base for permanently bad variants
        kb_blacklist: set[str] = set()
        if self._kb_session_factory:
            try:
                from scrapers.knowledge_base import SRIKnowledgeBase
                async with self._kb_session_factory() as kb_session:
                    kb = SRIKnowledgeBase(kb_session)
                    kb_blacklist = set(await kb.get_variant_blacklist())
                    if kb_blacklist:
                        log.info("kb_variants_blacklisted", variants=list(kb_blacklist))
            except Exception as exc:
                log.warning("kb_blacklist_query_error", error=str(exc))

        scored: list[tuple[float, int, dict]] = []
        for idx, variant in enumerate(variants):
            variant_name = variant.get("variant", "unknown")
            score = await self._get_score(f"{_VARIANT_KEY}:{variant_name}", variant_name)

            # Check if this variant is in cooldown (recently blocked heavily)
            in_cooldown = await self._is_in_cooldown(variant_name)
            if in_cooldown or variant_name in kb_blacklist:
                weight = -1.0  # push to end
            else:
                weight = score.weight

            scored.append((weight, idx, variant))

        scored.sort(key=lambda x: (-x[0], x[1]))

        ordered = [item[2] for item in scored]

        log.info(
            "adaptive_variants_reordenados",
            order=[
                f"{v.get('variant', '?')}({round(s, 3)})"
                for s, _, v in scored
            ],
        )
        return ordered

    async def get_provider_health(self, provider: str) -> StrategyScore:
        """Get health score for a specific CAPTCHA provider."""
        return await self._get_score(f"{_PROVIDER_KEY}:{provider}", provider)

    async def should_cooldown_engine(self, engine: str) -> tuple[bool, int]:
        """Check if an engine should cool down due to recent blocks."""
        block_count = await self._get_recent_block_count(engine)
        if block_count >= 5:
            cooldown_sec = min(block_count * 120, 1800)  # max 30 min
            return True, cooldown_sec
        return False, 0

    async def get_recommended_delay_multiplier(self) -> float:
        """
        Return a delay multiplier based on recent block rate.
        If SRI is blocking a lot, we slow down. If things are smooth, we go normal.
        """
        total_blocks = 0
        total_attempts = 0

        for engine_name in ("nodriver", "playwright"):
            score = await self._get_score(f"{_ENGINE_KEY}:{engine_name}", engine_name)
            total_blocks += score.blocks
            total_attempts += score.total

        if total_attempts < 5:
            return 1.0  # not enough data

        block_rate = total_blocks / max(total_attempts, 1)

        if block_rate > 0.5:
            return 2.5  # very aggressive blocking, slow way down
        elif block_rate > 0.3:
            return 1.8
        elif block_rate > 0.15:
            return 1.3
        return 1.0

    async def get_best_hours(self) -> list[int]:
        """Return hours of day sorted by success rate (best first)."""
        hours: list[tuple[float, int]] = []
        for h in range(24):
            score = await self._get_score(f"{_TIMING_KEY}:{h:02d}", f"hour_{h:02d}")
            if score.total > 0:
                hours.append((score.success_rate, h))

        hours.sort(key=lambda x: -x[0])
        return [h for _, h in hours]

    async def get_strategy_summary(self) -> dict:
        """Return a full summary of adaptive learning state."""
        engines = {}
        for name in ("nodriver", "playwright"):
            s = await self._get_score(f"{_ENGINE_KEY}:{name}", name)
            engines[name] = {
                "success_rate": round(s.success_rate, 3),
                "weight": round(s.weight, 3),
                "total": s.total,
                "blocks": s.blocks,
            }

        delay_mult = await self.get_recommended_delay_multiplier()
        best_engine = await self.get_best_engine()

        return {
            "best_engine": best_engine,
            "engines": engines,
            "delay_multiplier": delay_mult,
            "best_hours": await self.get_best_hours(),
        }

    # ── Internal helpers ───────────────────────────────────────────────

    async def _increment_stats(
        self,
        key: str,
        *,
        success: bool,
        blocked: bool = False,
        duration_sec: float = 0.0,
    ) -> None:
        r = self._redis
        now = time.time()

        raw = await r.get(key)
        if raw:
            data = json.loads(raw)
        else:
            data = {
                "successes": 0,
                "failures": 0,
                "blocks": 0,
                "last_success_ts": 0.0,
                "last_failure_ts": 0.0,
                "total_duration": 0.0,
                "duration_count": 0,
            }

        if success:
            data["successes"] += 1
            data["last_success_ts"] = now
        else:
            data["failures"] += 1
            data["last_failure_ts"] = now

        if blocked:
            data["blocks"] += 1

        if duration_sec > 0:
            data["total_duration"] += duration_sec
            data["duration_count"] += 1

        await r.setex(key, self._stats_ttl, json.dumps(data))

    async def _get_score(self, key: str, name: str) -> StrategyScore:
        r = self._redis
        raw = await r.get(key)
        if not raw:
            return StrategyScore(name=name)

        data = json.loads(raw)
        avg_dur = 0.0
        if data.get("duration_count", 0) > 0:
            avg_dur = data["total_duration"] / data["duration_count"]

        return StrategyScore(
            name=name,
            successes=data.get("successes", 0),
            failures=data.get("failures", 0),
            blocks=data.get("blocks", 0),
            last_success_ts=data.get("last_success_ts", 0.0),
            last_failure_ts=data.get("last_failure_ts", 0.0),
            avg_duration_sec=avg_dur,
        )

    async def _record_block_event(self, source: str, category: str) -> None:
        key = f"{_BLOCK_KEY}:{source}"
        r = self._redis
        await r.incr(key)
        await r.expire(key, self._block_ttl)

    async def _get_recent_block_count(self, source: str) -> int:
        key = f"{_BLOCK_KEY}:{source}"
        val = await self._redis.get(key)
        return int(val) if val else 0

    async def _is_in_cooldown(self, variant_name: str) -> bool:
        """Check if a variant has been blocked too much recently."""
        block_count = await self._get_recent_block_count(variant_name)
        return block_count >= 3
