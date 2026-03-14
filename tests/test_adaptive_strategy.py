"""Tests for scrapers.adaptive_strategy module."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapers.adaptive_strategy import (
    AdaptiveStrategyTracker,
    StrategyScore,
    _ENGINE_KEY,
    _VARIANT_KEY,
    _PROVIDER_KEY,
    _TIMING_KEY,
    _BLOCK_KEY,
    _STATS_TTL,
    _BLOCK_TTL,
)


# ── StrategyScore unit tests ─────────────────────────────────────────


class TestStrategyScore:
    def test_success_rate_no_data(self):
        s = StrategyScore(name="test")
        assert s.success_rate == 0.5  # neutral default

    def test_success_rate_all_success(self):
        s = StrategyScore(name="test", successes=10, failures=0)
        assert s.success_rate == 1.0

    def test_success_rate_mixed(self):
        s = StrategyScore(name="test", successes=7, failures=3)
        assert s.success_rate == pytest.approx(0.7)

    def test_total(self):
        s = StrategyScore(name="test", successes=5, failures=3)
        assert s.total == 8

    def test_weight_no_data(self):
        s = StrategyScore(name="test")
        assert s.weight == pytest.approx(0.5)

    def test_weight_with_recent_success(self):
        s = StrategyScore(
            name="test",
            successes=10,
            failures=0,
            last_success_ts=time.time(),  # just now
        )
        assert s.weight > 1.0  # success_rate(1.0) + recency bonus

    def test_weight_with_recent_block(self):
        s = StrategyScore(
            name="test",
            successes=5,
            failures=5,
            blocks=4,
            last_failure_ts=time.time(),  # just now
        )
        # 0.5 rate - 0.4 (very recent block) - 0.15 (consecutive block penalty)
        assert s.weight < 0.1

    def test_weight_never_negative(self):
        s = StrategyScore(
            name="test",
            successes=0,
            failures=10,
            blocks=10,
            last_failure_ts=time.time(),
        )
        assert s.weight >= 0.0


# ── FakeRedis for tracker tests ──────────────────────────────────────


class FakeRedis:
    """Minimal async Redis mock for testing."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    async def get(self, key: str):
        return self._data.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self._data[key] = value
        self._ttls[key] = ttl

    async def incr(self, key: str):
        val = int(self._data.get(key, 0)) + 1
        self._data[key] = str(val)
        return val

    async def expire(self, key: str, ttl: int):
        self._ttls[key] = ttl


# ── AdaptiveStrategyTracker tests ────────────────────────────────────


class TestAdaptiveStrategyTracker:
    @pytest.fixture
    def redis(self):
        return FakeRedis()

    @pytest.fixture
    def tracker(self, redis):
        return AdaptiveStrategyTracker(redis)

    @pytest.mark.asyncio
    async def test_record_engine_result_success(self, tracker, redis):
        await tracker.record_engine_result("nodriver", success=True, duration_sec=5.0)

        raw = await redis.get(f"{_ENGINE_KEY}:nodriver")
        data = json.loads(raw)
        assert data["successes"] == 1
        assert data["failures"] == 0
        assert data["total_duration"] == 5.0

    @pytest.mark.asyncio
    async def test_record_engine_result_failure_blocked(self, tracker, redis):
        await tracker.record_engine_result("playwright", success=False, blocked=True)

        raw = await redis.get(f"{_ENGINE_KEY}:playwright")
        data = json.loads(raw)
        assert data["failures"] == 1
        assert data["blocks"] == 1

        # Block event recorded
        block_val = await redis.get(f"{_BLOCK_KEY}:playwright")
        assert block_val == "1"

    @pytest.mark.asyncio
    async def test_record_variant_result(self, tracker, redis):
        await tracker.record_variant_result(
            "enterprise_v3", "capsolver", success=True
        )

        variant_raw = await redis.get(f"{_VARIANT_KEY}:enterprise_v3")
        provider_raw = await redis.get(f"{_PROVIDER_KEY}:capsolver")
        assert json.loads(variant_raw)["successes"] == 1
        assert json.loads(provider_raw)["successes"] == 1

    @pytest.mark.asyncio
    async def test_record_timing(self, tracker, redis):
        await tracker.record_timing(14, success=True)
        raw = await redis.get(f"{_TIMING_KEY}:14")
        assert json.loads(raw)["successes"] == 1

    @pytest.mark.asyncio
    async def test_get_best_engine_default_no_data(self, tracker):
        result = await tracker.get_best_engine(default="playwright")
        assert result == "playwright"

    @pytest.mark.asyncio
    async def test_get_best_engine_picks_higher_score(self, tracker):
        # nodriver: 8 success, 2 failures
        for _ in range(8):
            await tracker.record_engine_result("nodriver", success=True)
        for _ in range(2):
            await tracker.record_engine_result("nodriver", success=False)

        # playwright: 3 success, 7 failures
        for _ in range(3):
            await tracker.record_engine_result("playwright", success=True)
        for _ in range(7):
            await tracker.record_engine_result("playwright", success=False)

        best = await tracker.get_best_engine()
        assert best == "nodriver"

    @pytest.mark.asyncio
    async def test_get_ordered_variants_sorts_by_weight(self, tracker):
        # Good variant
        for _ in range(5):
            await tracker.record_variant_result("good_v1", "cap", success=True)
        # Bad variant
        for _ in range(5):
            await tracker.record_variant_result("bad_v1", "cap", success=False)

        variants = [
            {"variant": "bad_v1", "config": "x"},
            {"variant": "good_v1", "config": "y"},
        ]
        ordered = await tracker.get_ordered_variants(variants)
        assert ordered[0]["variant"] == "good_v1"
        assert ordered[1]["variant"] == "bad_v1"

    @pytest.mark.asyncio
    async def test_get_ordered_variants_cooldown(self, tracker, redis):
        # Put variant in cooldown by adding 3+ blocks
        for _ in range(4):
            await redis.incr(f"{_BLOCK_KEY}:cooldown_v1")

        variants = [
            {"variant": "cooldown_v1"},
            {"variant": "fresh_v1"},
        ]
        ordered = await tracker.get_ordered_variants(variants)
        assert ordered[0]["variant"] == "fresh_v1"

    @pytest.mark.asyncio
    async def test_should_cooldown_engine(self, tracker, redis):
        # Less than 5 blocks — no cooldown
        for _ in range(3):
            await redis.incr(f"{_BLOCK_KEY}:nodriver")
        should, sec = await tracker.should_cooldown_engine("nodriver")
        assert not should

        # 5+ blocks — cooldown
        for _ in range(3):
            await redis.incr(f"{_BLOCK_KEY}:nodriver")
        should, sec = await tracker.should_cooldown_engine("nodriver")
        assert should
        assert sec > 0

    @pytest.mark.asyncio
    async def test_get_recommended_delay_multiplier_no_data(self, tracker):
        mult = await tracker.get_recommended_delay_multiplier()
        assert mult == 1.0

    @pytest.mark.asyncio
    async def test_get_recommended_delay_multiplier_high_blocks(self, tracker):
        # Record many blocks
        for _ in range(8):
            await tracker.record_engine_result("nodriver", success=False, blocked=True)
        for _ in range(2):
            await tracker.record_engine_result("nodriver", success=True)

        mult = await tracker.get_recommended_delay_multiplier()
        assert mult > 1.0  # Should increase delays

    @pytest.mark.asyncio
    async def test_get_best_hours_empty(self, tracker):
        hours = await tracker.get_best_hours()
        assert hours == []

    @pytest.mark.asyncio
    async def test_get_best_hours_sorted(self, tracker):
        # Hour 10: 90% success
        for _ in range(9):
            await tracker.record_timing(10, success=True)
        await tracker.record_timing(10, success=False)

        # Hour 2: 30% success
        for _ in range(3):
            await tracker.record_timing(2, success=True)
        for _ in range(7):
            await tracker.record_timing(2, success=False)

        hours = await tracker.get_best_hours()
        assert hours[0] == 10
        assert hours[1] == 2

    @pytest.mark.asyncio
    async def test_get_strategy_summary(self, tracker):
        await tracker.record_engine_result("nodriver", success=True, duration_sec=3.0)
        summary = await tracker.get_strategy_summary()

        assert "best_engine" in summary
        assert "engines" in summary
        assert "delay_multiplier" in summary
        assert "best_hours" in summary
        assert summary["engines"]["nodriver"]["total"] == 1

    @pytest.mark.asyncio
    async def test_get_provider_health(self, tracker):
        await tracker.record_variant_result("v1", "capsolver", success=True)
        await tracker.record_variant_result("v1", "capsolver", success=False)

        health = await tracker.get_provider_health("capsolver")
        assert health.total == 2
        assert health.success_rate == 0.5

    @pytest.mark.asyncio
    async def test_accumulates_stats_over_calls(self, tracker, redis):
        await tracker.record_engine_result("nodriver", success=True, duration_sec=2.0)
        await tracker.record_engine_result("nodriver", success=True, duration_sec=4.0)
        await tracker.record_engine_result("nodriver", success=False)

        raw = await redis.get(f"{_ENGINE_KEY}:nodriver")
        data = json.loads(raw)
        assert data["successes"] == 2
        assert data["failures"] == 1
        assert data["total_duration"] == 6.0
        assert data["duration_count"] == 2
