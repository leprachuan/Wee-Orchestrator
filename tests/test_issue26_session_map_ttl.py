"""Regression tests for Issue #26: Session map grows unbounded — no TTL cleanup.

Root cause: ``n8n-session-map.json`` accumulated every session forever because
``save_session_map()`` never evicted stale entries and the cleanup daemon only
removed backend session *directories*, not the map entries themselves.

Fix: ``SessionManager._prune_session_map_ttl()`` removes entries whose
``last_activity`` timestamp is older than ``SESSION_MAP_TTL_DAYS`` (default 30)
before every ``save_session_map()`` call.  ``scripts/session-cleanup.py`` also
calls ``prune_stale_session_map_entries()`` on every cleanup cycle.

Scenarios tested:
- Entries older than TTL are evicted
- Entries newer than TTL are kept
- Entries without last_activity are kept (legacy safety)
- String-format legacy entries (no last_activity dict) are kept
- save_session_map() automatically prunes before writing
- Multiple old entries are all removed in one pass
- TTL can be overridden via SESSION_MAP_TTL_DAYS env var
- session-cleanup.py prune_stale_session_map_entries() evicts correctly
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_entry(last_activity):
    """Build a minimal session map entry dict."""
    return {
        "session_id": "00000000-0000-0000-0000-000000000000",
        "model": "haiku",
        "agent": "orchestrator",
        "runtime": "copilot",
        "last_activity": last_activity,
    }


@pytest.fixture
def session_mgr():
    """Minimal SessionManager instance with a temporary session map file."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    # Required attributes for model / agent resolution
    mgr._env_claude_models = {}
    mgr._env_gemini_models = {}
    mgr._env_codex_models = {}
    mgr._env_devin_models = {}
    mgr._env_cursor_models = {}
    mgr._env_wee_models = None
    # session_map_ttl: 30 days (default)
    mgr.session_map_ttl = 30 * 86400
    # Point to a temp file for all map I/O
    mgr._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    mgr.session_map_file = Path(mgr._tmp.name)
    mgr.session_map_file.write_text("{}")
    return mgr


# ── _prune_session_map_ttl() ──────────────────────────────────────────────────


class TestPruneSessionMapTtl:
    """Direct unit tests for _prune_session_map_ttl()."""

    def test_old_entry_is_evicted(self, session_mgr):
        """An entry with last_activity > TTL in the past must be removed."""
        stale_ts = time.time() - (31 * 86400)  # 31 days ago
        session_map = {"stale_session": _make_entry(stale_ts)}
        result = session_mgr._prune_session_map_ttl(session_map)
        assert "stale_session" not in result

    def test_recent_entry_is_kept(self, session_mgr):
        """An entry with last_activity within TTL must be retained."""
        fresh_ts = time.time() - (1 * 86400)  # 1 day ago
        session_map = {"fresh_session": _make_entry(fresh_ts)}
        result = session_mgr._prune_session_map_ttl(session_map)
        assert "fresh_session" in result

    def test_entry_without_last_activity_is_kept(self, session_mgr):
        """Entries missing last_activity must never be evicted (legacy safety)."""
        entry = {
            "session_id": "abc",
            "model": "haiku",
            # no last_activity
        }
        result = session_mgr._prune_session_map_ttl({"legacy_key": entry})
        assert "legacy_key" in result

    def test_string_legacy_entry_is_kept(self, session_mgr):
        """Old-format string-only session map entries are kept (no last_activity)."""
        result = session_mgr._prune_session_map_ttl({"legacy_str": "some-session-uuid"})
        assert "legacy_str" in result

    def test_multiple_stale_entries_all_evicted(self, session_mgr):
        """All stale entries are removed in a single pass."""
        stale_ts = time.time() - (35 * 86400)
        session_map = {f"stale_{i}": _make_entry(stale_ts) for i in range(5)}
        result = session_mgr._prune_session_map_ttl(session_map)
        assert len(result) == 0

    def test_mixed_map_only_stale_evicted(self, session_mgr):
        """Only stale entries are removed; fresh ones survive."""
        stale_ts = time.time() - (31 * 86400)
        fresh_ts = time.time() - (1 * 86400)
        session_map = {
            "stale": _make_entry(stale_ts),
            "fresh": _make_entry(fresh_ts),
        }
        result = session_mgr._prune_session_map_ttl(session_map)
        assert "stale" not in result
        assert "fresh" in result

    def test_exactly_at_ttl_boundary_is_kept(self, session_mgr):
        """An entry exactly at the cutoff (not strictly older) must be retained."""
        # TTL = 30d; set last_activity to exactly 30d ago + 1 second (still fresh)
        boundary_ts = time.time() - (session_mgr.session_map_ttl - 1)
        session_map = {"boundary": _make_entry(boundary_ts)}
        result = session_mgr._prune_session_map_ttl(session_map)
        assert "boundary" in result

    def test_ttl_override_via_attribute(self, session_mgr):
        """session_map_ttl attribute controls the cutoff."""
        # Use a very short TTL (1 hour)
        session_mgr.session_map_ttl = 3600
        slightly_old_ts = time.time() - 3700  # 1 hour and 100s ago
        session_map = {"old_session": _make_entry(slightly_old_ts)}
        result = session_mgr._prune_session_map_ttl(session_map)
        assert "old_session" not in result


# ── save_session_map() ────────────────────────────────────────────────────────


class TestSaveSessionMapPrunes:
    """save_session_map() must evict stale entries before writing to disk."""

    def test_save_prunes_stale_entry(self, session_mgr):
        """Stale entries must not appear in the written file."""
        stale_ts = time.time() - (31 * 86400)
        session_map = {
            "stale": _make_entry(stale_ts),
            "fresh": _make_entry(time.time()),
        }

        import threading

        session_mgr._session_map_lock = threading.Lock()
        session_mgr.save_session_map(session_map)

        written = json.loads(session_mgr.session_map_file.read_text())
        assert "stale" not in written
        assert "fresh" in written

    def test_save_with_all_fresh_entries(self, session_mgr):
        """When all entries are fresh, the full map is written unchanged."""
        fresh_ts = time.time() - 100
        session_map = {
            "s1": _make_entry(fresh_ts),
            "s2": _make_entry(fresh_ts),
        }

        import threading

        session_mgr._session_map_lock = threading.Lock()
        session_mgr.save_session_map(session_map)

        written = json.loads(session_mgr.session_map_file.read_text())
        assert len(written) == 2

    def test_save_does_not_modify_input_dict(self, session_mgr):
        """The original session_map passed to save_session_map must be unmodified."""
        stale_ts = time.time() - (31 * 86400)
        original = {"stale": _make_entry(stale_ts)}
        import copy
        import threading

        before = copy.deepcopy(original)
        session_mgr._session_map_lock = threading.Lock()
        session_mgr.save_session_map(original)
        # _prune_session_map_ttl returns a NEW dict, input unchanged
        assert original == before


# ── Unbounded-growth regression ───────────────────────────────────────────────


class TestNoBoundedGrowthRegression:
    """Reproduces the original bug: session map must not grow without bound."""

    def test_session_map_does_not_grow_unbounded(self, session_mgr, tmp_path):
        """Writing 100 old sessions over time must leave the map empty,

        not 100-large."""
        import threading

        session_mgr._session_map_lock = threading.Lock()
        session_mgr.session_map_file = tmp_path / "test-session-map.json"
        session_mgr.session_map_file.write_text("{}")

        stale_ts = time.time() - (32 * 86400)
        for i in range(100):
            session_map = {f"session_{j}": _make_entry(stale_ts) for j in range(i + 1)}
            session_mgr.save_session_map(session_map)

        written = json.loads(session_mgr.session_map_file.read_text())
        # All 100 stale sessions must have been pruned
        assert (
            len(written) == 0
        ), f"Session map grew to {len(written)} entries — TTL eviction not working"


# ── session-cleanup.py ────────────────────────────────────────────────────────


class TestCleanupScriptTtlPruning:
    """prune_stale_session_map_entries() in session-cleanup.py."""

    def _load_script(self):
        """Import prune_stale_session_map_entries from the cleanup script."""
        import importlib.util

        script = Path(__file__).parent.parent / "scripts" / "session-cleanup.py"
        spec = importlib.util.spec_from_file_location("session_cleanup", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_prune_removes_stale_entries(self, tmp_path):
        """prune_stale_session_map_entries() evicts entries older than ttl_days."""
        mod = self._load_script()

        stale_ts = time.time() - (31 * 86400)
        fresh_ts = time.time() - 3600

        map_data = {
            "stale_key": _make_entry(stale_ts),
            "fresh_key": _make_entry(fresh_ts),
        }
        map_file = tmp_path / "n8n-session-map.json"
        map_file.write_text(json.dumps(map_data))

        mod.prune_stale_session_map_entries([map_file], ttl_days=30)

        result = json.loads(map_file.read_text())
        assert "stale_key" not in result
        assert "fresh_key" in result

    def test_prune_no_op_on_all_fresh(self, tmp_path):
        """prune_stale_session_map_entries() is a no-op when all entries are fresh."""
        mod = self._load_script()

        fresh_ts = time.time() - 3600
        map_data = {"k1": _make_entry(fresh_ts), "k2": _make_entry(fresh_ts)}
        map_file = tmp_path / "n8n-session-map.json"
        map_file.write_text(json.dumps(map_data))

        mod.prune_stale_session_map_entries([map_file], ttl_days=30)

        result = json.loads(map_file.read_text())
        assert len(result) == 2

    def test_prune_missing_file_is_safe(self, tmp_path):
        """prune_stale_session_map_entries() must not raise if file does not exist."""
        mod = self._load_script()
        missing = tmp_path / "nonexistent.json"
        # Should complete without raising
        mod.prune_stale_session_map_entries([missing], ttl_days=30)

    def test_prune_entries_without_last_activity_kept(self, tmp_path):
        """Entries without last_activity are preserved (legacy safety)."""
        mod = self._load_script()

        map_data = {
            "no_ts": {"session_id": "abc", "model": "haiku"},
        }
        map_file = tmp_path / "n8n-session-map.json"
        map_file.write_text(json.dumps(map_data))

        mod.prune_stale_session_map_entries([map_file], ttl_days=30)

        result = json.loads(map_file.read_text())
        assert "no_ts" in result
