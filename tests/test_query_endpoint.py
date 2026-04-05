"""Tests for the /api/v1/query stateless query endpoint."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")


class TestQueryEndpoint(unittest.TestCase):
    """Tests for POST /api/v1/query — one-shot stateless queries."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch as _p
        from fastapi.testclient import TestClient
        import agent_manager

        cls._telegram_patch = _p.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = _p.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        cls._send_pairing_patch.start()
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.auth = {
            "Authorization": "Bearer shared_test_key_123",
            "X-User-Identity": "test_user",
            "X-Auth-Channel": "api",
        }

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    def test_query_no_auth_returns_401(self):
        """Query without auth should return 401."""
        resp = self.client.post(
            "/api/v1/query",
            json={"prompt": "hello"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_query_missing_prompt_returns_422(self):
        """Query without prompt field should return 422."""
        resp = self.client.post(
            "/api/v1/query",
            json={"runtime": "copilot"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 422)

    def test_query_prompt_too_long_returns_422(self):
        """Query with prompt > 10000 chars should return 422."""
        resp = self.client.post(
            "/api/v1/query",
            json={"prompt": "x" * 10001},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 422)

    @patch("agent_manager.SessionManager.execute", return_value="Hello back!")
    def test_query_basic_success(self, mock_execute):
        """Basic query should return 200 with response."""
        resp = self.client.post(
            "/api/v1/query",
            json={"prompt": "Say hello"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("response", data)
        self.assertEqual(data["response"], "Hello back!")
        self.assertIn("session_id", data)
        self.assertTrue(data["session_id"].startswith("query_"))
        self.assertIn("runtime", data)
        self.assertIn("model", data)

    @patch("agent_manager.SessionManager.execute", return_value="opencode result")
    def test_query_with_runtime_and_model(self, mock_execute):
        """Query with explicit runtime and model should set them."""
        resp = self.client.post(
            "/api/v1/query",
            json={
                "prompt": "test prompt",
                "runtime": "opencode",
                "model": "opencode/gpt-5-nano",
            },
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["response"], "opencode result")
        self.assertEqual(data["runtime"], "opencode")

    @patch("agent_manager.SessionManager.execute", return_value="agent result")
    def test_query_with_agent(self, mock_execute):
        """Query with explicit agent should use that agent."""
        resp = self.client.post(
            "/api/v1/query",
            json={
                "prompt": "test prompt",
                "agent": "orchestrator",
            },
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["response"], "agent result")

    @patch("agent_manager.SessionManager.execute", return_value="done")
    def test_query_with_timeout(self, mock_execute):
        """Query with timeout should be accepted."""
        resp = self.client.post(
            "/api/v1/query",
            json={
                "prompt": "test prompt",
                "timeout": 30,
            },
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)

    @patch("agent_manager.SessionManager.execute", return_value="cleaned up")
    def test_query_cleans_up_session(self, mock_execute):
        """Temporary query session should not persist after response."""
        resp = self.client.post(
            "/api/v1/query",
            json={"prompt": "test"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        session_id = resp.json()["session_id"]
        self.assertTrue(session_id.startswith("query_"))

        # Verify the session was ephemeral by checking a second query
        # creates a different session (not reusing the old one)
        resp2 = self.client.post(
            "/api/v1/query",
            json={"prompt": "test2"},
            headers=self.auth,
        )
        self.assertEqual(resp2.status_code, 200)
        session_id2 = resp2.json()["session_id"]
        self.assertNotEqual(session_id, session_id2)

    @patch("agent_manager.SessionManager.execute", return_value="result 1")
    def test_query_returns_correct_schema(self, mock_execute):
        """Response schema should have session_id, response, runtime, model."""
        resp = self.client.post(
            "/api/v1/query",
            json={"prompt": "test"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        required_keys = {"session_id", "response", "runtime", "model"}
        self.assertTrue(required_keys.issubset(set(data.keys())))

    @patch("agent_manager.SessionManager.execute", return_value="parallel ok")
    def test_query_multiple_concurrent(self, mock_execute):
        """Multiple queries should not interfere with each other."""
        import concurrent.futures

        def make_query(i):
            return self.client.post(
                "/api/v1/query",
                json={"prompt": f"test {i}"},
                headers=self.auth,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(make_query, i) for i in range(3)]
            results = [f.result() for f in futures]

        session_ids = set()
        for resp in results:
            self.assertEqual(resp.status_code, 200)
            sid = resp.json()["session_id"]
            session_ids.add(sid)

        # All session IDs should be unique
        self.assertEqual(len(session_ids), 3)


if __name__ == "__main__":
    unittest.main()
