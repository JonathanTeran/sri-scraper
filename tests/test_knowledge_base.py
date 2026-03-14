"""Tests for scrapers.knowledge_base and db.models.knowledge_base."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.models.knowledge_base import (
    BlockEvent,
    KnowledgeEntry,
    PatternCategory,
)


# ── Model unit tests (no DB required) ────────────────────────────────


class TestKnowledgeEntryModel:
    def _make_entry(self, **kwargs) -> KnowledgeEntry:
        defaults = dict(
            category=PatternCategory.ENGINE,
            key="nodriver",
            successes=0,
            failures=0,
            blocks=0,
            total_duration_sec=0.0,
            duration_count=0,
        )
        defaults.update(kwargs)
        return KnowledgeEntry(**defaults)

    def test_total(self):
        entry = self._make_entry(successes=7, failures=3)
        assert entry.total == 10

    def test_success_rate_no_data(self):
        entry = self._make_entry()
        assert entry.success_rate == 0.5

    def test_success_rate_with_data(self):
        entry = self._make_entry(successes=8, failures=2)
        assert entry.success_rate == pytest.approx(0.8)

    def test_avg_duration_no_data(self):
        entry = self._make_entry()
        assert entry.avg_duration == 0.0

    def test_avg_duration_with_data(self):
        entry = self._make_entry(total_duration_sec=30.0, duration_count=3)
        assert entry.avg_duration == pytest.approx(10.0)


class TestPatternCategory:
    def test_all_categories_exist(self):
        expected = {"engine", "captcha_variant", "captcha_provider", "timing", "block_signal", "stealth"}
        actual = {c.value for c in PatternCategory}
        assert actual == expected

    def test_enum_is_str(self):
        assert isinstance(PatternCategory.ENGINE, str)
        assert PatternCategory.ENGINE == "engine"


class TestBlockEventModel:
    def test_table_name(self):
        assert BlockEvent.__tablename__ == "sri_block_events"

    def test_knowledge_entry_table_name(self):
        assert KnowledgeEntry.__tablename__ == "sri_knowledge_base"
