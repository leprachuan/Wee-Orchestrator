# Fixture for issue #249 tests
import os
import pytest

# Part of #471: dozens of test files set API_SHARED_KEY at module level via
# os.environ.setdefault(...), each with its own unique per-file literal
# ("test_key_178", "test_key_29", "issue192concurrency", ...), apparently on
# the assumption that setdefault made this safe. It doesn't: setdefault only
# protects against overwriting a value that's already set -- it does nothing
# to stop a DIFFERENT file's setdefault from being the first to run and
# locking in ITS literal for the rest of the process. Whichever file pytest
# collects first "wins", and every other file's hardcoded
# "Bearer shared_<its-own-literal>" then mismatches the live key and gets a
# 401 instead of whatever it actually meant to test.
#
# conftest.py is always imported before any sibling test module in its
# directory, in every pytest invocation mode (whole suite or a single file),
# so setting the canonical value here -- before any test file's own
# setdefault gets a chance to run -- makes this deterministic instead of
# collection-order-dependent. Individual files' own setdefault calls become
# harmless no-ops; what still needs fixing per-file is any hardcoded
# "Bearer shared_<unique-literal>" that assumed its own value would win.
os.environ.setdefault("API_SHARED_KEY", "test_key_123")


@pytest.fixture
def mock_agents_config():
    """Create a mock agents configuration with primary_runtime/primary_model."""
    return {
        "agents": [
            {
                "name": "orchestrator",
                "path": "/opt/",
                "description": "Main orchestrator",
                "primary_runtime": "claude",
                "primary_model": "haiku",
                "fallback_runtime": "copilot",
                "fallback_model": "auto",
                "max_concurrent": 1,
            },
            {
                "name": "research",
                "path": "/opt/research",
                "description": "Research agent",
                "primary_runtime": "opencode",
                "primary_model": "nvidia/llama-3.1-nemotron",
                "fallback_runtime": "claude",
                "fallback_model": "sonnet",
                "max_concurrent": 1,
            },
            {
                "name": "wee-dev",
                "path": "/opt/wee-dev",
                "description": "Wee Orchestrator developer",
                "primary_runtime": "copilot",
                "primary_model": "gpt-5.4-mini",
                "fallback_runtime": "claude",
                "fallback_model": "opus",
                "max_concurrent": 1,
            },
        ]
    }
