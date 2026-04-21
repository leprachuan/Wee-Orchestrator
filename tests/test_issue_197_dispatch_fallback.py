"""Regression tests for Issue #197 — fallback_runtime / fallback_model dispatch.

These tests verify that:
- check_runtime_blocked() correctly parses RUNTIME_STATE.md
- resolve_dispatch_runtime_model() applies fallbacks when primary is blocked/unavailable
- No fallback is applied when primary is healthy
"""

import os
import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Bootstrap path so we can import from agent_manager without starting FastAPI
# ---------------------------------------------------------------------------
sys.path.insert(0, "/opt/n8n-copilot-shim-dev")  # noqa: E402

from unittest.mock import MagicMock, patch

import agent_manager


class TestCheckRuntimeBlocked(unittest.TestCase):
    """Unit tests for check_runtime_blocked()."""

    def _write_state(self, tmpdir, content):
        path = os.path.join(tmpdir, "RUNTIME_STATE.md")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_no_file_returns_false(self):
        result = agent_manager.check_runtime_blocked("copilot", "/nonexistent/path.md")
        self.assertFalse(result)

    def test_empty_incidents_returns_false(self):
        content = (
            "# Runtime State\n\n"
            "## Active Incidents\n\n"
            "| ID | Runtime/Service | Issue | Blocked Until | Written By | Fallback Action |\n"
            "|----|-----------------|-------|---------------|------------|----------------|\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = self._write_state(d, content)
            self.assertFalse(agent_manager.check_runtime_blocked("copilot", path))

    def test_blocked_runtime_detected(self):
        content = (
            "# Runtime State\n\n"
            "## Active Incidents\n\n"
            "| ID | Runtime/Service | Issue | Blocked Until | Written By | Fallback Action |\n"
            "|----|-----------------|-------|---------------|------------|----------------|\n"
            "| RS-001 | `copilot` | Rate limited | 2099-12-31 23:59 UTC | wee-dev | use claude-sdk |\n"  # noqa: E501
        )
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = self._write_state(d, content)
            self.assertTrue(agent_manager.check_runtime_blocked("copilot", path))

    def test_expired_incident_returns_false(self):
        content = (
            "# Runtime State\n\n"
            "## Active Incidents\n\n"
            "| ID | Runtime/Service | Issue | Blocked Until | Written By | Fallback Action |\n"
            "|----|-----------------|-------|---------------|------------|----------------|\n"
            "| RS-002 | `copilot` | Old issue | 2000-01-01 00:00 UTC | wee-dev | use claude-sdk |\n"  # noqa: E501
        )
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = self._write_state(d, content)
            self.assertFalse(agent_manager.check_runtime_blocked("copilot", path))

    def test_blank_blocked_until_treated_as_blocked(self):
        content = (
            "# Runtime State\n\n"
            "## Active Incidents\n\n"
            "| ID | Runtime/Service | Issue | Blocked Until | Written By | Fallback Action |\n"
            "|----|-----------------|-------|---------------|------------|----------------|\n"
            "| RS-003 | `claude-sdk` | Auth failure |  | wee-dev | use copilot |\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = self._write_state(d, content)
            self.assertTrue(agent_manager.check_runtime_blocked("claude-sdk", path))

    def test_different_runtime_not_matched(self):
        content = (
            "# Runtime State\n\n"
            "## Active Incidents\n\n"
            "| ID | Runtime/Service | Issue | Blocked Until | Written By | Fallback Action |\n"
            "|----|-----------------|-------|---------------|------------|----------------|\n"
            "| RS-004 | `claude-sdk` | Auth failure | 2099-01-01 00:00 UTC | wee-dev | use copilot |\n"  # noqa: E501
        )
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = self._write_state(d, content)
            self.assertFalse(agent_manager.check_runtime_blocked("copilot", path))

    def test_runtime_prefix_matching(self):
        content = (
            "# Runtime State\n\n"
            "## Active Incidents\n\n"
            "| ID | Runtime/Service | Issue | Blocked Until | Written By | Fallback Action |\n"
            "|----|-----------------|-------|---------------|------------|----------------|\n"
            "| RS-005 | `openrouter/:free` | Quota | 2099-01-01 00:00 UTC | wee-dev | use copilot |\n"  # noqa: E501
        )
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = self._write_state(d, content)
            # "openrouter" alone should match "openrouter/:free"
            self.assertTrue(agent_manager.check_runtime_blocked("openrouter", path))

    def test_backtick_stripped(self):
        content = (
            "# Runtime State\n\n"
            "## Active Incidents\n\n"
            "| ID | Runtime/Service | Issue | Blocked Until | Written By | Fallback Action |\n"
            "|----|-----------------|-------|---------------|------------|----------------|\n"
            "| RS-006 | copilot | Rate limit | 2099-01-01 00:00 UTC | wee-dev | - |\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = self._write_state(d, content)
            self.assertTrue(agent_manager.check_runtime_blocked("copilot", path))


class TestResolveDispatchRuntimeModel(unittest.TestCase):
    """Unit tests for resolve_dispatch_runtime_model()."""

    AGENTS_WITH_FALLBACK = {
        "wee-dev": {
            "dispatch_config": {
                "runtime": "claude-sdk",
                "model": "claude-sonnet-4.6",
                "fallback_runtime": "copilot",
                "fallback_model": "claude-haiku-4.5",
            }
        }
    }

    AGENTS_NO_FALLBACK = {
        "orchestrator": {
            "dispatch_config": {
                "runtime": "copilot",
                "model": "claude-sonnet-4.6",
            }
        }
    }

    def test_no_fallback_config_returns_original(self):
        rt, model, used, reason = agent_manager.resolve_dispatch_runtime_model(
            "orchestrator", "copilot", "claude-sonnet-4.6", self.AGENTS_NO_FALLBACK
        )
        self.assertEqual(rt, "copilot")
        self.assertFalse(used)

    def test_primary_healthy_no_fallback(self):
        with patch.object(agent_manager, "check_runtime_blocked", return_value=False), \
             patch.object(agent_manager, "check_runtime_available", return_value=True):
            rt, model, used, reason = agent_manager.resolve_dispatch_runtime_model(
                "wee-dev", "claude-sdk", "claude-sonnet-4.6", self.AGENTS_WITH_FALLBACK
            )
        self.assertEqual(rt, "claude-sdk")
        self.assertFalse(used)

    def test_blocked_runtime_triggers_fallback(self):
        with patch.object(agent_manager, "check_runtime_blocked", return_value=True):
            rt, model, used, reason = agent_manager.resolve_dispatch_runtime_model(
                "wee-dev", "claude-sdk", "claude-sonnet-4.6", self.AGENTS_WITH_FALLBACK
            )
        self.assertEqual(rt, "copilot")
        self.assertEqual(model, "claude-haiku-4.5")
        self.assertTrue(used)
        self.assertIn("blocked in RUNTIME_STATE.md", reason)

    def test_unavailable_runtime_triggers_fallback(self):
        with patch.object(agent_manager, "check_runtime_blocked", return_value=False), \
             patch.object(agent_manager, "check_runtime_available", return_value=False):
            rt, model, used, reason = agent_manager.resolve_dispatch_runtime_model(
                "wee-dev", "claude-sdk", "claude-sonnet-4.6", self.AGENTS_WITH_FALLBACK
            )
        self.assertEqual(rt, "copilot")
        self.assertTrue(used)
        self.assertIn("not available on this host", reason)

    def test_unknown_agent_returns_original(self):
        with patch.object(agent_manager, "check_runtime_blocked", return_value=False), \
             patch.object(agent_manager, "check_runtime_available", return_value=True):
            rt, model, used, reason = agent_manager.resolve_dispatch_runtime_model(
                "nonexistent-agent", "copilot", "gpt-5-mini", {}
            )
        self.assertEqual(rt, "copilot")
        self.assertFalse(used)

    def test_fallback_runtime_only_preserves_original_model(self):
        agents = {
            "my-agent": {
                "dispatch_config": {
                    "runtime": "claude-sdk",
                    "model": "sonnet",
                    "fallback_runtime": "copilot",
                    # No fallback_model — should preserve original
                }
            }
        }
        with patch.object(agent_manager, "check_runtime_blocked", return_value=True):
            rt, model, used, reason = agent_manager.resolve_dispatch_runtime_model(
                "my-agent", "claude-sdk", "sonnet", agents
            )
        self.assertEqual(rt, "copilot")
        self.assertEqual(model, "sonnet")  # original preserved
        self.assertTrue(used)

    def test_fallback_model_only_preserves_original_runtime(self):
        agents = {
            "my-agent": {
                "dispatch_config": {
                    "runtime": "claude-sdk",
                    "model": "opus",
                    "fallback_model": "haiku",
                    # No fallback_runtime — should preserve original
                }
            }
        }
        with patch.object(agent_manager, "check_runtime_blocked", return_value=True):
            rt, model, used, reason = agent_manager.resolve_dispatch_runtime_model(
                "my-agent", "claude-sdk", "opus", agents
            )
        self.assertEqual(rt, "claude-sdk")
        self.assertEqual(model, "haiku")
        self.assertTrue(used)


if __name__ == "__main__":
    unittest.main()
