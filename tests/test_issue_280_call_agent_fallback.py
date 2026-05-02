"""Regression tests for Issue #280: call_agent fallback and response display."""

import json
import sys
import os
from unittest import mock
from urllib.error import HTTPError, URLError
import io

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wee_runtime


class MockResponse:
    """Mock HTTP response."""

    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestCallAgentFallback:
    """Test call_agent fallback behavior."""

    def test_call_agent_primary_success(self):
        """Test successful call with primary runtime."""
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = MockResponse(
                json.dumps({"response": "Hello from orchestrator"}).encode()
            )

            result = wee_runtime._call_agent_handler(
                {"agent": "wee-dev", "prompt": "test", "mode": "quick"}
            )

            assert "[Wee]" in result
            assert "Response" in result
            assert "Hello from orchestrator" in result

    def test_call_agent_primary_fails_fallback_succeeds(self):
        """Test fallback retry after primary runtime fails with 429."""
        call_count = [0]

        def mock_urlopen_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call fails with 429 (rate limit)
                error = HTTPError(
                    url="https://test.com",
                    code=429,
                    msg="Too Many Requests",
                    hdrs={},
                    fp=io.BytesIO(b"Rate limited"),
                )
                raise error
            else:
                # Second call (fallback) succeeds
                return MockResponse(
                    json.dumps({"response": "Hello from fallback"}).encode()
                )

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
            result = wee_runtime._call_agent_handler(
                {"agent": "wee-dev", "prompt": "test", "mode": "quick"}
            )

            assert "[Fallback]" in result or "Hello from fallback" in result
            assert "Response" in result
            assert call_count[0] == 2

    def test_call_agent_all_retries_fail(self):
        """Test error handling when all runtimes fail."""

        def mock_urlopen_side_effect(*args, **kwargs):
            error = HTTPError(
                url="https://test.com",
                code=429,
                msg="Too Many Requests",
                hdrs={},
                fp=io.BytesIO(b"Rate limited"),
            )
            raise error

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
            result = wee_runtime._call_agent_handler(
                {"agent": "wee-dev", "prompt": "test", "mode": "quick"}
            )

            assert "[Wee] ERROR" in result

    def test_call_agent_timeout_fallback(self):
        """Test timeout triggers fallback."""
        call_count = [0]

        def mock_urlopen_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise URLError("Connection timed out")
            else:
                return MockResponse(
                    json.dumps({"response": "Timeout fallback success"}).encode()
                )

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
            result = wee_runtime._call_agent_handler(
                {"agent": "wee-dev", "prompt": "test", "mode": "quick"}
            )

            assert "Response" in result
            assert "Timeout fallback success" in result


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
