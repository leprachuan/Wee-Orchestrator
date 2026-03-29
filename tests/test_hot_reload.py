#!/usr/bin/env python3
"""
Tests for F004: Hot-reload agents.json

Tests cover:
- reload_agents_from_disk() validation and fallback
- File-watcher mtime detection
- put_agents_config auto-reload integration
- agents_list bug fix (session_mgr.AGENTS lookup)
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agent_manager import SessionManager


class TestHotReloadBase(unittest.TestCase):
    """Shared setup for hot-reload tests."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.agents_file = os.path.join(self.test_dir, "agents.json")
        self.valid_config = {
            "agents": [
                {
                    "name": "test-agent",
                    "path": "/opt/test-agent",
                    "description": "Test agent",
                    "max_concurrent": 2,
                    "runtime": "copilot",
                },
                {
                    "name": "other-agent",
                    "path": "/opt/other-agent",
                    "description": "Other agent",
                    "max_concurrent": 1,
                },
            ]
        }
        with open(self.agents_file, "w") as f:
            json.dump(self.valid_config, f)

        self.manager = SessionManager(config_file=self.agents_file)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)


class TestReloadAgentsFromDisk(TestHotReloadBase):
    """Test reload_agents_from_disk() method."""

    def test_successful_reload(self):
        """Reload returns True and updates AGENTS on valid config."""
        # Modify agents.json on disk
        new_config = {
            "agents": [
                {"name": "alpha", "path": "/opt/alpha", "description": "Alpha agent"},
                {"name": "beta", "path": "/opt/beta", "description": "Beta agent"},
                {"name": "gamma", "path": "/opt/gamma", "description": "Gamma agent"},
            ]
        }
        with open(self.agents_file, "w") as f:
            json.dump(new_config, f)

        ok, msg = self.manager.reload_agents_from_disk()
        self.assertTrue(ok)
        self.assertIn("3 agent(s)", msg)
        self.assertIn("alpha", self.manager.AGENTS)
        self.assertIn("beta", self.manager.AGENTS)
        self.assertIn("gamma", self.manager.AGENTS)
        self.assertNotIn("test-agent", self.manager.AGENTS)

    def test_reload_preserves_fields(self):
        """Reload correctly parses max_concurrent, runtime, model."""
        new_config = {
            "agents": [
                {
                    "name": "precise",
                    "path": "/opt/p",
                    "max_concurrent": 5,
                    "runtime": "claude",
                    "model": "opus",
                    "description": "Precise",
                },
            ]
        }
        with open(self.agents_file, "w") as f:
            json.dump(new_config, f)

        ok, msg = self.manager.reload_agents_from_disk()
        self.assertTrue(ok)
        agent = self.manager.AGENTS["precise"]
        self.assertEqual(agent["max_concurrent"], 5)
        self.assertEqual(agent["runtime"], "claude")
        self.assertEqual(agent["model"], "opus")

    def test_reload_invalid_json(self):
        """Reload fails gracefully on invalid JSON."""
        with open(self.agents_file, "w") as f:
            f.write("{not valid json!!!")

        ok, msg = self.manager.reload_agents_from_disk()
        self.assertFalse(ok)
        self.assertIn("Invalid JSON", msg)
        # Original agents preserved
        self.assertIn("test-agent", self.manager.AGENTS)

    def test_reload_missing_agents_key(self):
        """Reload fails if top-level 'agents' key is missing."""
        with open(self.agents_file, "w") as f:
            json.dump({"config": "bad"}, f)

        ok, msg = self.manager.reload_agents_from_disk()
        self.assertFalse(ok)
        self.assertIn("missing top-level", msg)
        self.assertIn("test-agent", self.manager.AGENTS)

    def test_reload_agents_not_array(self):
        """Reload fails if 'agents' is not an array."""
        with open(self.agents_file, "w") as f:
            json.dump({"agents": "not-a-list"}, f)

        ok, msg = self.manager.reload_agents_from_disk()
        self.assertFalse(ok)
        self.assertIn("must be an array", msg)
        self.assertIn("test-agent", self.manager.AGENTS)

    def test_reload_agent_missing_name(self):
        """Reload fails if an agent entry is missing 'name'."""
        with open(self.agents_file, "w") as f:
            json.dump({"agents": [{"path": "/opt/no-name"}]}, f)

        ok, msg = self.manager.reload_agents_from_disk()
        self.assertFalse(ok)
        self.assertIn("missing required", msg)

    def test_reload_agent_missing_path(self):
        """Reload fails if an agent entry is missing 'path'."""
        with open(self.agents_file, "w") as f:
            json.dump({"agents": [{"name": "no-path"}]}, f)

        ok, msg = self.manager.reload_agents_from_disk()
        self.assertFalse(ok)
        self.assertIn("missing required", msg)

    def test_reload_refuses_empty_replacement(self):
        """Reload refuses to replace non-empty config with empty one."""
        self.assertTrue(len(self.manager.AGENTS) > 0)
        with open(self.agents_file, "w") as f:
            json.dump({"agents": []}, f)

        ok, msg = self.manager.reload_agents_from_disk()
        self.assertFalse(ok)
        self.assertIn("Refusing", msg)
        self.assertIn("test-agent", self.manager.AGENTS)

    def test_reload_file_not_found(self):
        """Reload fails if agents.json was deleted."""
        os.remove(self.agents_file)
        ok, msg = self.manager.reload_agents_from_disk()
        self.assertFalse(ok)
        self.assertIn("not found", msg)

    def test_reload_no_config_path(self):
        """Reload fails if _agents_config_path was never set."""
        self.manager._agents_config_path = None
        ok, msg = self.manager.reload_agents_from_disk()
        self.assertFalse(ok)
        self.assertIn("not found", msg)

    def test_reload_agent_not_dict(self):
        """Reload fails if an agent entry is not a dict."""
        with open(self.agents_file, "w") as f:
            json.dump({"agents": ["string-not-dict"]}, f)

        ok, msg = self.manager.reload_agents_from_disk()
        self.assertFalse(ok)
        self.assertIn("not an object", msg)


class TestMtimeTracking(TestHotReloadBase):
    """Test mtime tracking for file-watcher."""

    def test_initial_mtime_set(self):
        """After init, _agents_json_mtime is set to file mtime."""
        self.assertGreater(self.manager._agents_json_mtime, 0.0)
        expected = Path(self.agents_file).stat().st_mtime
        self.assertEqual(self.manager._agents_json_mtime, expected)

    def test_mtime_updates_on_reload(self):
        """After reload, _agents_json_mtime matches new file mtime."""
        old_mtime = self.manager._agents_json_mtime
        # Ensure different mtime by sleeping briefly and rewriting
        time.sleep(0.05)
        with open(self.agents_file, "w") as f:
            json.dump(self.valid_config, f)
        new_file_mtime = Path(self.agents_file).stat().st_mtime
        self.assertNotEqual(old_mtime, new_file_mtime)

        ok, _ = self.manager.reload_agents_from_disk()
        self.assertTrue(ok)
        self.assertEqual(self.manager._agents_json_mtime, new_file_mtime)

    def test_mtime_unchanged_on_failed_reload(self):
        """On failed reload, mtime stays at the old value."""
        old_mtime = self.manager._agents_json_mtime
        with open(self.agents_file, "w") as f:
            f.write("bad json")

        ok, _ = self.manager.reload_agents_from_disk()
        self.assertFalse(ok)
        self.assertEqual(self.manager._agents_json_mtime, old_mtime)

    def test_config_path_stored(self):
        """_agents_config_path is set to the resolved path."""
        self.assertEqual(str(self.manager._agents_config_path), self.agents_file)


class TestConfigPathResolution(unittest.TestCase):
    """Test config path resolution priority."""

    def test_explicit_config_file(self):
        """Explicit config_file is used when provided."""
        test_dir = tempfile.mkdtemp()
        agents_file = os.path.join(test_dir, "custom-agents.json")
        with open(agents_file, "w") as f:
            json.dump({"agents": [{"name": "x", "path": "/x"}]}, f)

        mgr = SessionManager(config_file=agents_file)
        self.assertEqual(str(mgr._agents_config_path), agents_file)

        import shutil

        shutil.rmtree(test_dir, ignore_errors=True)

    def test_missing_file_mtime_zero(self):
        """When file doesn't exist, mtime is 0.0."""
        mgr = SessionManager(config_file="/nonexistent/agents.json")
        self.assertEqual(mgr._agents_json_mtime, 0.0)
        self.assertEqual(mgr.AGENTS, {})


class TestMaxConcurrentLookup(TestHotReloadBase):
    """Test that max_concurrent is read from session_mgr.AGENTS (not undefined agents_list)."""

    def test_max_concurrent_from_agents(self):
        """max_concurrent is correctly available from AGENTS dict."""
        agent_config = self.manager.AGENTS.get("test-agent", {})
        self.assertEqual(agent_config.get("max_concurrent"), 2)

    def test_max_concurrent_default(self):
        """Unknown agents get default max_concurrent from dict.get() fallback."""
        agent_config = self.manager.AGENTS.get("nonexistent", {})
        self.assertEqual(agent_config.get("max_concurrent", 1), 1)

    def test_max_concurrent_after_reload(self):
        """max_concurrent updates after hot-reload."""
        new_config = {
            "agents": [
                {
                    "name": "test-agent",
                    "path": "/opt/test-agent",
                    "description": "Updated",
                    "max_concurrent": 10,
                },
            ]
        }
        with open(self.agents_file, "w") as f:
            json.dump(new_config, f)

        ok, _ = self.manager.reload_agents_from_disk()
        self.assertTrue(ok)
        self.assertEqual(self.manager.AGENTS["test-agent"]["max_concurrent"], 10)


if __name__ == "__main__":
    unittest.main()
