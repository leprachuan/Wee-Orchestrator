"""Regression tests for GitHub Issue #200 - auth gate on /api/v1/agents
and /api/v1/runtimes.

wee-qa blocker: GET /api/v1/agents and GET /api/v1/runtimes returned HTTP 200 to
unauthenticated callers. Both endpoints must now require a valid Bearer token,
matching the behaviour of /api/v1/models.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


class TestIssue200AgentsRuntimesAuth(unittest.TestCase):
    """GET /api/v1/agents and /api/v1/runtimes must require authentication."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import agent_manager

        cls._telegram_patch = patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = patch.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        cls._send_pairing_patch.start()
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.auth_header = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    # ---------- /api/v1/agents ----------

    def test_agents_unauthenticated_rejected(self):
        """GET /api/v1/agents without auth must return HTTP 401."""
        resp = self.client.get("/api/v1/agents")
        self.assertIn(
            resp.status_code,
            (401, 403),
            f"Expected 401/403 for unauthenticated /api/v1/agents, "
            f"got {resp.status_code}",
        )

    def test_agents_authenticated_ok(self):
        """GET /api/v1/agents with a valid Bearer token must return HTTP 200."""
        resp = self.client.get("/api/v1/agents", headers=self.auth_header)
        self.assertEqual(
            resp.status_code,
            200,
            f"Authenticated /api/v1/agents should return 200, got {resp.status_code}",
        )
        body = resp.json()
        self.assertIn("agents", body, "Response must contain 'agents' key")

    def test_agents_wrong_token_rejected(self):
        """GET /api/v1/agents with a wrong token must return HTTP 401/403."""
        resp = self.client.get(
            "/api/v1/agents",
            headers={"Authorization": "Bearer wrong_token"},
        )
        self.assertIn(
            resp.status_code,
            (401, 403),
            f"Wrong token should be rejected, got {resp.status_code}",
        )

    # ---------- /api/v1/runtimes ----------

    def test_runtimes_unauthenticated_rejected(self):
        """GET /api/v1/runtimes without auth must return HTTP 401."""
        resp = self.client.get("/api/v1/runtimes")
        self.assertIn(
            resp.status_code,
            (401, 403),
            f"Expected 401/403 for unauthenticated /api/v1/runtimes, "
            f"got {resp.status_code}",
        )

    def test_runtimes_authenticated_ok(self):
        """GET /api/v1/runtimes with a valid Bearer token must return HTTP 200."""
        resp = self.client.get("/api/v1/runtimes", headers=self.auth_header)
        self.assertEqual(
            resp.status_code,
            200,
            f"Authenticated /api/v1/runtimes should return 200, got {resp.status_code}",
        )
        body = resp.json()
        self.assertIn("runtimes", body, "Response must contain 'runtimes' key")

    def test_runtimes_wrong_token_rejected(self):
        """GET /api/v1/runtimes with a wrong token must return HTTP 401/403."""
        resp = self.client.get(
            "/api/v1/runtimes",
            headers={"Authorization": "Bearer wrong_token"},
        )
        self.assertIn(
            resp.status_code,
            (401, 403),
            f"Wrong token should be rejected, got {resp.status_code}",
        )


if __name__ == "__main__":
    unittest.main()
