"""
Regression test for Issue #292:
POST /api/v1/background-tasks accepts any model string without validation — invalid
model names are silently queued and fail at execution time.

Expected behavior after fix:
- Explicitly provided invalid model → HTTP 422
- Explicitly provided valid model → accepted, resolved to canonical ID
- No model provided → accepted (API uses runtime default)
- "auto" as model name → HTTP 422 (not a valid model ID for any runtime)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Part of #471: a plain assignment here is not actually hermetic -- it's a
# module-level side effect that permanently overwrites API_SHARED_KEY for
# every test file collected afterward in the same process. setdefault with
# the value most other test files already use avoids adding to that
# collision surface while still guaranteeing a known value.
os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _start_app():
    """Import and start the FastAPI app with minimal mocking."""
    from unittest.mock import patch as _p

    import agent_manager

    patches = []

    p1 = _p.object(
        agent_manager,
        "_resolve_telegram_identity",
        side_effect=lambda identity: identity,
    )
    p1.start()
    patches.append(p1)

    p2 = _p.object(
        agent_manager,
        "_send_pairing_code",
        return_value=True,
    )
    p2.start()
    patches.append(p2)

    app = agent_manager.create_api_app()
    return app, patches


class TestIssue292BgTaskModelValidation(unittest.TestCase):
    """Background task model validation — reject invalid models at submission."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        cls.app, cls._patches = _start_app()
        cls.client = TestClient(cls.app)
        # Read live rather than hardcoding the literal set above: whichever
        # test file's own API_SHARED_KEY assignment "wins" for the process
        # (see #471), this always matches whatever create_api_app() above
        # actually picked up.
        shared_key = os.environ.get("API_SHARED_KEY", "test_key_123")
        cls.auth = {
            "Authorization": f"Bearer shared_{shared_key}",
            "X-User-Identity": "test_user_292",
            "X-Auth-Channel": "api",
        }

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()

    def _post_bg_task(self, payload):
        return self.client.post(
            "/api/v1/background-tasks",
            json=payload,
            headers=self.auth,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Core regression: invalid explicit model must return 422 (not 200/queued)
    # ──────────────────────────────────────────────────────────────────────────

    def test_invalid_model_returns_422(self):
        """Invalid model name must return 422, not silently queue the task."""
        resp = self._post_bg_task(
            {
                "prompt": "Say hello",
                "agent": "fosterbot",
                "runtime": "copilot",
                "model": "definitely-not-a-real-model-zzz999",
            }
        )
        self.assertEqual(
            resp.status_code,
            422,
            f"Expected 422 for invalid model, got {resp.status_code}",
        )
        detail = str(resp.json().get("detail", ""))
        self.assertIn(
            "definitely-not-a-real-model-zzz999",
            detail,
            "Error detail should include the invalid model name",
        )

    def test_auto_model_returns_422_for_explicit_model(self):
        """'auto' is not a real model ID — must be rejected when explicit."""
        resp = self._post_bg_task(
            {
                "prompt": "Say hello",
                "agent": "fosterbot",
                "runtime": "copilot",
                "model": "auto",
            }
        )
        self.assertEqual(
            resp.status_code,
            422,
            f"'auto' should be rejected, got {resp.status_code}",
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Happy path: no model provided — should not reject
    # ──────────────────────────────────────────────────────────────────────────

    @patch("agent_manager.BackgroundTaskManager.create_task")
    @patch("agent_manager.BackgroundTaskManager.count_running", return_value=0)
    @patch("agent_manager.BackgroundTaskManager.count_queued", return_value=0)
    def test_no_model_uses_default_and_succeeds(
        self, mock_queued, mock_running, mock_create
    ):
        """Omitting 'model' should succeed — API resolves using default model."""
        mock_task = {
            "task_id": "bg_test292",
            "session_id": "sess-292",
            "agent": "fosterbot",
            "runtime": "copilot",
            "model": "claude-sonnet-4.6",
            "status": "running",
            "timeout": 900,
            "permission_mode": "restricted",
        }
        mock_create.return_value = mock_task

        resp = self._post_bg_task(
            {
                "prompt": "Say hello",
                "agent": "fosterbot",
                "runtime": "copilot",
                # no "model" field
            }
        )

        self.assertNotEqual(
            resp.status_code,
            422,
            f"Omitting model should not return 422, got {resp.status_code}",
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Model resolved to canonical ID when valid alias/name is provided
    # ──────────────────────────────────────────────────────────────────────────

    @patch("agent_manager.BackgroundTaskManager.create_task")
    @patch("agent_manager.BackgroundTaskManager.count_running", return_value=0)
    @patch("agent_manager.BackgroundTaskManager.count_queued", return_value=0)
    @patch(
        "agent_manager.SessionManager.get_model_from_name",
        return_value="claude-sonnet-4-6-20251001",
    )
    def test_valid_model_alias_is_resolved(
        self, mock_get_model, mock_queued, mock_running, mock_create
    ):
        """A valid model alias should be resolved to canonical ID before creation."""
        mock_task = {
            "task_id": "bg_test292b",
            "session_id": "sess-292b",
            "agent": "fosterbot",
            "runtime": "claude",
            "model": "claude-sonnet-4-6-20251001",
            "status": "running",
            "timeout": 900,
            "permission_mode": "restricted",
        }
        mock_create.return_value = mock_task

        resp = self._post_bg_task(
            {
                "prompt": "Say hello",
                "agent": "fosterbot",
                "runtime": "claude",
                "model": "claude-sonnet-4.6",
            }
        )

        self.assertNotEqual(
            resp.status_code,
            422,
            f"Valid model should not return 422, got {resp.status_code}",
        )
        mock_get_model.assert_called_with("claude-sonnet-4.6", "claude")

    # ──────────────────────────────────────────────────────────────────────────
    # Error message quality: must include the model name and runtime
    # ──────────────────────────────────────────────────────────────────────────

    def test_422_error_includes_runtime_in_detail(self):
        """422 error detail must mention the runtime for the caller to diagnose."""
        resp = self._post_bg_task(
            {
                "prompt": "Say hello",
                "runtime": "copilot",
                "model": "not-a-model-xyz",
            }
        )
        self.assertEqual(resp.status_code, 422)
        detail = str(resp.json().get("detail", ""))
        self.assertIn(
            "copilot",
            detail,
            "Error detail should mention the runtime",
        )

    def test_422_error_includes_models_endpoint_hint(self):
        """422 error detail should hint at the models endpoint for discovery."""
        resp = self._post_bg_task(
            {
                "prompt": "Say hello",
                "runtime": "claude",
                "model": "gpt-4-turbo-invalid",
            }
        )
        self.assertEqual(resp.status_code, 422)
        detail = str(resp.json().get("detail", ""))
        self.assertIn(
            "/api/v1/models",
            detail,
            "Error detail should point to /api/v1/models endpoint",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
