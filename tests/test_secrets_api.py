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

    def test_delete_secret_rejects_invalid_name(self):
        """DELETE /api/v1/secrets/{name} rejects names with invalid characters.
        Names containing slashes are not included because URL routing handles
        those at the path level before the handler is reached.
        """
        for bad_name in ["bad name", "bad;name", "bad@name", "bad!name", "bad$name"]:
            with self.subTest(name=bad_name):
                import urllib.parse

                encoded = urllib.parse.quote(bad_name, safe="")
                resp = self.client.delete(
                    f"/api/v1/secrets/{encoded}",
                    headers=self.auth_header,
                )
                self.assertEqual(
                    resp.status_code, 400, f"Expected 400 for name={bad_name!r}"
                )


if __name__ == "__main__":
    unittest.main()


class TestSecretsF405(unittest.TestCase):
    """Tests for F405: remove copy/reveal; add edit/rotate capability."""

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

    # ─── F405: No GET /secrets/{name} endpoint ─────────────────────────────

    def test_get_individual_secret_not_found(self):
        """GET /api/v1/secrets/{name} must not exist (404 or 405)."""
        resp = self.client.get(
            "/api/v1/secrets/SOME_SECRET",
            headers=self.auth_header,
        )
        self.assertIn(
            resp.status_code,
            [404, 405],
            "GET /api/v1/secrets/{name} must not exist — secrets are write-only",
        )

    def test_get_individual_secret_no_value_leak(self):
        """GET /api/v1/secrets/{name} response body must not contain value key."""
        resp = self.client.get(
            "/api/v1/secrets/ANY_KEY",
            headers=self.auth_header,
        )
        body = resp.text
        self.assertNotIn('"value"', body)

    # ─── F405: List never returns values ───────────────────────────────────

    def test_list_response_contains_no_value_key(self):
        """GET /api/v1/secrets response must not contain a value key."""
        resp = self.client.get(
            "/api/v1/secrets",
            headers=self.auth_header,
        )
        if resp.status_code == 200:
            data = resp.json()
            # No value or values keys in the response
            self.assertNotIn("value", data)
            self.assertNotIn("values", data)
            # secrets list contains only strings (names)
            for item in data.get("secrets", []):
                self.assertIsInstance(item, str)

    # ─── F405: POST upsert works for edit/rotate ──────────────────────────

    def test_post_upsert_accepts_existing_name(self):
        """POST /api/v1/secrets with an existing name should succeed (upsert)."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "F405_ROTATE_TEST", "value": "original"},
            headers=self.auth_header,
        )
        # First call — may succeed or fail due to backend, but not 400
        if resp.status_code == 200:
            # Second call with same name, new value = rotation
            resp2 = self.client.post(
                "/api/v1/secrets",
                json={"name": "F405_ROTATE_TEST", "value": "rotated"},
                headers=self.auth_header,
            )
            self.assertNotEqual(resp2.status_code, 400)
            if resp2.status_code == 200:
                data = resp2.json()
                self.assertEqual(data.get("action"), "updated")
                self.assertNotIn("value", data)

    def test_post_upsert_response_never_contains_value(self):
        """POST /api/v1/secrets response must never echo back the value."""
        resp = self.client.post(
            "/api/v1/secrets",
            json={"name": "F405_LEAK_TEST", "value": "super_secret_xyz_405"},
            headers=self.auth_header,
        )
        if resp.status_code == 200:
            self.assertNotIn("super_secret_xyz_405", resp.text)
            data = resp.json()
            self.assertNotIn("value", data)

    # ─── F405: Cleanup (optional) ──────────────────────────────────────────

    def test_delete_f405_test_secrets(self):
        """Cleanup: delete F405 test secrets if they exist."""
        for name in ("F405_ROTATE_TEST", "F405_LEAK_TEST"):
            self.client.delete(
                f"/api/v1/secrets/{name}",
                headers=self.auth_header,
            )
