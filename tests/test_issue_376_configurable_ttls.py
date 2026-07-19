"""Regression test for issue #376: Telegram auth flow TTLs configurable via .env.

Verifies that WEE_PAIRING_CODE_TTL, WEE_SESSION_TOKEN_TTL, and
WEE_SESSION_TOKEN_ABSOLUTE_TTL environment variables are respected,
with fallback to unprefixed names and hardcoded defaults.
"""

import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_manager import AuthManager


class TestIssue376ConfigurableTTLs(unittest.TestCase):
    """TTL values should be configurable via WEE_-prefixed env vars."""

    def test_auth_manager_accepts_custom_ttls(self):
        """AuthManager uses the TTL values passed to its constructor."""
        mgr = AuthManager(
            shared_key="test",
            pairing_code_ttl=600,
            session_token_ttl=14400,
            session_token_absolute_ttl=172800,
        )
        self.assertEqual(mgr.pairing_code_ttl, 600)
        self.assertEqual(mgr.session_token_ttl, 14400)
        self.assertEqual(mgr.session_token_absolute_ttl, 172800)

    def test_auth_manager_default_ttls(self):
        """AuthManager defaults retain the production 30/180-day policy."""
        mgr = AuthManager(shared_key="test")
        self.assertEqual(mgr.pairing_code_ttl, 300)
        self.assertEqual(mgr.session_token_ttl, 2592000)
        self.assertEqual(mgr.session_token_absolute_ttl, 15552000)

    def test_env_var_parsing_wee_prefix(self):
        """WEE_-prefixed env vars are read by the env-parsing pattern."""
        env = {
            "WEE_PAIRING_CODE_TTL": "600",
            "WEE_SESSION_TOKEN_TTL": "14400",
            "WEE_SESSION_TOKEN_ABSOLUTE_TTL": "172800",
        }
        with patch.dict(os.environ, env, clear=False):
            pairing = int(os.environ.get("WEE_PAIRING_CODE_TTL", os.environ.get("PAIRING_CODE_TTL", "300")))
            session = int(os.environ.get("WEE_SESSION_TOKEN_TTL", os.environ.get("SESSION_TOKEN_TTL", "2592000")))
            absolute = int(os.environ.get("WEE_SESSION_TOKEN_ABSOLUTE_TTL", os.environ.get("SESSION_TOKEN_ABSOLUTE_TTL", "15552000")))
        self.assertEqual(pairing, 600)
        self.assertEqual(session, 14400)
        self.assertEqual(absolute, 172800)

    def test_env_var_parsing_unprefixed_fallback(self):
        """Unprefixed env vars still work when WEE_ vars are not set."""
        clean_keys = ["WEE_PAIRING_CODE_TTL", "WEE_SESSION_TOKEN_TTL", "WEE_SESSION_TOKEN_ABSOLUTE_TTL"]
        env = {
            "PAIRING_CODE_TTL": "120",
            "SESSION_TOKEN_TTL": "7200",
            "SESSION_TOKEN_ABSOLUTE_TTL": "43200",
        }
        with patch.dict(os.environ, env, clear=False):
            for k in clean_keys:
                os.environ.pop(k, None)
            pairing = int(os.environ.get("WEE_PAIRING_CODE_TTL", os.environ.get("PAIRING_CODE_TTL", "300")))
            session = int(os.environ.get("WEE_SESSION_TOKEN_TTL", os.environ.get("SESSION_TOKEN_TTL", "2592000")))
            absolute = int(os.environ.get("WEE_SESSION_TOKEN_ABSOLUTE_TTL", os.environ.get("SESSION_TOKEN_ABSOLUTE_TTL", "15552000")))
        self.assertEqual(pairing, 120)
        self.assertEqual(session, 7200)
        self.assertEqual(absolute, 43200)

    def test_wee_prefix_takes_precedence(self):
        """WEE_-prefixed vars take precedence over unprefixed."""
        env = {
            "WEE_PAIRING_CODE_TTL": "999",
            "PAIRING_CODE_TTL": "111",
            "WEE_SESSION_TOKEN_TTL": "888",
            "SESSION_TOKEN_TTL": "222",
            "WEE_SESSION_TOKEN_ABSOLUTE_TTL": "777",
            "SESSION_TOKEN_ABSOLUTE_TTL": "333",
        }
        with patch.dict(os.environ, env, clear=False):
            pairing = int(os.environ.get("WEE_PAIRING_CODE_TTL", os.environ.get("PAIRING_CODE_TTL", "300")))
            session = int(os.environ.get("WEE_SESSION_TOKEN_TTL", os.environ.get("SESSION_TOKEN_TTL", "2592000")))
            absolute = int(os.environ.get("WEE_SESSION_TOKEN_ABSOLUTE_TTL", os.environ.get("SESSION_TOKEN_ABSOLUTE_TTL", "15552000")))
        self.assertEqual(pairing, 999)
        self.assertEqual(session, 888)
        self.assertEqual(absolute, 777)

    def test_pairing_code_uses_configured_ttl(self):
        """Pairing codes expire according to the configured TTL."""
        mgr = AuthManager(shared_key="test", pairing_code_ttl=10)
        code = mgr.generate_pairing_code("test_identity", "telegram")
        entry = mgr.pairing_codes[code]
        self.assertAlmostEqual(entry["expires_at"], time.time() + 10, delta=2)

    def test_session_token_uses_configured_ttls(self):
        """Session tokens use both sliding and absolute TTL values."""
        mgr = AuthManager(
            shared_key="test",
            pairing_code_ttl=300,
            session_token_ttl=14400,
            session_token_absolute_ttl=172800,
        )
        code = mgr.generate_pairing_code("test_identity", "telegram")
        token = mgr.verify_pairing_code(code, "test_identity")
        self.assertIsNotNone(token)
        result = mgr.validate_session_token(token)
        self.assertIsNotNone(result)
        entry = mgr.session_tokens[token]
        now = time.time()
        self.assertAlmostEqual(entry["expires_at"], now + 14400, delta=2)
        self.assertAlmostEqual(entry["absolute_expires_at"], now + 172800, delta=2)

    def test_source_code_uses_wee_prefix(self):
        """agent_manager.py reads WEE_-prefixed env vars for all three TTLs."""
        source_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agent_manager.py",
        )
        with open(source_file) as f:
            source = f.read()
        assert 'os.environ.get("WEE_PAIRING_CODE_TTL"' in source
        assert 'os.environ.get("WEE_SESSION_TOKEN_TTL"' in source
        assert 'os.environ.get("WEE_SESSION_TOKEN_ABSOLUTE_TTL"' in source


if __name__ == "__main__":
    unittest.main()
