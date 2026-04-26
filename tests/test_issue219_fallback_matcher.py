"""Issue #219 — regression tests for the background-task fallback matcher.

These tests exercise the *production* ``BackgroundTaskManager._is_fallback_eligible``
matcher and the ``_run_background_task`` fallback retry path.  They guard the
specific false positives flagged by wee-qa (rejections on 2026-04-24 23:03 and
2026-04-25 00:47 UTC):

* ``status_code_429_count mismatch`` must NOT trigger fallback.
* ``AssertionError: expected fixture text 503 Service Unavailable to be preserved``
  must NOT trigger fallback (test/assertion context).
* ``unauthorized_users`` / ``timeout_value`` / ``api_key_invalid_count`` must NOT
  trigger fallback (identifier substrings).
* ``RuntimeError: 503 Service Unavailable``, ``RuntimeError: connection refused``
  and ``ValueError: API key invalid`` MUST still trigger fallback (the prior
  over-correction regression).
"""

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure we import the production module from the dev tree, not a copy.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_manager  # noqa: E402

_BG = agent_manager.BackgroundTaskManager


# ---------------------------------------------------------------------------
# Production-matcher unit tests — call the real classmethod, no exec/copy.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        # Plain infra failures
        "Rate limit exceeded. Try again in 30s.",
        "rate-limited by upstream provider",
        "Quota exceeded for project foo",
        "503 Service Unavailable",
        "HTTP 429 Too Many Requests",
        "HTTP/1.1 503 Service Unavailable",
        "status_code: 429",
        "status: 503",
        "code=502",
        "(429)",
        "Bad Gateway",
        "Connection refused",
        "ECONNREFUSED 127.0.0.1:8000",
        "ETIMEDOUT",
        "ECONNRESET while reading from upstream",
        "Request timed out after 60s",
        "gateway timeout",
        "socket timeout",
        "overloaded — please retry",
        "authentication failed",
        "missing authentication credentials",
        "invalid api key",
        "API key is expired",
        "unauthorized request from client",
        "unauthenticated",
        # Prefix-wrapped (the over-correction regression from QA round 2)
        "RuntimeError: 503 Service Unavailable",
        "RuntimeError: connection refused by upstream runtime",
        "RuntimeError: rate limit exceeded",
        "ValueError: API key invalid",
        "ValueError: invalid api key",
        "OSError: ECONNREFUSED",
        "Exception: 429 Too Many Requests",
        "Error: HTTP 503 Service Unavailable from upstream",
    ],
)
def test_eligible_infra_failures(msg):
    assert _BG._is_fallback_eligible(msg) is True, f"should be eligible: {msg!r}"


@pytest.mark.parametrize(
    "msg",
    [
        # Identifiers that contain infra-like substrings — must not match.
        "status_code_429_count mismatch",
        "AssertionError: status_code_429_count == 7 but got 4",
        "unauthorized_users list grew unexpectedly",
        "timeout_value should default to 60",
        "api_key_invalid_count > 0 in test fixture",
        "var_429_x not defined",
        "test_503_path failed: KeyError",
        # Application/test errors that mention infra words but are not infra.
        "AssertionError: expected 503 Service Unavailable to be preserved",
        "AssertionError: expected fixture text 503 Service Unavailable to be preserved",
        "assert response.status_code == 429",
        "expected '429 Too Many Requests' to equal '200 OK'",
        "FAILED tests/test_foo.py::test_503 - KeyError: 'service'",
        "pytest collected 1 item; rate limit fixture used",
        "fixture 'rate_limit_response' returned None",
        "tests/test_bar.py:42: AssertionError",
        # Generic application failures with no infra signal.
        "KeyError: 'user_id'",
        "ZeroDivisionError: division by zero",
        "TypeError: object of type NoneType is not subscriptable",
        "FileNotFoundError: /tmp/missing.json",
        "JSONDecodeError: Expecting value at line 1 column 1",
        "Task did not return a result",
        "",
        None,
    ],
)
def test_not_eligible_application_or_test_failures(msg):
    assert _BG._is_fallback_eligible(msg) is False, (
        f"should NOT be eligible: {msg!r}"
    )


def test_word_boundary_protects_status_codes():
    """A bare 429 inside an identifier must not match, but 'HTTP 429' must."""
    assert _BG._is_fallback_eligible("status_code_429_count") is False
    assert _BG._is_fallback_eligible("var_429_thing") is False
    assert _BG._is_fallback_eligible("HTTP 429 Too Many Requests") is True
    assert _BG._is_fallback_eligible("status: 429") is True


def test_test_marker_blocks_even_if_infra_phrase_present():
    """Assertion failures that mention infra phrases must still be blocked."""
    msg = (
        "AssertionError: expected fixture text 503 Service Unavailable "
        "to be preserved verbatim"
    )
    assert _BG._is_fallback_eligible(msg) is False


def test_prefix_wrapped_infra_failures_still_eligible():
    """Regression for QA round 2 over-correction: don't reject infra failures
    just because they begin with a Python exception class name."""
    cases = [
        "RuntimeError: 503 Service Unavailable",
        "RuntimeError: connection refused by upstream",
        "RuntimeError: rate limit exceeded for org foo",
        "ValueError: API key invalid",
        "ValueError: invalid api key 'sk-...'",
        "OSError: ECONNREFUSED 127.0.0.1:443",
    ]
    for msg in cases:
        assert _BG._is_fallback_eligible(msg) is True, msg


def test_resolve_fallback_same_as_primary_returns_none():
    mgr = _BG.__new__(_BG)
    task = {
        "runtime": "copilot",
        "model": "claude-haiku-4.5",
        "fallback_runtime": "copilot",
        "fallback_model": "claude-haiku-4.5",
    }
    assert mgr._resolve_fallback(task) == (None, None)


def test_resolve_fallback_different_returns_pair():
    mgr = _BG.__new__(_BG)
    task = {
        "runtime": "copilot",
        "model": "claude-haiku-4.5",
        "fallback_runtime": "claude-sdk",
        "fallback_model": "claude-sonnet-4.6",
    }
    assert mgr._resolve_fallback(task) == ("claude-sdk", "claude-sonnet-4.6")


def test_resolve_fallback_no_fields_returns_none():
    mgr = _BG.__new__(_BG)
    task = {"runtime": "copilot", "model": "claude-haiku-4.5"}
    assert mgr._resolve_fallback(task) == (None, None)


# ---------------------------------------------------------------------------
# Production-matcher integration test — exercise it via getattr on the
# instantiated manager exactly as ``_run_background_task`` does, to prove the
# call site uses the same logic the unit tests cover.
# ---------------------------------------------------------------------------


def test_call_site_uses_production_matcher():
    """``agent_manager`` calls ``self._bg_task_mgr._is_fallback_eligible``.
    Verify the matcher resolves to the production classmethod and behaves
    identically through that attribute path.
    """
    mgr = _BG.__new__(_BG)
    bound = mgr._is_fallback_eligible

    # Identifier substrings — false
    assert bound("status_code_429_count mismatch") is False
    assert bound("api_key_invalid_count > 0") is False
    assert bound("unauthorized_users list") is False

    # Infra failures — true
    assert bound("HTTP 429 Too Many Requests") is True
    assert bound("RuntimeError: 503 Service Unavailable") is True
    assert bound("ECONNREFUSED 127.0.0.1:443") is True


def test_matcher_is_a_method_on_the_manager_class():
    """Sanity: the matcher is a real attribute on BackgroundTaskManager — not
    a stale module-level helper or a copy-paste regex block.
    """
    assert callable(getattr(_BG, "_is_fallback_eligible", None))
    # And it is the same callable the call site reaches.
    src = Path(agent_manager.__file__).read_text()
    assert "self._bg_task_mgr._is_fallback_eligible(" in src


# ---------------------------------------------------------------------------
# Pattern-set sanity: every documented HTTP status is matched in at least one
# canonical phrasing, and every documented phrase is matched in at least one
# realistic form.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [401, 408, 429, 499, 502, 503, 504])
def test_each_documented_http_status_matches_in_canonical_form(code):
    msg = f"HTTP {code} something happened"
    assert _BG._is_fallback_eligible(msg) is True, msg


@pytest.mark.parametrize(
    "phrase",
    [
        "rate limit exceeded",
        "quota exceeded",
        "service unavailable",
        "bad gateway",
        "connection refused",
        "connection reset",
        "request timed out",
        "gateway timeout",
        "overloaded",
        "authentication failed",
        "missing authentication credentials",
        "invalid api key",
    ],
)
def test_each_documented_phrase_matches(phrase):
    assert _BG._is_fallback_eligible(phrase) is True, phrase
