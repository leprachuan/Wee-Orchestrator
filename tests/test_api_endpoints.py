"""Tests for FastAPI endpoints."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


class TestAPIEndpoints(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import agent_manager

        # Mock Telegram identity resolution so pairing tests don't need live bot
        cls._telegram_patch = patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.shared_header = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()

    def test_health_endpoint(self):
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("environment", data)
        self.assertEqual(data["version"], "1.0.0")

    def test_request_pairing_success(self):
        resp = self.client.post(
            "/api/v1/auth/request-pairing",
            json={"identity": "test_user", "channel": "telegram"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("expires_in", data)

    def test_request_pairing_invalid_channel(self):
        resp = self.client.post(
            "/api/v1/auth/request-pairing",
            json={"identity": "test_user", "channel": "sms"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_verify_pairing_invalid_code(self):
        resp = self.client.post(
            "/api/v1/auth/verify-pairing",
            json={"code": "000000", "identity": "test_user"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_execute_no_auth(self):
        resp = self.client.post(
            "/api/v1/sessions/test_session/execute",
            json={"query": "hello"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_execute_bad_shared_key(self):
        resp = self.client.post(
            "/api/v1/sessions/test_session/execute",
            json={"query": "hello"},
            headers={"Authorization": "Bearer shared_wrong_key"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_session_create_with_shared_key(self):
        resp = self.client.post(
            "/api/v1/sessions/create",
            json={},
            headers={
                **self.shared_header,
                "X-User-Identity": "telegram_123",
                "X-Auth-Channel": "telegram",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("session_id", data)

    def test_session_status_not_found(self):
        resp = self.client.get(
            "/api/v1/sessions/nonexistent_session_xyz/status",
            headers=self.shared_header,
        )
        self.assertEqual(resp.status_code, 404)

    def test_full_pairing_flow(self):
        resp = self.client.post(
            "/api/v1/auth/request-pairing",
            json={"identity": "flow_user", "channel": "telegram"},
        )
        self.assertEqual(resp.status_code, 200)

        from agent_manager import _api_auth_manager

        codes = {
            k: v
            for k, v in _api_auth_manager.pairing_codes.items()
            if v["identity"] == "flow_user"
        }
        self.assertEqual(len(codes), 1)
        code = list(codes.keys())[0]

        resp = self.client.post(
            "/api/v1/auth/verify-pairing",
            json={"code": code, "identity": "flow_user"},
        )
        self.assertEqual(resp.status_code, 200)
        token = resp.json()["token"]
        self.assertTrue(token.startswith("session_"))

        resp = self.client.post(
            "/api/v1/sessions/create",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
