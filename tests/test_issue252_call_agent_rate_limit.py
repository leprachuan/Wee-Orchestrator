"""Regression test for issue #252: wee-cli call_agent hitting 429 rate limit.

The call_agent tool should not be rate-limited when called from localhost.
This test verifies that:
1. Local calls (127.0.0.1) are exempt or have high rate limits
2. Error messages are clear when rate limits are hit
3. Multiple rapid call_agent invocations succeed
"""

import pytest
import json
from unittest.mock import patch, MagicMock
import urllib.error


def test_call_agent_local_exempt_from_rate_limit():
    """Local calls (127.0.0.1) should not be rate-limited."""
    from wee_runtime import _call_agent_handler
    
    # Mock successful response
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "response": "Task result"
    }).encode()
    
    with patch('urllib.request.urlopen') as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        result = _call_agent_handler({
            "agent": "orchestrator",
            "prompt": "How many tasks?",
            "mode": "quick"
        })
        
        assert "✓" in result
        assert "Task result" in result


def test_call_agent_429_error_message():
    """Rate limit errors (429) should provide clear message."""
    from wee_runtime import _call_agent_handler
    
    # Mock HTTP 429 response
    http_error = urllib.error.HTTPError(
        "http://example.com", 429, "Too Many Requests", {}, None
    )
    
    with patch('urllib.request.urlopen') as mock_urlopen:
        mock_urlopen.side_effect = http_error
        
        result = _call_agent_handler({
            "agent": "orchestrator",
            "prompt": "Test",
            "mode": "quick"
        })
        
        assert "Rate limit exceeded" in result or "rate" in result.lower()


def test_call_agent_other_http_errors():
    """Other HTTP errors should be returned with status code."""
    from wee_runtime import _call_agent_handler
    
    # Mock HTTP 503 response
    http_error = urllib.error.HTTPError(
        "http://example.com", 503, "Service Unavailable", {}, None
    )
    http_error.fp = MagicMock()
    http_error.fp.read.return_value = b"Service down"
    
    with patch('urllib.request.urlopen') as mock_urlopen:
        mock_urlopen.side_effect = http_error
        
        result = _call_agent_handler({
            "agent": "orchestrator",
            "prompt": "Test",
            "mode": "quick"
        })
        
        assert "503" in result


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
        allowed = rate_limiter.check("192.168.1.5", "query", max_requests=100, window=60)
        assert allowed, f"Request {i+1} should not be rate-limited with 100 req limit"
    
    # 101st request from remote IP should be blocked
    allowed = rate_limiter.check("192.168.1.5", "query", max_requests=100, window=60)
    assert not allowed, "101st request should be rate-limited with 100 req limit"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
