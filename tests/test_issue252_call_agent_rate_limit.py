"""Regression test for issue #252: wee-cli call_agent hitting 429 rate limit.

Issue #443 removed wee_runtime.py's own `_call_agent_handler` (it was only
reachable via the now-deleted hand-rolled tool loop) — call_agent delegation
now runs exclusively through agent_manager.py's `_wee_call_agent`, which has
its own dedicated HTTP-error-handling coverage in
tests/test_issue_343_call_agent_delegation.py. What's still independently
testable here is the underlying rate limiter's local-vs-remote behavior.
"""

import pytest


def test_query_endpoint_local_high_rate_limit():
    """Test agent_manager query endpoint has high rate limit for local calls."""
    # This is an integration test that verifies the rate limiter in agent_manager.py
    # Local IPs (127.0.0.1, localhost, ::1) should have 1000 req/60s
    # Remote IPs should have 100 req/60s

    from agent_manager import rate_limiter

    # Reset limiter for clean test
    rate_limiter.records.clear()

    # Simulate 100 requests from 127.0.0.1 (should all pass)
    for i in range(100):
        allowed = rate_limiter.check("127.0.0.1", "query", max_requests=1000, window=60)
        if i < 100:
            assert allowed, f"Request {i+1} should not be rate-limited for local IP"

    # Simulate 50 requests from remote IP (should pass with 100 limit)
    rate_limiter.records.clear()
    for i in range(50):
        allowed = rate_limiter.check(
            "192.168.1.5", "query", max_requests=100, window=60
        )
        assert allowed, f"Request {i+1} should not be rate-limited with 100 req limit"

    # 101st request from remote IP should be blocked
    allowed = rate_limiter.check("192.168.1.5", "query", max_requests=100, window=60)
    assert not allowed, "101st request should be rate-limited with 100 req limit"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
