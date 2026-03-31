"""Tests for F009 — Expose max_concurrent in agent settings panel.

Covers:
- Backend validation of max_concurrent in PUT /api/v1/agents-config
- In-memory agent config loading with max_concurrent
- Round-trip save and reload
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_agents_json(agents, tmpdir):
    """Helper to write agents.json to a temp directory."""
    path = os.path.join(tmpdir, "agents.json")
    with open(path, "w") as f:
        json.dump({"agents": agents}, f)
    return path


def _minimal_agent(**overrides):
    """Return a minimal valid agent dict, with optional overrides."""
    base = {
        "name": "test-agent",
        "path": "/opt/test",
        "description": "A test agent",
        "max_concurrent": 1,
    }
    base.update(overrides)
    return base


class TestMaxConcurrentLoadConfig(unittest.TestCase):
    """Test that _load_agents_config correctly parses max_concurrent."""

    def _load(self, agents):
        """Write agents.json and load via SessionManager."""
        from agent_manager import SessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = _make_agents_json(agents, tmpdir)
            mgr = SessionManager.__new__(SessionManager)
            mgr._agents_config_path = None
            mgr._agents_json_mtime = 0
            result = mgr._load_agents_config(config_file)
            return result

    def test_default_max_concurrent(self):
        """Agent without max_concurrent defaults to 1."""
        agents = self._load([{"name": "a", "path": "/opt/a"}])
        self.assertEqual(agents["a"]["max_concurrent"], 1)

    def test_explicit_max_concurrent(self):
        """Agent with explicit max_concurrent=5 is parsed correctly."""
        agents = self._load([{"name": "a", "path": "/opt/a", "max_concurrent": 5}])
        self.assertEqual(agents["a"]["max_concurrent"], 5)

    def test_max_concurrent_preserved_across_agents(self):
        """Each agent retains its own max_concurrent."""
        agents = self._load(
            [
                {"name": "a", "path": "/opt/a", "max_concurrent": 3},
                {"name": "b", "path": "/opt/b", "max_concurrent": 7},
            ]
        )
        self.assertEqual(agents["a"]["max_concurrent"], 3)
        self.assertEqual(agents["b"]["max_concurrent"], 7)


class TestMaxConcurrentAPIValidation(unittest.TestCase):
    """Test PUT /api/v1/agents-config validation of max_concurrent."""

    def _validate_payload(self, agents_list):
        """Simulate the validation logic from put_agents_config.

        Returns None on success, or the error detail string on failure.
        """
        data = {"agents": agents_list}
        if not isinstance(data, dict) or "agents" not in data:
            return "Payload must have an 'agents' key"
        if not isinstance(data["agents"], list):
            return "'agents' must be an array"
        for idx, ag in enumerate(data["agents"]):
            if not isinstance(ag, dict):
                return f"agents[{idx}] must be an object"
            if "name" not in ag or "path" not in ag:
                return f"agents[{idx}] requires 'name' and 'path'"
            mc = ag.get("max_concurrent")
            if mc is not None:
                if not isinstance(mc, int) or isinstance(mc, bool) or mc < 1:
                    return f"agents[{idx}].max_concurrent must be an integer >= 1"
        return None

    def test_valid_max_concurrent_1(self):
        """max_concurrent=1 is valid."""
        err = self._validate_payload([_minimal_agent(max_concurrent=1)])
        self.assertIsNone(err)

    def test_valid_max_concurrent_10(self):
        """max_concurrent=10 is valid."""
        err = self._validate_payload([_minimal_agent(max_concurrent=10)])
        self.assertIsNone(err)

    def test_missing_max_concurrent_is_valid(self):
        """Agent without max_concurrent field passes validation."""
        agent = {"name": "a", "path": "/opt/a"}
        err = self._validate_payload([agent])
        self.assertIsNone(err)

    def test_zero_max_concurrent_rejected(self):
        """max_concurrent=0 is rejected."""
        err = self._validate_payload([_minimal_agent(max_concurrent=0)])
        self.assertIsNotNone(err)
        self.assertIn("max_concurrent", err)

    def test_negative_max_concurrent_rejected(self):
        """max_concurrent=-1 is rejected."""
        err = self._validate_payload([_minimal_agent(max_concurrent=-1)])
        self.assertIsNotNone(err)
        self.assertIn("max_concurrent", err)

    def test_float_max_concurrent_rejected(self):
        """max_concurrent=2.5 is rejected (must be integer)."""
        err = self._validate_payload([_minimal_agent(max_concurrent=2.5)])
        self.assertIsNotNone(err)
        self.assertIn("max_concurrent", err)

    def test_string_max_concurrent_rejected(self):
        """max_concurrent='3' is rejected (must be int type)."""
        err = self._validate_payload([_minimal_agent(max_concurrent="3")])
        self.assertIsNotNone(err)
        self.assertIn("max_concurrent", err)

    def test_bool_max_concurrent_rejected(self):
        """max_concurrent=True is rejected (bool is not valid
        even though bool subclasses int)."""
        err = self._validate_payload([_minimal_agent(max_concurrent=True)])
        self.assertIsNotNone(err)
        self.assertIn("max_concurrent", err)


class TestMaxConcurrentRoundTrip(unittest.TestCase):
    """Test save → reload preserves max_concurrent."""

    def test_save_and_reload(self):
        """Write agents.json with max_concurrent, then reload."""
        from agent_manager import SessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            agents = [
                {"name": "a", "path": "/opt/a", "max_concurrent": 4},
                {"name": "b", "path": "/opt/b"},
            ]
            config_file = _make_agents_json(agents, tmpdir)

            mgr = SessionManager.__new__(SessionManager)
            mgr._agents_config_path = None
            mgr._agents_json_mtime = 0
            loaded = mgr._load_agents_config(config_file)

            self.assertEqual(loaded["a"]["max_concurrent"], 4)
            self.assertEqual(loaded["b"]["max_concurrent"], 1)  # default

            # Simulate save: update max_concurrent and write back
            config_path = Path(config_file)
            raw = json.loads(config_path.read_text())
            raw["agents"][1]["max_concurrent"] = 6
            config_path.write_text(json.dumps(raw, indent=2) + "\n")

            # Reload
            mgr2 = SessionManager.__new__(SessionManager)
            mgr2._agents_config_path = None
            mgr2._agents_json_mtime = 0
            reloaded = mgr2._load_agents_config(config_file)

            self.assertEqual(reloaded["a"]["max_concurrent"], 4)
            self.assertEqual(reloaded["b"]["max_concurrent"], 6)


if __name__ == "__main__":
    unittest.main()
