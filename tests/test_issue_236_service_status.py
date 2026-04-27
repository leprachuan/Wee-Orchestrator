"""Tests for issue #236: Service status endpoint for Telegram and WebEx connectors."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


class TestServiceStatusEndpoint(unittest.TestCase):
    """Test the /api/v1/service-status endpoint added in issue #236."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch

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

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    def test_service_status_endpoint_exists(self):
        """Endpoint must return 200 (not 404)."""
        resp = self.client.get("/api/v1/service-status")
        self.assertNotEqual(resp.status_code, 404, "service-status endpoint must exist")

    def test_service_status_returns_both_services(self):
        """Response must include both 'telegram' and 'webex' entries."""
        resp = self.client.get("/api/v1/service-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("services", data)
        services = data["services"]
        self.assertIn("telegram", services, "telegram must be present in services")
        self.assertIn("webex", services, "webex must be present in services")

    def test_service_status_schema(self):
        """Each service entry must have required fields."""
        resp = self.client.get("/api/v1/service-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for name in ("telegram", "webex"):
            svc = data["services"][name]
            self.assertIn("service", svc, f"{name}.service must be present")
            self.assertIn("status", svc, f"{name}.status must be present")
            self.assertIn("active", svc, f"{name}.active must be boolean")
            self.assertIsInstance(svc["active"], bool)

    def test_service_status_includes_node(self):
        """Response must include node hostname."""
        resp = self.client.get("/api/v1/service-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("node", data)
        self.assertIsInstance(data["node"], str)
        self.assertGreater(len(data["node"]), 0)

    def test_service_status_includes_timestamp(self):
        """Response must include checked_at timestamp."""
        resp = self.client.get("/api/v1/service-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("checked_at", data)
        self.assertIsInstance(data["checked_at"], (int, float))
        self.assertGreater(data["checked_at"], 0)

    def test_dev_environment_uses_dev_service_names(self):
        """In DEV environment, service names must have -dev suffix."""
        resp = self.client.get("/api/v1/service-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        telegram_svc = data["services"]["telegram"]["service"]
        webex_svc = data["services"]["webex"]["service"]
        self.assertIn("-dev", telegram_svc, "telegram service name must have -dev suffix in DEV env")
        self.assertIn("-dev", webex_svc, "webex service name must have -dev suffix in DEV env")

    def test_service_status_active_field_valid_type(self):
        """active field must always be boolean, never string."""
        resp = self.client.get("/api/v1/service-status")
        data = resp.json()
        for name, svc in data["services"].items():
            self.assertIsInstance(
                svc["active"], bool,
                f"{name}.active must be bool, got {type(svc['active'])}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
