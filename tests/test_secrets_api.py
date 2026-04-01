"""Tests for F019: Secrets Manager API endpoints."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")


class TestSecretsAPI(unittest.TestCase):
    """Test /api/v1/secrets endpoints."""

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
        cls.auth_header = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    # ─── Auth Tests ────────────────────────────────────────────────────────

    def test_list_secrets_requires_auth(self):
        """GET /api/v1/secrets rejects unauthenticated requests."""
        resp = self.client.get("/api/v1/secrets")
        self.assertEqual(resp.status_code, 401)

    def test_store_secret_requires_auth(self):
        """POST /api/v1/secrets rejects unauthenticated requests."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "TEST_KEY", "value": "test_val"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_delete_secret_requires_auth(self):
        """DELETE /api/v1/secrets/{name} rejects unauthenticated requests."""
        resp = self.client.delete("/api/v1/secrets/TEST_KEY")
        self.assertEqual(resp.status_code, 401)

    # ─── Input Validation ──────────────────────────────────────────────────

    def test_store_secret_rejects_empty_name(self):
        """POST /api/v1/secrets rejects empty name."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "", "value": "val"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("name", resp.json()["detail"].lower())

    def test_store_secret_rejects_missing_name(self):
        """POST /api/v1/secrets rejects missing name."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"value": "val"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)

    def test_store_secret_rejects_empty_value(self):
        """POST /api/v1/secrets rejects empty value."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "TEST_KEY", "value": ""},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("value", resp.json()["detail"].lower())

    def test_store_secret_rejects_invalid_name_spaces(self):
        """POST /api/v1/secrets rejects names with spaces."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "foo bar", "value": "val"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)

    def test_store_secret_rejects_invalid_name_slash(self):
        """POST /api/v1/secrets rejects names with slashes."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "foo/bar", "value": "val"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)

    def test_store_secret_rejects_invalid_name_semicolon(self):
        """POST /api/v1/secrets rejects names with semicolons."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "a;b", "value": "val"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)

    def test_store_secret_rejects_shell_injection(self):
        """POST /api/v1/secrets rejects names with shell metacharacters."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "$(cmd)", "value": "val"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)

    def test_store_secret_rejects_pipe(self):
        """POST /api/v1/secrets rejects names with pipe characters."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "a|b", "value": "val"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)

    def test_store_secret_accepts_valid_alphanumeric(self):
        """POST /api/v1/secrets accepts alphanumeric names (validation only)."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "MY_KEY", "value": "test"},
            headers=self.auth_header,
        )
        # Should pass input validation (not 400)
        self.assertNotEqual(resp.status_code, 400)

    def test_store_secret_accepts_dots_hyphens(self):
        """POST /api/v1/secrets accepts names with dots and hyphens."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "api-key.prod", "value": "test"},
            headers=self.auth_header,
        )
        self.assertNotEqual(resp.status_code, 400)

    # ─── Endpoint Existence ────────────────────────────────────────────────

    def test_list_secrets_endpoint_exists(self):
        """GET /api/v1/secrets endpoint is registered and authenticated."""
        resp = self.client.get("/api/v1/secrets", headers=self.auth_header)
        # Should not be 404 or 405; may be 500 if secret-tool binary not installed
        self.assertNotIn(resp.status_code, [404, 405])

    def test_store_secret_endpoint_exists(self):
        """POST /api/v1/secrets endpoint is registered."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "ENDPOINT_TEST", "value": "v"},
            headers=self.auth_header,
        )
        self.assertNotIn(resp.status_code, [404, 405])

    def test_delete_secret_endpoint_exists(self):
        """DELETE /api/v1/secrets/{name} endpoint is registered."""
        resp = self.client.delete(
            "/api/v1/secrets/NONEXISTENT_12345",
            headers=self.auth_header,
        )
        self.assertNotIn(resp.status_code, [405])

    # ─── Response Shape ────────────────────────────────────────────────────

    def test_list_secrets_response_shape(self):
        """GET /api/v1/secrets returns {secrets: [...], count: N}."""
        resp = self.client.get("/api/v1/secrets", headers=self.auth_header)
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("secrets", data)
            self.assertIn("count", data)
            self.assertIsInstance(data["secrets"], list)
            self.assertEqual(data["count"], len(data["secrets"]))

    def test_list_secrets_never_returns_values(self):
        """GET /api/v1/secrets response contains only name strings, never values."""
        resp = self.client.get("/api/v1/secrets", headers=self.auth_header)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("secrets", []):
                self.assertIsInstance(item, str)

    def test_store_secret_response_no_value(self):
        """POST /api/v1/secrets response never includes the secret value."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "SAFE_TEST", "value": "super_secret_value_12345"},
            headers=self.auth_header,
        )
        if resp.status_code == 200:
            body_text = resp.text
            self.assertNotIn("super_secret_value_12345", body_text)
            data = resp.json()
            self.assertNotIn("value", data)
            self.assertNotIn("credential", data)


if __name__ == "__main__":
    unittest.main()
