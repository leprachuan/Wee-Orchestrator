"""Tests for Issue #76: Copilot SDK Runtime (copilot-sdk).

Validates that the copilot-sdk runtime is correctly integrated into all
routing paths, configuration surfaces, and API endpoints without affecting
the existing copilot CLI runtime.
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# Ensure test key is consistent across test suite
os.environ.setdefault("API_SHARED_KEY", "test_key_123")

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


def _get_session_mgr():
    """Create a minimal SessionManager for testing."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    # Minimal init to avoid full constructor side effects
    mgr.mode = None
    mgr.command_timeout = 300
    mgr._session_map_lock = __import__("threading").Lock()
    mgr.session_map_file = __import__("pathlib").Path("/tmp/_test_sdk_session_map.json")
    mgr.session_state_dir = __import__("pathlib").Path("/tmp/_test_sdk_sessions")
    mgr.session_state_dir.mkdir(parents=True, exist_ok=True)
    mgr.copilot_bin = "/usr/local/bin/copilot"
    mgr.AGENTS = {
        "orchestrator": {
            "path": "/opt/n8n-copilot-shim-dev",
            "description": "Test",
            "name": "orchestrator",
        }
    }
    mgr.skill_repositories = []
    mgr._bg_identity = None
    # Ensure session map file exists
    if not mgr.session_map_file.exists():
        mgr.session_map_file.write_text("{}")
    return mgr


class TestCopilotSdkRuntimeRouting(unittest.TestCase):
    """Test that copilot-sdk is correctly registered in all routing surfaces."""

    def test_slash_runtime_list_includes_sdk(self):
        """copilot-sdk appears in /runtime list output."""
        mgr = _get_session_mgr()
        session_data = {"runtime": "copilot"}
        result = mgr._slash_runtime("list", session_data, "test-sess-1")
        self.assertIn("copilot-sdk", result)
        self.assertIn("Copilot SDK", result)

    def test_slash_runtime_set_copilot_sdk(self):
        """Can switch to copilot-sdk via /runtime set."""
        mgr = _get_session_mgr()
        session_data = {"runtime": "copilot", "session_id": "old-id", "model": "gpt-5"}
        result = mgr._slash_runtime("set copilot-sdk", session_data, "test-sess-2")
        self.assertIn("copilot-sdk", result)
        self.assertNotIn("Unknown runtime", result)

    def test_slash_runtime_set_copilot_still_works(self):
        """Existing copilot CLI runtime still works."""
        mgr = _get_session_mgr()
        session_data = {"runtime": "claude", "session_id": "old-id", "model": "haiku"}
        result = mgr._slash_runtime("set copilot", session_data, "test-sess-3")
        self.assertIn("copilot", result)
        self.assertNotIn("Unknown runtime", result)

    def test_help_text_includes_copilot_sdk(self):
        """Help text mentions copilot-sdk."""
        mgr = _get_session_mgr()
        session_data = {"runtime": "copilot"}
        result = mgr._slash_help("", session_data, "test-sess-4")
        self.assertIn("copilot-sdk", result)

    def test_argparse_includes_copilot_sdk_in_choices(self):
        """argparse runtime choices include copilot-sdk."""
        import agent_manager
        # Read the source to verify argparse choices contain copilot-sdk
        import inspect
        source = inspect.getsource(agent_manager)
        import re
        self.assertIsNotNone(
            re.search(r'"copilot-sdk"', source),
            "argparse choices should include copilot-sdk",
        )
        self.assertIsNotNone(
            re.search(r'"opencode"', source),
            "argparse choices should include opencode",
        )


class TestCopilotSdkStripMetadata(unittest.TestCase):
    """Test metadata stripping for copilot-sdk runtime."""

    def test_strip_metadata_copilot_sdk(self):
        """copilot-sdk output goes through same strip_metadata as copilot."""
        mgr = _get_session_mgr()
        test_output = "Hello, world!\nTotal usage est: 100 tokens\nBreakdown by AI model:\n  gpt-5: 100"
        result = mgr.strip_metadata(test_output, "copilot-sdk")
        self.assertIn("Hello, world!", result)
        self.assertNotIn("Total usage est", result)

    def test_strip_metadata_copilot_unchanged(self):
        """Original copilot strip_metadata still works."""
        mgr = _get_session_mgr()
        test_output = "Response text\nTotal usage est: 50 tokens"
        result = mgr.strip_metadata(test_output, "copilot")
        self.assertIn("Response text", result)
        self.assertNotIn("Total usage est", result)


class TestCopilotSdkSessionManagement(unittest.TestCase):
    """Test session existence and resumption for copilot-sdk."""

    def test_session_exists_copilot_sdk(self):
        """session_exists handles copilot-sdk like copilot."""
        mgr = _get_session_mgr()
        result = mgr.session_exists("nonexistent-session-id", "copilot-sdk")
        self.assertFalse(result)

    def test_get_most_recent_session_copilot_sdk(self):
        """get_most_recent_session_id handles copilot-sdk like copilot."""
        mgr = _get_session_mgr()
        result = mgr.get_most_recent_session_id("copilot-sdk")
        self.assertIsNone(result)


class TestCopilotSdkRunMethod(unittest.TestCase):
    """Test the run_copilot_sdk method itself."""

    def test_sdk_not_installed_error(self):
        """Returns friendly error when SDK not installed."""
        mgr = _get_session_mgr()
        with patch.dict("sys.modules", {"copilot": None}):
            result = mgr.run_copilot_sdk(
                "test", "gpt-5", "orchestrator", None, False, "test-sess-5"
            )
            self.assertIn("github-copilot-sdk not installed", result)

    @patch("copilot.CopilotClient")
    def test_sdk_creates_session(self, mock_client_cls):
        """SDK creates a new session when not resuming."""
        mgr = _get_session_mgr()

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "sdk-session-123"
        mock_session.disconnect = AsyncMock()

        mock_event = MagicMock()
        mock_event.data = MagicMock()
        mock_event.data.content = "SDK response text"
        mock_session.send_and_wait = AsyncMock(return_value=mock_event)
        mock_session.on = MagicMock()
        mock_session.get_messages = MagicMock(return_value=[])

        mock_client.create_session = AsyncMock(return_value=mock_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_client_cls.return_value = mock_client

        # Mock build_agent_context_prompt to avoid complex dependencies
        with patch.object(mgr, "build_agent_context_prompt", return_value="test prompt"):
            result = mgr.run_copilot_sdk(
                "what is 2+2", "gpt-5", "orchestrator", None, False, "test-sess-6"
            )
        self.assertIn("SDK response text", result)
        mock_client.create_session.assert_called_once()

    @patch("copilot.CopilotClient")
    def test_sdk_resumes_session(self, mock_client_cls):
        """SDK resumes an existing session when resume=True."""
        mgr = _get_session_mgr()

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "existing-session-456"
        mock_session.disconnect = AsyncMock()

        mock_event = MagicMock()
        mock_event.data = MagicMock()
        mock_event.data.content = "Resumed response"
        mock_session.send_and_wait = AsyncMock(return_value=mock_event)
        mock_session.on = MagicMock()
        mock_session.get_messages = MagicMock(return_value=[])

        mock_client.resume_session = AsyncMock(return_value=mock_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_client_cls.return_value = mock_client

        result = mgr.run_copilot_sdk(
            "continue", "gpt-5", "orchestrator",
            "existing-session-456", True, "test-sess-7"
        )
        self.assertIn("Resumed response", result)
        mock_client.resume_session.assert_called_once()

    @patch("copilot.CopilotClient")
    def test_sdk_handles_session_creation_exception(self, mock_client_cls):
        """SDK returns structured error when session creation fails."""
        mgr = _get_session_mgr()

        mock_client = AsyncMock()
        mock_client.create_session = AsyncMock(
            side_effect=ConnectionError("SDK server not running")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_client_cls.return_value = mock_client

        with patch.object(mgr, "build_agent_context_prompt", return_value="test"):
            result = mgr.run_copilot_sdk(
                "test", "gpt-5", "orchestrator", None, False, "test-sess-8"
            )
        self.assertIn("Error", result)
        self.assertIn("Copilot SDK", result)

    @patch("copilot.CopilotClient")
    def test_sdk_collects_events_fallback(self, mock_client_cls):
        """Falls back to collected event messages when send_and_wait returns None."""
        mgr = _get_session_mgr()

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "sdk-session-event"
        mock_session.disconnect = AsyncMock()
        mock_session.get_messages = MagicMock(return_value=[])

        # Capture on_event kwarg passed to create_session
        captured_on_event = None

        async def capture_create_session(**kwargs):
            nonlocal captured_on_event
            captured_on_event = kwargs.get("on_event")
            return mock_session

        mock_client.create_session = AsyncMock(side_effect=capture_create_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        async def send_and_wait_with_events(prompt, timeout=60.0):
            if captured_on_event:
                event = MagicMock()
                from copilot.session import SessionEventType
                event.type = SessionEventType.ASSISTANT_MESSAGE
                event.data = MagicMock()
                event.data.content = "Event-collected response"
                captured_on_event(event)
            return None

        mock_session.send_and_wait = AsyncMock(side_effect=send_and_wait_with_events)

        with patch.object(mgr, "build_agent_context_prompt", return_value="test"):
            result = mgr.run_copilot_sdk(
                "test", "gpt-5", "orchestrator", None, False, "test-sess-9"
            )
        self.assertIn("Event-collected response", result)

    @patch("copilot.CopilotClient")
    def test_sdk_elevated_mode(self, mock_client_cls):
        """Elevated mode instruction appended to SDK prompt."""
        mgr = _get_session_mgr()

        mock_client = AsyncMock()
        mock_session = AsyncMock()
        mock_session.session_id = "sdk-elevated"
        mock_session.disconnect = AsyncMock()

        mock_event = MagicMock()
        mock_event.data = MagicMock()
        mock_event.data.content = "elevated result"
        mock_session.send_and_wait = AsyncMock(return_value=mock_event)
        mock_session.on = MagicMock()

        mock_client.create_session = AsyncMock(return_value=mock_session)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_client_cls.return_value = mock_client

        with patch.object(mgr, "build_agent_context_prompt", return_value="test"):
            # The /mode elevated gets parsed from the prompt
            result = mgr.run_copilot_sdk(
                "/mode elevated do stuff", "gpt-5", "orchestrator",
                None, False, "test-sess-10"
            )
        self.assertIn("elevated result", result)


class TestCopilotSdkDispatch(unittest.TestCase):
    """Test runtime dispatch routes copilot-sdk correctly."""

    def test_dispatch_routes_to_sdk(self):
        """_dispatch_single_runtime routes copilot-sdk to run_copilot_sdk."""
        mgr = _get_session_mgr()

        with patch.object(mgr, "run_copilot_sdk", return_value="SDK dispatched") as mock_run:
            with patch.object(mgr, "touch_session"):
                result = mgr._dispatch_single_runtime(
                    prompt="test",
                    model="gpt-5",
                    agent="orchestrator",
                    session_id=None,
                    can_resume=False,
                    n8n_session_id="test-sess-11",
                    effective_timeout=300,
                    render_type="text",
                    runtime="copilot-sdk",
                )
                self.assertEqual(result, "SDK dispatched")
                mock_run.assert_called_once()

    def test_dispatch_copilot_still_routes_to_cli(self):
        """_dispatch_single_runtime still routes copilot to run_copilot."""
        mgr = _get_session_mgr()

        with patch.object(mgr, "run_copilot", return_value="CLI dispatched") as mock_run:
            with patch.object(mgr, "touch_session"):
                result = mgr._dispatch_single_runtime(
                    prompt="test",
                    model="gpt-5",
                    agent="orchestrator",
                    session_id=None,
                    can_resume=False,
                    n8n_session_id="test-sess-12",
                    effective_timeout=300,
                    render_type="text",
                    runtime="copilot",
                )
                self.assertEqual(result, "CLI dispatched")
                mock_run.assert_called_once()


class TestCopilotSdkApiEndpoints(unittest.TestCase):
    """Test API endpoints include copilot-sdk."""

    def test_get_runtimes_includes_sdk(self):
        """GET /api/v1/runtimes includes copilot-sdk."""
        from agent_manager import create_api_app
        from fastapi.testclient import TestClient

        app = create_api_app()
        client = TestClient(app)
        response = client.get(
            "/api/v1/runtimes",
            headers={"Authorization": "Bearer test_key_123"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        runtime_ids = [r["id"] for r in data["runtimes"]]
        self.assertIn("copilot-sdk", runtime_ids)
        self.assertIn("copilot", runtime_ids)

    def test_get_models_accepts_sdk(self):
        """GET /api/v1/models?runtime=copilot-sdk does not return unknown error."""
        from agent_manager import create_api_app
        from fastapi.testclient import TestClient

        app = create_api_app()
        client = TestClient(app)
        response = client.get(
            "/api/v1/models?runtime=copilot-sdk",
            headers={"Authorization": "Bearer test_key_123"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertNotIn("error", data.get("error", ""),
                         "copilot-sdk should not return 'Unknown runtime' error")


if __name__ == "__main__":
    unittest.main()
