"""Tests for issue #75: background task agent field must not be inherited from session map.

Tests _compute_bg_task_defaults by extracting it directly from agent_manager.py
without importing the full module (which requires a running server environment).
"""
import ast
import os
import re
import sys

import pytest

_AM_PATH = os.path.join(os.path.dirname(__file__), "..", "agent_manager.py")

def _load_fn():
    with open(_AM_PATH, "r") as f:
        source = f.read()
    m = re.search(
        r"(def _compute_bg_task_defaults\(.*?)(?=\ndef |\nclass |\Z)",
        source,
        re.DOTALL,
    )
    assert m, "_compute_bg_task_defaults not found in agent_manager.py"
    ns = {}
    exec(m.group(1), ns)  # noqa: S102
    return ns["_compute_bg_task_defaults"]


_compute_bg_task_defaults = _load_fn()


def _sm(sessions):
    """Build a session_map from a list of session dicts."""
    return {f"sid_{i}": s for i, s in enumerate(sessions)}


# ---------------------------------------------------------------------------
# Core: agent field must NEVER appear in defaults
# ---------------------------------------------------------------------------

def test_agent_not_inherited_same_channel():
    d = _compute_bg_task_defaults(
        _sm([{"identity": "foster", "channel": "tg", "agent": "wee-dev",
              "runtime": "copilot", "model": "claude-sonnet-4.6"}]),
        "foster", "tg",
    )
    assert "agent" not in d, f"'agent' leaked into defaults: {d}"


def test_agent_not_inherited_cross_channel():
    d = _compute_bg_task_defaults(
        _sm([{"identity": "foster", "channel": "webex", "agent": "email_triage",
              "runtime": "copilot", "model": "claude-haiku-4.5"}]),
        "foster", "tg",
    )
    assert "agent" not in d, f"'agent' leaked cross-channel: {d}"


def test_agent_not_inherited_multiple_sessions():
    d = _compute_bg_task_defaults(
        _sm([
            {"identity": "foster", "channel": "tg",    "agent": "wee-dev"},
            {"identity": "foster", "channel": "tg",    "agent": "research"},
            {"identity": "foster", "channel": "webex", "agent": "devops"},
        ]),
        "foster", "tg",
    )
    assert "agent" not in d, f"agent leaked from multi-session map: {d}"


# ---------------------------------------------------------------------------
# Safe fields ARE inherited
# ---------------------------------------------------------------------------

def test_runtime_and_model_inherited():
    d = _compute_bg_task_defaults(
        _sm([{"identity": "foster", "channel": "tg", "agent": "wee-dev",
              "runtime": "copilot", "model": "claude-opus-4.6"}]),
        "foster", "tg",
    )
    assert d.get("runtime") == "copilot"
    assert d.get("model") == "claude-opus-4.6"
    assert "agent" not in d


def test_notification_preference_inherited():
    d = _compute_bg_task_defaults(
        _sm([{"identity": "foster", "channel": "tg", "agent": "orchestrator",
              "notification_preference": "telegram"}]),
        "foster", "tg",
    )
    assert d.get("notification_preference") == "telegram"
    assert "agent" not in d


# ---------------------------------------------------------------------------
# Same-channel priority
# ---------------------------------------------------------------------------

def test_same_channel_preferred_over_cross_channel():
    d = _compute_bg_task_defaults(
        _sm([
            {"identity": "foster", "channel": "webex", "runtime": "claude",   "model": "haiku"},
            {"identity": "foster", "channel": "tg",    "runtime": "copilot",  "model": "claude-opus-4.6"},
        ]),
        "foster", "tg",
    )
    assert d.get("runtime") == "copilot"
    assert d.get("model") == "claude-opus-4.6"


# ---------------------------------------------------------------------------
# Identity isolation
# ---------------------------------------------------------------------------

def test_different_identity_not_matched():
    d = _compute_bg_task_defaults(
        _sm([{"identity": "leslie", "channel": "tg",
              "agent": "family_knowledge", "runtime": "claude", "model": "haiku"}]),
        "foster", "tg",
    )
    assert d == {}, f"Leaked other user's session: {d}"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_session_map():
    assert _compute_bg_task_defaults({}, "foster", "tg") == {}


def test_legacy_string_session_no_crash():
    d = _compute_bg_task_defaults({"s0": "legacy_string_id"}, "foster", "tg")
    assert d == {}


def test_session_without_identity_not_matched():
    d = _compute_bg_task_defaults(
        _sm([{"channel": "tg", "agent": "wee-dev", "runtime": "copilot"}]),
        "foster", "tg",
    )
    assert d == {}
