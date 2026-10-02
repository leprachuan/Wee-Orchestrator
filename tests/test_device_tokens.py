"""Tests for long-lived, per-device authentication tokens.

Covers the feature that lets iOS/macOS/WebUI clients pair once via
Telegram/WebEx and stay signed in via a per-device token instead of
re-pairing frequently:

- Issuance: verify-pairing issues a per-device token with device metadata
- Storage: tokens are hashed at rest (SHA-256), never stored in plaintext
- Backward compatibility: legacy plaintext-keyed sessions.json migrates
  transparently and continues to authenticate
- Validation: sliding TTL refresh, hard absolute-TTL cap, revoked/unknown
  tokens rejected
- Listing: GET /api/v1/auth/devices returns only the caller's own devices
- Revocation: DELETE /api/v1/auth/devices/{id} and POST .../revoke-all
- Authorization isolation: one identity cannot list or revoke another
  identity's device tokens
- Refresh endpoint and backwards-compatible shared-key auth
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8198")


class TestAuthManagerDeviceTokens(unittest.TestCase):
    """Unit tests directly against AuthManager — no HTTP layer."""

    def setUp(self):
        import agent_manager

        self.tmpdir = tempfile.mkdtemp()
        self.sessions_file = os.path.join(self.tmpdir, "sessions.json")
        self.mgr = agent_manager.AuthManager(
            shared_key="testkey",
            pairing_code_ttl=300,
            session_token_ttl=3600,
            session_token_absolute_ttl=7200,
            sessions_file=self.sessions_file,
        )

    def _issue(self, identity="alice", channel="telegram", device_name="iPhone", platform="iOS"):
        code = self.mgr.generate_pairing_code(identity, channel)
        return self.mgr.verify_pairing_code(code, identity, device_name, platform)

    def test_issuance_returns_token_and_id(self):
        issued = self._issue()
        self.assertIsNotNone(issued)
        self.assertTrue(issued["token"].startswith("session_"))
        self.assertIn("token_id", issued)

    def test_token_hashed_at_rest_not_plaintext(self):
        issued = self._issue()
        with open(self.sessions_file) as f:
            on_disk = f.read()
        self.assertNotIn(issued["token"], on_disk, "raw token must never be persisted")
        entry = json.loads(on_disk)[issued["token_id"]]
        self.assertEqual(
            entry["token_hash"],
            self.mgr._hash_token(issued["token"]),
        )

    def test_validate_accepts_freshly_issued_token(self):
        issued = self._issue()
        data = self.mgr.validate_session_token(issued["token"])
        self.assertIsNotNone(data)
        self.assertEqual(data["identity"], "alice")
        self.assertEqual(data["token_id"], issued["token_id"])

    def test_validate_rejects_unknown_token(self):
        self.assertIsNone(self.mgr.validate_session_token("session_not-a-real-token"))

    def test_validate_rejects_tampered_token(self):
        issued = self._issue()
        tampered = issued["token"][:-1] + ("x" if issued["token"][-1] != "x" else "y")
        self.assertIsNone(self.mgr.validate_session_token(tampered))

    def test_sliding_ttl_extends_on_use(self):
        issued = self._issue()
        first = self.mgr.validate_session_token(issued["token"])
        with patch("time.time", return_value=time.time() + 100):
            second = self.mgr.validate_session_token(issued["token"])
        self.assertGreater(second["expires_at"], first["expires_at"])

    def test_absolute_ttl_caps_sliding_window(self):
        """Repeated use within the sliding TTL keeps extending expires_at,
        but never past the hard absolute_expires_at cap."""
        issued = self._issue()
        base = time.time()

        with patch("time.time", return_value=base + 3000):
            data1 = self.mgr.validate_session_token(issued["token"])
        self.assertIsNotNone(data1)

        with patch("time.time", return_value=base + 6000):
            data2 = self.mgr.validate_session_token(issued["token"])
        self.assertIsNotNone(data2)

        # Naive sliding would give base+6000+3600=base+9600; the absolute
        # cap (base+7200) must win instead.
        self.assertLess(data2["expires_at"], base + 6000 + 3600)
        self.assertLessEqual(data2["expires_at"], base + 7200 + 1)

    def test_token_expires_past_absolute_ttl(self):
        issued = self._issue()
        past_absolute = time.time() + 7300  # beyond absolute_ttl of 7200
        with patch("time.time", return_value=past_absolute):
            data = self.mgr.validate_session_token(issued["token"])
        self.assertIsNone(data)

    def test_list_device_tokens_returns_metadata_no_raw_token(self):
        issued = self._issue(device_name="Foster's iPhone", platform="iOS")
        devices = self.mgr.list_device_tokens("alice")
        self.assertEqual(len(devices), 1)
        d = devices[0]
        self.assertEqual(d["token_id"], issued["token_id"])
        self.assertEqual(d["device_name"], "Foster's iPhone")
        self.assertEqual(d["platform"], "iOS")
        self.assertNotIn("token", d)
        self.assertNotIn("token_hash", d)

    def test_list_device_tokens_isolated_per_identity(self):
        self._issue(identity="alice")
        self._issue(identity="bob")
        self.assertEqual(len(self.mgr.list_device_tokens("alice")), 1)
        self.assertEqual(len(self.mgr.list_device_tokens("bob")), 1)
        self.assertEqual(len(self.mgr.list_device_tokens("carol")), 0)

    def test_revoke_device_token_removes_it(self):
        issued = self._issue()
        ok = self.mgr.revoke_device_token("alice", issued["token_id"])
        self.assertTrue(ok)
        self.assertIsNone(self.mgr.validate_session_token(issued["token"]))
        self.assertEqual(self.mgr.list_device_tokens("alice"), [])

    def test_revoke_device_token_denies_cross_identity(self):
        """Authorization boundary: bob cannot revoke alice's device token."""
        issued = self._issue(identity="alice")
        ok = self.mgr.revoke_device_token("bob", issued["token_id"])
        self.assertFalse(ok)
        # Token must still be valid — the revoke attempt had no effect.
        self.assertIsNotNone(self.mgr.validate_session_token(issued["token"]))

    def test_revoke_all_device_tokens_only_affects_owner(self):
        a1 = self._issue(identity="alice", device_name="iPhone")
        a2 = self._issue(identity="alice", device_name="iPad")
        b1 = self._issue(identity="bob", device_name="MacBook")

        count = self.mgr.revoke_all_device_tokens("alice")

        self.assertEqual(count, 2)
        self.assertIsNone(self.mgr.validate_session_token(a1["token"]))
        self.assertIsNone(self.mgr.validate_session_token(a2["token"]))
        self.assertIsNotNone(self.mgr.validate_session_token(b1["token"]))

    def test_legacy_plaintext_sessions_migrate_and_still_validate(self):
        """Backward compatibility: an existing plaintext-keyed sessions.json
        (the pre-device-token format) must still authenticate, and get
        transparently rewritten to the hashed, per-device format."""
        import agent_manager

        legacy_token = "session_legacy_abc123"
        now = time.time()
        with open(self.sessions_file, "w") as f:
            json.dump(
                {
                    legacy_token: {
                        "identity": "legacy_user",
                        "channel": "webex",
                        "created_at": now,
                        "last_used": now,
                        "expires_at": now + 3600,
                        "absolute_expires_at": now + 7200,
                    }
                },
                f,
            )

        mgr2 = agent_manager.AuthManager(
            shared_key="testkey",
            session_token_ttl=3600,
            session_token_absolute_ttl=7200,
            sessions_file=self.sessions_file,
        )

        data = mgr2.validate_session_token(legacy_token)
        self.assertIsNotNone(data, "legacy plaintext token must still validate after migration")
        self.assertEqual(data["identity"], "legacy_user")

        # File on disk must now be hashed, not plaintext.
        with open(self.sessions_file) as f:
            on_disk = f.read()
        self.assertNotIn(legacy_token, on_disk)

        devices = mgr2.list_device_tokens("legacy_user")
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device_name"], "Legacy device")

    def test_expired_tokens_not_migrated(self):
        import agent_manager

        expired_token = "session_expired_xyz"
        now = time.time()
        with open(self.sessions_file, "w") as f:
            json.dump(
                {
                    expired_token: {
                        "identity": "someone",
                        "channel": "webex",
                        "created_at": now - 10000,
                        "expires_at": now - 100,
                        "absolute_expires_at": now - 50,
                    }
                },
                f,
            )
        mgr2 = agent_manager.AuthManager(
            shared_key="testkey", sessions_file=self.sessions_file
        )
        self.assertIsNone(mgr2.validate_session_token(expired_token))

    def test_cleanup_expired_removes_hash_index_entry(self):
        issued = self._issue()
        with patch("time.time", return_value=time.time() + 7300):
            self.mgr.cleanup_expired()
        self.assertIsNone(self.mgr.validate_session_token(issued["token"]))
        self.assertEqual(self.mgr._hash_index, {})


class TestDeviceTokenAPI(unittest.TestCase):
    """HTTP-level tests for the device token management endpoints."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import agent_manager

        cls.tmpdir = tempfile.mkdtemp()
        cls._saved_scheduler_jobs_file = os.environ.get("SCHEDULER_JOBS_FILE")
        os.environ["SCHEDULER_JOBS_FILE"] = os.path.join(cls.tmpdir, "scheduler_jobs.json")

        cls._saved_api_key = os.environ.get("API_SHARED_KEY")
        os.environ["API_SHARED_KEY"] = "testsharedkey"

        cls._telegram_patch = patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = patch.object(
            agent_manager, "_send_pairing_code", return_value=True
        )
        cls._send_pairing_patch.start()
        cls._username_patch = patch.object(
            agent_manager, "_get_telegram_username", return_value=None
        )
        cls._username_patch.start()

        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.shared_header = {"Authorization": "Bearer shared_testsharedkey"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()
        cls._username_patch.stop()
        if cls._saved_api_key is None:
            os.environ.pop("API_SHARED_KEY", None)
        else:
            os.environ["API_SHARED_KEY"] = cls._saved_api_key
        if cls._saved_scheduler_jobs_file is None:
            os.environ.pop("SCHEDULER_JOBS_FILE", None)
        else:
            os.environ["SCHEDULER_JOBS_FILE"] = cls._saved_scheduler_jobs_file

    def _pair(self, identity, channel="telegram", device_name="Test Device", platform="iOS"):
        # Generate the pairing code directly rather than going through
        # POST /api/v1/auth/request-pairing — that endpoint is rate limited
        # to 5 requests/15min per client IP (tested elsewhere), and this
        # test class issues many more pairings than that limit to exercise
        # the device-token endpoints below.
        import agent_manager

        code = agent_manager._api_auth_manager.generate_pairing_code(identity, channel)
        verify = self.client.post(
            "/api/v1/auth/verify-pairing",
            json={
                "code": code,
                "identity": identity,
                "device_name": device_name,
                "platform": platform,
            },
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        return verify.json()

    def _auth_header(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_verify_pairing_returns_device_token(self):
        result = self._pair("device-user-1", device_name="Foster's iPhone", platform="iOS")
        self.assertTrue(result["token"].startswith("session_"))
        self.assertIn("token_id", result)

    def test_devices_endpoint_requires_auth(self):
        resp = self.client.get("/api/v1/auth/devices")
        self.assertEqual(resp.status_code, 401)

    def test_devices_endpoint_lists_own_device(self):
        result = self._pair("device-user-2", device_name="iPad Pro", platform="iOS")
        resp = self.client.get(
            "/api/v1/auth/devices", headers=self._auth_header(result["token"])
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        devices = resp.json()["devices"]
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["device_name"], "iPad Pro")
        self.assertTrue(devices[0]["current"])
        self.assertNotIn("token", devices[0])

    def test_devices_isolated_between_identities(self):
        """Authorization boundary: user A's device list must never include
        user B's devices, even when both are authenticated."""
        result_a = self._pair("device-user-a", device_name="A-Phone")
        result_b = self._pair("device-user-b", device_name="B-Phone")

        resp_a = self.client.get(
            "/api/v1/auth/devices", headers=self._auth_header(result_a["token"])
        )
        names_a = [d["device_name"] for d in resp_a.json()["devices"]]
        self.assertIn("A-Phone", names_a)
        self.assertNotIn("B-Phone", names_a)

        resp_b = self.client.get(
            "/api/v1/auth/devices", headers=self._auth_header(result_b["token"])
        )
        names_b = [d["device_name"] for d in resp_b.json()["devices"]]
        self.assertIn("B-Phone", names_b)
        self.assertNotIn("A-Phone", names_b)

    def test_revoke_own_device_succeeds(self):
        result = self._pair("device-user-3", device_name="Revoke Me")
        token_id = result["token_id"]
        resp = self.client.delete(
            f"/api/v1/auth/devices/{token_id}",
            headers=self._auth_header(result["token"]),
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        # The revoked token itself is now invalid.
        check = self.client.get(
            "/api/v1/auth/devices", headers=self._auth_header(result["token"])
        )
        self.assertEqual(check.status_code, 401)

    def test_revoke_other_users_device_returns_404(self):
        """Authorization boundary: cannot revoke someone else's device
        token even if you know its token_id."""
        victim = self._pair("device-victim", device_name="Victim Phone")
        attacker = self._pair("device-attacker", device_name="Attacker Phone")

        resp = self.client.delete(
            f"/api/v1/auth/devices/{victim['token_id']}",
            headers=self._auth_header(attacker["token"]),
        )
        self.assertEqual(resp.status_code, 404)

        # Victim's device must be unaffected.
        check = self.client.get(
            "/api/v1/auth/devices", headers=self._auth_header(victim["token"])
        )
        self.assertEqual(check.status_code, 200)
        self.assertEqual(len(check.json()["devices"]), 1)

    def test_revoke_all_only_revokes_callers_own_devices(self):
        user_devices = [
            self._pair("device-user-multi", device_name=f"Device {i}")
            for i in range(3)
        ]
        other = self._pair("device-user-other", device_name="Other Device")

        resp = self.client.post(
            "/api/v1/auth/devices/revoke-all",
            headers=self._auth_header(user_devices[0]["token"]),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["revoked_count"], 3)

        # All three of that user's tokens are now dead.
        for d in user_devices:
            check = self.client.get(
                "/api/v1/auth/devices", headers=self._auth_header(d["token"])
            )
            self.assertEqual(check.status_code, 401)

        # The other user's device is untouched.
        other_check = self.client.get(
            "/api/v1/auth/devices", headers=self._auth_header(other["token"])
        )
        self.assertEqual(other_check.status_code, 200)
        self.assertEqual(len(other_check.json()["devices"]), 1)

    def test_refresh_endpoint_extends_expiry(self):
        result = self._pair("device-user-refresh", device_name="Refresh Phone")
        resp = self.client.post(
            "/api/v1/auth/refresh", headers=self._auth_header(result["token"])
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["token_id"], result["token_id"])
        self.assertIn("expires_at", body)

    def test_refresh_rejects_shared_key(self):
        resp = self.client.post("/api/v1/auth/refresh", headers=self.shared_header)
        self.assertEqual(resp.status_code, 400)

    def test_shared_key_auth_still_works_backwards_compatible(self):
        """Existing shared-key (non-per-device) auth must be unaffected by
        the device-token feature."""
        resp = self.client.get("/api/v1/agents", headers=self.shared_header)
        self.assertEqual(resp.status_code, 200)

    def test_device_session_token_authenticates_other_endpoints(self):
        result = self._pair("device-user-general", device_name="General Use")
        resp = self.client.get(
            "/api/v1/agents", headers=self._auth_header(result["token"])
        )
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
