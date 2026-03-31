"""Tests for F015: session agent must not be overwritten by API calls.

The execute and stream endpoints used to unconditionally persist body.agent
to the session, silently changing the user's agent mid-conversation.  After
the fix, only /agent set (slash command) and session creation may persist
agent changes.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")


class TestSessionAgentGuard(unittest.TestCase):
    """Verify that execute/stream endpoints do NOT overwrite session agent."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch as _patch

        from fastapi.testclient import TestClient

        import agent_manager

        cls._telegram_patch = _patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = _patch.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        cls._send_pairing_patch.start()
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.shared_header = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    def _create_session(self, agent="orchestrator"):
        """Helper: create a session with a specific agent and return its ID."""
        resp = self.client.post(
            "/api/v1/sessions/create",
            json={"agent": agent},
            headers=self.shared_header,
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["session_id"]

    def _get_session_agent(self, session_id):
        """Helper: read the agent field from the session status endpoint."""
        resp = self.client.get(
            f"/api/v1/sessions/{session_id}/status",
            headers=self.shared_header,
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json().get("agent")

    # ------------------------------------------------------------------
    # Test: session creation correctly persists agent
    # ------------------------------------------------------------------
    def test_create_session_persists_agent(self):
        """Session creation SHOULD persist the requested agent."""
        sid = self._create_session(agent="research")
        self.assertEqual(self._get_session_agent(sid), "research")

    # ------------------------------------------------------------------
    # Test: execute endpoint does NOT overwrite agent
    # ------------------------------------------------------------------
    def test_execute_does_not_overwrite_agent(self):
        """POST /execute with body.agent must NOT change session agent."""
        sid = self._create_session(agent="orchestrator")
        self.assertEqual(self._get_session_agent(sid), "orchestrator")

        # Patch SessionManager.execute to avoid spawning a real subprocess
        import agent_manager

        sm_cls = agent_manager.SessionManager
        with patch.object(sm_cls, "execute", return_value="mocked response"):
            resp = self.client.post(
                f"/api/v1/sessions/{sid}/execute",
                json={"query": "hello", "agent": "wee-dev"},
                headers=self.shared_header,
            )
            self.assertEqual(resp.status_code, 200)

        # Agent should still be the original
        self.assertEqual(
            self._get_session_agent(sid),
            "orchestrator",
            "execute endpoint must NOT overwrite session agent",
        )

    # ------------------------------------------------------------------
    # Test: stream endpoint does NOT overwrite agent
    # ------------------------------------------------------------------
    def test_stream_does_not_overwrite_agent(self):
        """POST /stream with body.agent must NOT change session agent."""
        sid = self._create_session(agent="orchestrator")
        self.assertEqual(self._get_session_agent(sid), "orchestrator")

        import agent_manager

        sm_cls = agent_manager.SessionManager
        with patch.object(sm_cls, "execute", return_value="mocked streaming"):
            resp = self.client.post(
                f"/api/v1/sessions/{sid}/stream",
                json={"query": "hello", "agent": "research"},
                headers=self.shared_header,
            )
            self.assertEqual(resp.status_code, 200)

        # Agent should still be the original
        self.assertEqual(
            self._get_session_agent(sid),
            "orchestrator",
            "stream endpoint must NOT overwrite session agent",
        )

    # ------------------------------------------------------------------
    # Test: execute endpoint without body.agent leaves agent unchanged
    # ------------------------------------------------------------------
    def test_execute_without_agent_field_is_fine(self):
        """POST /execute without body.agent should leave session agent alone."""
        sid = self._create_session(agent="devops")
        self.assertEqual(self._get_session_agent(sid), "devops")

        import agent_manager

        sm_cls = agent_manager.SessionManager
        with patch.object(sm_cls, "execute", return_value="mocked"):
            resp = self.client.post(
                f"/api/v1/sessions/{sid}/execute",
                json={"query": "hello"},
                headers=self.shared_header,
            )
            self.assertEqual(resp.status_code, 200)

        self.assertEqual(self._get_session_agent(sid), "devops")

    # ------------------------------------------------------------------
    # Test: model override still works in execute
    # ------------------------------------------------------------------
    def test_execute_model_override_still_works(self):
        """Model overrides via body.model should still be persisted."""
        sid = self._create_session(agent="orchestrator")

        import agent_manager

        sm_cls = agent_manager.SessionManager
        with patch.object(sm_cls, "execute", return_value="mocked"):
            resp = self.client.post(
                f"/api/v1/sessions/{sid}/execute",
                json={"query": "hello", "model": "claude-haiku-4.5"},
                headers=self.shared_header,
            )
            self.assertEqual(resp.status_code, 200)

        # Model should have been updated — check via status endpoint
        status = self.client.get(
            f"/api/v1/sessions/{sid}/status",
            headers=self.shared_header,
        ).json()
        model = status.get("model", "")
        self.assertTrue(
            len(model) > 0,
            "Model override should still be persisted after F015 fix",
        )

    # ------------------------------------------------------------------
    # Test: multiple execute calls with different agents don't change it
    # ------------------------------------------------------------------
    def test_multiple_executes_with_different_agents(self):
        """Sending varying body.agent across requests must NOT change session."""
        sid = self._create_session(agent="orchestrator")

        import agent_manager

        sm_cls = agent_manager.SessionManager
        agents_to_try = ["wee-dev", "research", "devops", "smarthome"]
        for agent_name in agents_to_try:
            with patch.object(sm_cls, "execute", return_value="mocked"):
                self.client.post(
                    f"/api/v1/sessions/{sid}/execute",
                    json={"query": "hello", "agent": agent_name},
                    headers=self.shared_header,
                )

        # Session agent should still be "orchestrator"
        self.assertEqual(
            self._get_session_agent(sid),
            "orchestrator",
            "Session agent should not change after multiple execute calls",
        )

    # ------------------------------------------------------------------
    # Test: multiple stream calls with different agents don't change it
    # ------------------------------------------------------------------
    def test_multiple_streams_with_different_agents(self):
        """Sending varying body.agent across stream requests must NOT change."""
        sid = self._create_session(agent="orchestrator")

        import agent_manager

        sm_cls = agent_manager.SessionManager
        agents_to_try = ["wee-dev", "research", "devops"]
        for agent_name in agents_to_try:
            with patch.object(sm_cls, "execute", return_value="mocked"):
                self.client.post(
                    f"/api/v1/sessions/{sid}/stream",
                    json={"query": "hello", "agent": agent_name},
                    headers=self.shared_header,
                )

        self.assertEqual(
            self._get_session_agent(sid),
            "orchestrator",
            "Session agent should not change after multiple stream calls",
        )


if __name__ == "__main__":
    unittest.main()
