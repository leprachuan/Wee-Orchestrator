"""Tests for AuthManager - pairing codes, session tokens, rate limiting."""
import time
import unittest
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAuthManager(unittest.TestCase):
    """Test AuthManager authentication logic."""

    def setUp(self):
        from agent_manager import AuthManager
        self.auth = AuthManager(
            shared_key="test_shared_key",
            pairing_code_length=6,
            pairing_code_ttl=300,
            session_token_ttl=3600,
            session_token_absolute_ttl=86400,
        )

    def test_validate_shared_key_success(self):
        result = self.auth.validate_shared_key("shared_test_shared_key")
        self.assertTrue(result)

    def test_validate_shared_key_wrong_key(self):
        result = self.auth.validate_shared_key("shared_wrong_key")
        self.assertFalse(result)

    def test_validate_shared_key_missing_prefix(self):
        result = self.auth.validate_shared_key("test_shared_key")
        self.assertFalse(result)

    def test_generate_pairing_code(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_generate_pairing_code_stored(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        self.assertIn(code, self.auth.pairing_codes)
        entry = self.auth.pairing_codes[code]
        self.assertEqual(entry["identity"], "user123")
        self.assertEqual(entry["channel"], "telegram")

    def test_verify_pairing_code_success(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        self.assertIsNotNone(token)
        self.assertTrue(token.startswith("session_"))

    def test_verify_pairing_code_wrong_identity(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "wrong_user")
        self.assertIsNone(token)

    def test_verify_pairing_code_wrong_code(self):
        token = self.auth.verify_pairing_code("000000", "user123")
        self.assertIsNone(token)

    def test_verify_pairing_code_consumed(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        self.auth.verify_pairing_code(code, "user123")
        token2 = self.auth.verify_pairing_code(code, "user123")
        self.assertIsNone(token2)

    def test_verify_pairing_code_expired(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        self.auth.pairing_codes[code]["expires_at"] = time.time() - 1
        token = self.auth.verify_pairing_code(code, "user123")
        self.assertIsNone(token)

    def test_validate_session_token_success(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        result = self.auth.validate_session_token(token)
        self.assertIsNotNone(result)
        self.assertEqual(result["identity"], "user123")
        self.assertEqual(result["channel"], "telegram")

    def test_validate_session_token_invalid(self):
        result = self.auth.validate_session_token("session_bogus")
        self.assertIsNone(result)

    def test_validate_session_token_expired(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        self.auth.session_tokens[token]["expires_at"] = time.time() - 1
        result = self.auth.validate_session_token(token)
        self.assertIsNone(result)

    def test_validate_session_token_updates_last_used(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        before = self.auth.session_tokens[token]["last_used"]
        time.sleep(0.01)
        self.auth.validate_session_token(token)
        after = self.auth.session_tokens[token]["last_used"]
        self.assertGreater(after, before)

    def test_validate_session_token_respects_absolute_expiry(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        self.auth.session_tokens[token]["absolute_expires_at"] = time.time() - 1
        result = self.auth.validate_session_token(token)
        self.assertIsNone(result)

    def test_validate_session_token_does_not_extend_beyond_absolute_expiry(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        now = time.time()
        self.auth.session_tokens[token]["absolute_expires_at"] = now + 10
        self.auth.validate_session_token(token)
        self.assertLessEqual(self.auth.session_tokens[token]["expires_at"], now + 10.1)

    def test_cleanup_expired(self):
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        code2 = self.auth.generate_pairing_code("user456", "webex")
        self.auth.pairing_codes[code2]["expires_at"] = time.time() - 1
        self.auth.session_tokens[token]["expires_at"] = time.time() - 1
        self.auth.cleanup_expired()
        self.assertNotIn(code2, self.auth.pairing_codes)
        self.assertNotIn(token, self.auth.session_tokens)


class TestRateLimiter(unittest.TestCase):

    def setUp(self):
        from agent_manager import RateLimiter
        self.limiter = RateLimiter()

    def test_allow_under_limit(self):
        allowed = self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        self.assertTrue(allowed)

    def test_block_over_limit(self):
        for _ in range(3):
            self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        allowed = self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        self.assertFalse(allowed)

    def test_different_ips_independent(self):
        for _ in range(3):
            self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        allowed = self.limiter.check("5.6.7.8", "pairing", max_requests=3, window=900)
        self.assertTrue(allowed)

    def test_different_endpoints_independent(self):
        for _ in range(3):
            self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        allowed = self.limiter.check("1.2.3.4", "execute", max_requests=60, window=60)
        self.assertTrue(allowed)

    def test_expired_entries_dont_count(self):
        self.limiter.records.setdefault("1.2.3.4", {}).setdefault("pairing", [])
        self.limiter.records["1.2.3.4"]["pairing"] = [time.time() - 1000, time.time() - 1000, time.time() - 1000]
        allowed = self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
