"""Regression tests for GitHub issue #31.

Issue #31 — Auth coverage for /api/v1/agents, /api/v1/runtimes,
and /api/v1/models; plus structured logging.

Functional coverage:
- Unauthenticated GET /api/v1/agents → 401
- Unauthenticated GET /api/v1/runtimes → 401
- Unauthenticated GET /api/v1/models → 401
- Authenticated GET /api/v1/agents → 200 with agent list
- Authenticated GET /api/v1/runtimes → 200 with runtime list
- Authenticated GET /api/v1/models → 200 with grouped models, no _group NameError
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = ""
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8199"


class TestIssue31AuthCoverage(unittest.TestCase):
    """Regression tests: auth for /api/v1/agents, /runtimes, /models."""

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
        # When API_SHARED_KEY is empty, "Bearer shared_" is a valid token
        cls.auth_header = {"Authorization": "Bearer shared_"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    def _mock_copilot_models(self):
        return {
            "featured": ["claude-sonnet-4.6", "claude-haiku-4.5"],
            "openai": ["gpt-5.4"],
        }

    # ── /api/v1/agents ────────────────────────────────────────────────────────

    def test_issue_31_unauthenticated_agents_returns_401(self):
        """GET /api/v1/agents without auth must return 401."""
        resp = self.client.get("/api/v1/agents")
        self.assertEqual(
            resp.status_code,
            401,
            f"Expected 401 for unauthenticated /api/v1/agents, got {resp.status_code}",
        )

    def test_issue_31_authenticated_agents_returns_list(self):
        """GET /api/v1/agents with valid auth must return 200 with agents list."""
        resp = self.client.get("/api/v1/agents", headers=self.auth_header)
        self.assertEqual(
            resp.status_code,
            200,
            f"Expected 200 authenticated /api/v1/agents, got {resp.status_code}",
        )
        data = resp.json()
        self.assertIn("agents", data, "Response must contain 'agents' key")
        self.assertIsInstance(data["agents"], list)

    # ── /api/v1/runtimes ──────────────────────────────────────────────────────

    def test_issue_31_unauthenticated_runtimes_returns_401(self):
        """GET /api/v1/runtimes without auth must return 401."""
        resp = self.client.get("/api/v1/runtimes")
        self.assertEqual(
            resp.status_code,
            401,
            f"Expected 401 unauthenticated /api/v1/runtimes, got {resp.status_code}",
        )

    def test_issue_31_authenticated_runtimes_returns_list(self):
        """GET /api/v1/runtimes with valid auth must return 200 with runtimes."""
        resp = self.client.get("/api/v1/runtimes", headers=self.auth_header)
        self.assertEqual(
            resp.status_code,
            200,
            f"Expected 200 for authenticated /api/v1/runtimes, got {resp.status_code}",
        )
        data = resp.json()
        self.assertIn("runtimes", data, "Response must contain 'runtimes' key")
        self.assertIsInstance(data["runtimes"], list)
        self.assertGreater(len(data["runtimes"]), 0, "Must return at least one runtime")

    # ── /api/v1/models ────────────────────────────────────────────────────────

    def test_issue_31_unauthenticated_models_returns_401(self):
        """GET /api/v1/models without auth must return 401."""
        resp = self.client.get("/api/v1/models", params={"runtime": "copilot"})
        self.assertEqual(
            resp.status_code,
            401,
            f"Expected 401 for unauthenticated /api/v1/models, got {resp.status_code}",
        )

    def test_issue_31_authenticated_models_returns_grouped_models(self):
        """GET /api/v1/models with auth: grouped models, group field present."""
        import agent_manager

        with patch.object(
            agent_manager._session_mgr,
            "get_models_for_runtime",
            return_value=self._mock_copilot_models(),
        ):
            resp = self.client.get(
                "/api/v1/models",
                params={"runtime": "copilot"},
                headers=self.auth_header,
            )
        self.assertEqual(
            resp.status_code,
            200,
            f"Expected 200 for authenticated /api/v1/models, got {resp.status_code}",
        )
        data = resp.json()
        self.assertNotIn("error", data, f"Unexpected error: {data.get('error')}")
        models = data.get("models", [])
        self.assertGreater(len(models), 0, "models list must not be empty")
        for m in models:
            self.assertIn(
                "group",
                m,
                f"Each model must have 'group' field (no _group NameError). Got: {m}",
            )
            self.assertNotIn(
                "_group",
                m,
                "Response must not contain '_group' key (NameError regression)",
            )

    def test_issue_31_authenticated_models_no_name_error(self):
        """Authenticated /api/v1/models must not raise NameError (_group undefined)."""
        import agent_manager

        with patch.object(
            agent_manager._session_mgr,
            "get_models_for_runtime",
            return_value=self._mock_copilot_models(),
        ):
            try:
                resp = self.client.get(
                    "/api/v1/models",
                    params={"runtime": "copilot"},
                    headers=self.auth_header,
                )
            except NameError as e:
                self.fail(f"NameError raised in /api/v1/models: {e}")
        # A NameError would manifest as a 500 response
        self.assertNotEqual(
            resp.status_code,
            500,
            "500 response indicates NameError (_group) or other server error",
        )


if __name__ == "__main__":
    unittest.main()
