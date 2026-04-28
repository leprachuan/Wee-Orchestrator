"""Regression test for issue #28: AuthManager.validate_shared_key() timing safety.

Verifies that validate_shared_key() uses hmac.compare_digest() instead of ==,
which prevents timing side-channel attacks on the Bearer token comparison.
"""

import hmac
import inspect
import os
import sys

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

from agent_manager import AuthManager


def test_issue_28_validate_shared_key_uses_compare_digest():
    """validate_shared_key must use hmac.compare_digest, not ==."""
    source = inspect.getsource(AuthManager.validate_shared_key)
    assert (
        "hmac.compare_digest" in source
    ), "validate_shared_key must use hmac.compare_digest() for timing-safe comparison"
    assert (
        "==" not in source
    ), "validate_shared_key must NOT use == for key comparison (timing side-channel)"


def test_issue_28_valid_key_accepted():
    """Correct shared key must be accepted."""
    mgr = AuthManager(shared_key="testsecret")
    assert mgr.validate_shared_key("shared_testsecret") is True


def test_issue_28_wrong_key_rejected():
    """Wrong key must be rejected."""
    mgr = AuthManager(shared_key="testsecret")
    assert mgr.validate_shared_key("shared_wrongkey") is False


def test_issue_28_missing_prefix_rejected():
    """Token without 'shared_' prefix must be rejected."""
    mgr = AuthManager(shared_key="testsecret")
    assert mgr.validate_shared_key("testsecret") is False
    assert mgr.validate_shared_key("bearer_testsecret") is False
    assert mgr.validate_shared_key("") is False


def test_issue_28_empty_key_safe():
    """Empty token after prefix must be rejected even if shared_key is also empty string."""
    mgr = AuthManager(shared_key="nonempty")
    assert mgr.validate_shared_key("shared_") is False
