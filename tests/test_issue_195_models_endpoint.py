"""Regression tests for GitHub issue #195.

Issue #195: /api/v1/models returns empty list for copilot/copilot-sdk runtimes
due to a NameError (_group used instead of group_name in the loop body).

Issue #195 round-2: /api/v1/models was unauthenticated — any caller could
retrieve the full model catalogue without a valid token.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


class TestIssue195ModelsEndpoint(unittest.TestCase):
    """Regression tests for /api/v1/models NameError bug and auth gate."""

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
        cls.shared_header = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    def _mock_copilot_models(self):
        """Return a realistic multi-group copilot model dict."""
        return {
            "featured": ["auto", "claude-opus-4.7", "claude-sonnet-4.6"],
            "openai": ["gpt-5.4", "gpt-5.3-codex"],
        }

    def test_issue_195_unauthenticated_rejected(self):
        """GET /api/v1/models without auth must return HTTP 401."""
        import agent_manager

        with patch.object(
            agent_manager._session_mgr,
            "get_models_for_runtime",
            return_value=self._mock_copilot_models(),
        ):
            resp = self.client.get(
                "/api/v1/models",
                params={"runtime": "copilot"},
            )
        self.assertEqual(
            resp.status_code,
            401,
            "Unauthenticated request must be rejected with 401",
        )

    def test_issue_195_copilot_no_error(self):
        """GET /api/v1/models?runtime=copilot must not return an 'error' key."""
        import agent_manager

        with patch.object(
            agent_manager._session_mgr,
            "get_models_for_runtime",
            return_value=self._mock_copilot_models(),
        ):
            resp = self.client.get(
                "/api/v1/models",
                params={"runtime": "copilot"},
                headers=self.shared_header,
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotIn(
            "error",
            data,
            f"Unexpected error in response: {data.get('error')}",
        )

    def test_issue_195_copilot_returns_non_empty_list(self):
        """GET /api/v1/models?runtime=copilot must return a non-empty list."""
        import agent_manager

        with patch.object(
            agent_manager._session_mgr,
            "get_models_for_runtime",
            return_value=self._mock_copilot_models(),
        ):
            resp = self.client.get(
                "/api/v1/models",
                params={"runtime": "copilot"},
                headers=self.shared_header,
            )
        data = resp.json()
        models = data.get("models", [])
        self.assertGreater(len(models), 0, "Models list must not be empty")

    def test_issue_195_copilot_group_field_populated(self):
        """Each model entry must have a 'group' field matching its group_name."""
        import agent_manager

        with patch.object(
            agent_manager._session_mgr,
            "get_models_for_runtime",
            return_value=self._mock_copilot_models(),
        ):
            resp = self.client.get(
                "/api/v1/models",
                params={"runtime": "copilot"},
                headers=self.shared_header,
            )
        data = resp.json()
        models = data.get("models", [])
        for model in models:
            self.assertIn(
                "group",
                model,
                f"Model {model.get('id')} missing 'group' field",
            )
            self.assertIsNotNone(model["group"], "group must not be None")
            self.assertIn(
                model["group"],
                ["featured", "openai"],
                f"Unexpected group value: {model['group']}",
            )

    def test_issue_195_copilot_all_expected_models_present(self):
        """All models from the mock data must appear in the response."""
        import agent_manager

        with patch.object(
            agent_manager._session_mgr,
            "get_models_for_runtime",
            return_value=self._mock_copilot_models(),
        ):
            resp = self.client.get(
                "/api/v1/models",
                params={"runtime": "copilot"},
                headers=self.shared_header,
            )
        data = resp.json()
        returned_ids = {m["id"] for m in data.get("models", [])}
        expected = {
            "auto",
            "claude-opus-4.7",
            "claude-sonnet-4.6",
            "gpt-5.4",
            "gpt-5.3-codex",
        }
        self.assertEqual(
            expected,
            returned_ids,
            f"Missing models: {expected - returned_ids}",
        )

    def test_issue_195_copilot_sdk_no_error(self):
        """GET /api/v1/models?runtime=copilot-sdk must also work."""
        import agent_manager

        with patch.object(
            agent_manager._session_mgr,
            "get_models_for_runtime",
            return_value={"main": ["auto", "claude-sonnet-4.6"]},
        ):
            resp = self.client.get(
                "/api/v1/models",
                params={"runtime": "copilot-sdk"},
                headers=self.shared_header,
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotIn("error", data)
        self.assertEqual(len(data.get("models", [])), 2)

    def test_issue_195_runtime_key_in_response(self):
        """Response must include the runtime key matching the query parameter."""
        import agent_manager

        with patch.object(
            agent_manager._session_mgr,
            "get_models_for_runtime",
            return_value={"main": ["auto"]},
        ):
            resp = self.client.get(
                "/api/v1/models",
                params={"runtime": "copilot"},
                headers=self.shared_header,
            )
        data = resp.json()
        self.assertEqual(data.get("runtime"), "copilot")


if __name__ == "__main__":
    unittest.main()
