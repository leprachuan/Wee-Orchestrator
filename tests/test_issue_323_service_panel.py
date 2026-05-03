"""Tests for issue #323: Track API and scheduler services in Services panel."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


class TestApiSchedulerServices(unittest.TestCase):
    """API and scheduler must appear in /api/v1/service-status."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import agent_manager

        cls._telegram_patch = patch.object(
            agent_manager, "_resolve_telegram_identity", side_effect=lambda x: x
        )
        cls._telegram_patch.start()
        cls._pairing_patch = patch.object(
            agent_manager, "_send_pairing_code", return_value=True
        )
        cls._pairing_patch.start()
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._pairing_patch.stop()

    def _get_services(self):
        resp = self.client.get("/api/v1/service-status")
        self.assertEqual(resp.status_code, 200)
        return resp.json()["services"]

    def test_api_service_present(self):
        """Services response must include 'api' key."""
        services = self._get_services()
        self.assertIn("api", services, "'api' must be present in services")

    def test_scheduler_service_present(self):
        """Services response must include 'scheduler' key."""
        services = self._get_services()
        self.assertIn("scheduler", services, "'scheduler' must be present in services")

    def test_api_service_schema(self):
        """api entry must have service, status, and active fields."""
        svc = self._get_services()["api"]
        self.assertIn("service", svc)
        self.assertIn("status", svc)
        self.assertIn("active", svc)
        self.assertIsInstance(svc["active"], bool)

    def test_scheduler_service_schema(self):
        """scheduler entry must have service, status, and active fields."""
        svc = self._get_services()["scheduler"]
        self.assertIn("service", svc)
        self.assertIn("status", svc)
        self.assertIn("active", svc)
        self.assertIsInstance(svc["active"], bool)

    def test_api_dev_suffix(self):
        """In DEV env api service name must contain '-dev'."""
        svc = self._get_services()["api"]
        self.assertIn("-dev", svc["service"], "api service must use -dev suffix in DEV env")

    def test_scheduler_dev_suffix(self):
        """In DEV env scheduler service name must contain '-dev'."""
        svc = self._get_services()["scheduler"]
        self.assertIn("-dev", svc["service"], "scheduler service must use -dev suffix in DEV env")

    def test_all_four_services_present(self):
        """All four services (telegram, webex, api, scheduler) must be present."""
        services = self._get_services()
        for name in ("telegram", "webex", "api", "scheduler"):
            self.assertIn(name, services, f"'{name}' missing from services response")

    def test_unavailable_service_shows_status_not_silent(self):
        """An unreachable service must return a status entry, not be omitted."""
        services = self._get_services()
        for name in ("api", "scheduler"):
            self.assertIn(name, services, f"'{name}' must appear even if offline")
            self.assertIn("status", services[name], f"'{name}' must have a status field")


class TestServicesPanelHtml(unittest.TestCase):
    """HTML Services panel must include elements for api and scheduler."""

    @classmethod
    def setUpClass(cls):
        import pathlib
        candidates = [
            pathlib.Path("/opt/n8n-copilot-shim-dev/webui/dist/index.html"),
            pathlib.Path("/opt/n8n-copilot-shim/webui/dist/index.html"),
        ]
        cls.html = None
        for p in candidates:
            if p.exists():
                cls.html = p.read_text()
                break

    def test_html_file_found(self):
        self.assertIsNotNone(self.html, "index.html not found in expected locations")

    def test_api_dot_element_present(self):
        self.assertIn('id="svc-dot-api"', self.html)

    def test_api_badge_element_present(self):
        self.assertIn('id="svc-badge-api"', self.html)

    def test_scheduler_dot_element_present(self):
        self.assertIn('id="svc-dot-scheduler"', self.html)

    def test_scheduler_badge_element_present(self):
        self.assertIn('id="svc-badge-scheduler"', self.html)

    def test_api_item_container_present(self):
        self.assertIn('id="svc-item-api"', self.html)

    def test_scheduler_item_container_present(self):
        self.assertIn('id="svc-item-scheduler"', self.html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
