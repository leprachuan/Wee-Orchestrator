"""
Regression / feature tests for Issue #506 — LLM model router.

Part A tests llm_router.py in isolation (no agent_manager import at all):
config load/save/validate, cooldown tracking, prompt building, tolerant JSON
parsing, and LLMRouter.route()'s full decision/validation/fallback logic.

Part B tests the agent_manager.py integration points: runtime registration
("router" appears in / is excluded from available runtimes based on config),
config_schemas validation, and SessionManager.run_router() built as a minimal
double (per the tests/test_issue125_429_retry.py `_make_mgr` pattern) with
_dispatch_single_runtime mocked so no real runtime is ever invoked.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("API_SHARED_KEY", "test_key_123")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import llm_router  # noqa: E402


# ---------------------------------------------------------------------------
# Part A — llm_router.py in isolation
# ---------------------------------------------------------------------------


def _cfg(**overrides):
    cfg = json.loads(json.dumps(llm_router.DEFAULT_ROUTER_CONFIG))
    cfg["allowlist"] = [
        {"runtime": "claude-sdk", "model": "opus", "hint": "complex coding"},
        {"runtime": "copilot", "model": "haiku", "hint": "quick questions"},
    ]
    cfg["fallback"] = {"runtime": "copilot", "model": "auto"}
    cfg.update(overrides)
    return cfg


class _FakeConfig:
    """Stand-in for RouterConfig that returns a fixed dict, no filesystem."""

    def __init__(self, cfg):
        self._cfg = cfg

    def load(self):
        return self._cfg


def _identity_resolver(name, rt):
    return name


class TestRouterConfig:
    def test_load_missing_file_returns_disabled_defaults(self, tmp_path):
        cfg = llm_router.RouterConfig(tmp_path / "nope.json")
        data = cfg.load()
        assert data["enabled"] is False
        assert data["allowlist"] == []

    def test_save_then_load_round_trips(self, tmp_path):
        path = tmp_path / "router_config.json"
        cfg = llm_router.RouterConfig(path)
        valid = _cfg(enabled=True)
        cfg.save(valid)
        reloaded = llm_router.RouterConfig(path).load()
        assert reloaded["enabled"] is True
        assert reloaded["allowlist"][0]["runtime"] == "claude-sdk"

    def test_save_rejects_invalid_config(self, tmp_path):
        cfg = llm_router.RouterConfig(tmp_path / "router_config.json")
        with pytest.raises(llm_router.RouterConfigError):
            cfg.save({"enabled": True, "allowlist": []})

    def test_validate_rejects_recursive_brain(self):
        bad = _cfg(brain={"runtime": "router", "model": "x"})
        errors = llm_router.RouterConfig.validate(bad)
        assert any("brain.runtime" in e for e in errors)

    def test_validate_rejects_recursive_allowlist_entry(self):
        bad = _cfg()
        bad["allowlist"].append({"runtime": "router", "model": "x"})
        errors = llm_router.RouterConfig.validate(bad)
        assert any("cannot target 'router'" in e for e in errors)

    def test_validate_rejects_missing_template_placeholders(self):
        bad = _cfg(prompt_template="no placeholders here")
        errors = llm_router.RouterConfig.validate(bad)
        assert any("prompt_template" in e for e in errors)

    def test_validate_rejects_bad_timeout(self):
        bad = _cfg(timeout_seconds=0)
        errors = llm_router.RouterConfig.validate(bad)
        assert any("timeout_seconds" in e for e in errors)

    def test_validate_rejects_empty_allowlist(self):
        bad = _cfg(allowlist=[])
        errors = llm_router.RouterConfig.validate(bad)
        assert any("allowlist" in e for e in errors)


class TestRuntimeCooldownTracker:
    def test_mark_and_check(self):
        tracker = llm_router.RuntimeCooldownTracker()
        assert not tracker.is_cooling("copilot")
        tracker.mark_failure("copilot", "429", cooldown_seconds=60)
        assert tracker.is_cooling("copilot")
        assert "copilot" in tracker.status()

    def test_cooldown_expires(self, monkeypatch):
        tracker = llm_router.RuntimeCooldownTracker()
        base = 1000.0
        monkeypatch.setattr(llm_router.time, "time", lambda: base)
        tracker.mark_failure("copilot", "429", cooldown_seconds=10)
        assert tracker.is_cooling("copilot")
        monkeypatch.setattr(llm_router.time, "time", lambda: base + 11)
        assert not tracker.is_cooling("copilot")
        assert tracker.status() == {}


class TestParsing:
    def test_parse_plain_json(self):
        assert llm_router.parse_decision_json('{"runtime":"copilot","model":"auto","reason":"x"}') == {
            "runtime": "copilot", "model": "auto", "reason": "x",
        }

    def test_parse_fenced_json(self):
        raw = '```json\n{"runtime": "copilot", "model": "auto", "reason": "y"}\n```'
        assert llm_router.parse_decision_json(raw)["runtime"] == "copilot"

    def test_parse_json_with_surrounding_prose(self):
        raw = 'Sure, here is my pick:\n{"runtime": "claude-sdk", "model": "opus", "reason": "z"}\nHope that helps!'
        assert llm_router.parse_decision_json(raw)["model"] == "opus"

    def test_parse_garbage_returns_none(self):
        assert llm_router.parse_decision_json("not json at all") is None

    def test_parse_empty_returns_none(self):
        assert llm_router.parse_decision_json("") is None
        assert llm_router.parse_decision_json(None) is None


class TestIsInfraFailureText:
    @pytest.mark.parametrize("text", [
        "Error 429: rate limited", "Error: quota exceeded", "401 unauthorized",
        "503 Service Unavailable", "connection refused", "request timed out",
        "model overloaded, please retry",
    ])
    def test_detects_infra_failures(self, text):
        assert llm_router.is_infra_failure_text(text)

    def test_ignores_application_exceptions(self):
        assert not llm_router.is_infra_failure_text("AssertionError: expected 429 in output")

    def test_ignores_unrelated_text(self):
        assert not llm_router.is_infra_failure_text("Here are the search results you asked for.")

    def test_handles_empty(self):
        assert not llm_router.is_infra_failure_text("")
        assert not llm_router.is_infra_failure_text(None)


class TestBuildHelpers:
    def test_allowlist_table_includes_hints(self):
        table = llm_router.build_allowlist_table(_cfg()["allowlist"])
        assert "claude-sdk" in table and "complex coding" in table

    def test_stickiness_hint_within_window(self):
        last = {"runtime": "copilot", "model": "auto", "ts": time.time()}
        hint = llm_router.build_stickiness_hint(last, {"enabled": True, "prefer_same_runtime": True, "window_seconds": 900})
        assert "copilot" in hint

    def test_stickiness_hint_expired(self):
        last = {"runtime": "copilot", "model": "auto", "ts": time.time() - 10000}
        hint = llm_router.build_stickiness_hint(last, {"enabled": True, "window_seconds": 900})
        assert hint == ""

    def test_stickiness_hint_disabled(self):
        last = {"runtime": "copilot", "model": "auto", "ts": time.time()}
        hint = llm_router.build_stickiness_hint(last, {"enabled": False})
        assert hint == ""


class TestLLMRouterRoute:
    def _router(self, cfg):
        return llm_router.LLMRouter(_FakeConfig(cfg), llm_router.RuntimeCooldownTracker())

    def test_valid_decision_in_allowlist(self):
        router = self._router(_cfg())
        decision = router.route(
            prompt="write me a recursive quicksort",
            last_routed=None,
            runtime_available=lambda rt: True,
            invoke_brain=lambda rt, m, p, t: '{"runtime":"claude-sdk","model":"opus","reason":"coding task"}',
            resolve_model=_identity_resolver,
        )
        assert decision.runtime == "claude-sdk"
        assert decision.model == "opus"
        assert decision.source == "router"

    def test_disallowed_runtime_falls_back(self):
        router = self._router(_cfg())
        decision = router.route(
            prompt="hi",
            last_routed=None,
            runtime_available=lambda rt: True,
            invoke_brain=lambda rt, m, p, t: '{"runtime":"devin","model":"x","reason":"nope"}',
            resolve_model=_identity_resolver,
        )
        assert decision.source == "fallback"
        assert decision.runtime == "copilot"
        assert decision.model == "auto"

    def test_invalid_json_falls_back(self):
        router = self._router(_cfg())
        decision = router.route(
            prompt="hi",
            last_routed=None,
            runtime_available=lambda rt: True,
            invoke_brain=lambda rt, m, p, t: "I refuse to answer in JSON",
            resolve_model=_identity_resolver,
        )
        assert decision.source == "fallback"
        assert decision.reason == "unparseable brain reply"

    def test_brain_exception_falls_back_never_raises(self):
        router = self._router(_cfg())

        def boom(rt, m, p, t):
            raise TimeoutError("brain took too long")

        decision = router.route(
            prompt="hi", last_routed=None,
            runtime_available=lambda rt: True,
            invoke_brain=boom,
            resolve_model=_identity_resolver,
        )
        assert decision.source == "fallback"
        assert "TimeoutError" in decision.reason

    def test_model_outside_pair_is_normalized_not_trusted(self):
        router = self._router(_cfg())
        # Brain picks an eligible runtime but a model that isn't the
        # allowlisted model for that runtime — must not be trusted verbatim.
        decision = router.route(
            prompt="hi", last_routed=None,
            runtime_available=lambda rt: True,
            invoke_brain=lambda rt, m, p, t: '{"runtime":"copilot","model":"some-random-model","reason":"x"}',
            resolve_model=lambda name, rt: name,
        )
        assert decision.runtime == "copilot"
        assert decision.model == "haiku"  # allowlisted model for copilot, not the brain's free text

    def test_no_eligible_runtimes_uses_fallback(self):
        # Allowlist candidates are unavailable; the fallback pair (copilot,
        # not itself in the allowlist) is what should be used.
        cfg = _cfg(allowlist=[{"runtime": "claude-sdk", "model": "opus", "hint": "x"}])
        router = self._router(cfg)
        decision = router.route(
            prompt="hi", last_routed=None,
            runtime_available=lambda rt: rt == "copilot",  # only fallback's runtime is up
            invoke_brain=lambda *a: (_ for _ in ()).throw(AssertionError("brain should not be called")),
            resolve_model=_identity_resolver,
        )
        assert decision.source == "fallback"
        assert decision.runtime == "copilot"

    def test_single_eligible_pair_skips_brain_call(self):
        cfg = _cfg(allowlist=[{"runtime": "copilot", "model": "auto", "hint": "only one"}])
        router = self._router(cfg)
        decision = router.route(
            prompt="hi", last_routed=None,
            runtime_available=lambda rt: True,
            invoke_brain=lambda *a: (_ for _ in ()).throw(AssertionError("brain should not be called")),
            resolve_model=_identity_resolver,
        )
        assert decision.source == "single"
        assert decision.runtime == "copilot"

    def test_zero_eligible_and_no_fallback_returns_empty_decision(self):
        router = self._router(_cfg())
        decision = router.route(
            prompt="hi", last_routed=None,
            runtime_available=lambda rt: False,  # nothing is up, not even fallback
            invoke_brain=lambda *a: (_ for _ in ()).throw(AssertionError("brain should not be called")),
            resolve_model=_identity_resolver,
        )
        assert decision.runtime == ""
        assert decision.model == ""
        assert "fallback also unavailable" in decision.reason

    def test_cooldown_excludes_runtime_from_eligibility(self):
        cooldowns = llm_router.RuntimeCooldownTracker()
        cooldowns.mark_failure("claude-sdk", "429", cooldown_seconds=60)
        router = llm_router.LLMRouter(_FakeConfig(_cfg()), cooldowns)
        # Only copilot remains eligible -> single-pair shortcut, no brain call.
        decision = router.route(
            prompt="hi", last_routed=None,
            runtime_available=lambda rt: True,
            invoke_brain=lambda *a: (_ for _ in ()).throw(AssertionError("brain should not be called")),
            resolve_model=_identity_resolver,
        )
        assert decision.runtime == "copilot"
        assert decision.source == "single"

    def test_router_itself_never_eligible(self):
        cfg = _cfg()
        cfg["allowlist"].append({"runtime": "router", "model": "x"})
        router = self._router(cfg)
        eligible = router.eligible_pairs(cfg["allowlist"], lambda rt: True)
        assert all(e["runtime"] != "router" for e in eligible)


# ---------------------------------------------------------------------------
# Part B — agent_manager.py integration points
# ---------------------------------------------------------------------------

os.environ.setdefault("WEE_AGENT_DIR", "/opt/wee-dev")

import agent_manager  # noqa: E402


def _make_mgr():
    """Minimal SessionManager double, mirroring tests/test_issue125_429_retry.py."""
    mgr = agent_manager.SessionManager.__new__(agent_manager.SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 60
    mgr._stream_buffers = {}
    mgr.AGENTS = {
        "orchestrator": {"path": "/opt", "primary_runtime": "claude", "primary_model": "haiku"},
    }
    return mgr


@pytest.fixture(autouse=True)
def _reset_router_singletons():
    """Router config/cooldown singletons are module-level; reset between tests."""
    agent_manager._router_config = None
    agent_manager._runtime_cooldown_tracker = None
    agent_manager._llm_router = None
    yield
    agent_manager._router_config = None
    agent_manager._runtime_cooldown_tracker = None
    agent_manager._llm_router = None


class TestRuntimeRegistration:
    def test_router_absent_when_disabled(self, monkeypatch):
        monkeypatch.setenv("WEE_ROUTER_ENABLED", "0")
        assert agent_manager.check_runtime_available("router") is False
        ids = [rt["id"] for rt in agent_manager.get_available_runtimes()]
        assert "router" not in ids

    def test_router_present_when_enabled_and_brain_available(self, monkeypatch, tmp_path):
        cfg_path = tmp_path / "router_config.json"
        cfg = _cfg(enabled=True, brain={"runtime": "copilot", "model": "auto"})
        agent_manager._router_config = llm_router.RouterConfig(cfg_path)
        agent_manager._router_config.save(cfg)
        monkeypatch.setenv("WEE_ROUTER_ENABLED", "1")
        # "copilot" (the configured brain) availability shouldn't depend on
        # whether the real CLI is installed on the test machine.
        monkeypatch.setattr(agent_manager.shutil, "which", lambda name: "/usr/bin/copilot" if name == "copilot" else None)

        assert agent_manager.check_runtime_available("router") is True
        ids = [rt["id"] for rt in agent_manager.get_available_runtimes()]
        assert "router" in ids

    def test_router_absent_when_brain_unavailable(self, monkeypatch, tmp_path):
        cfg_path = tmp_path / "router_config.json"
        cfg = _cfg(enabled=True, brain={"runtime": "devin", "model": "x"})
        agent_manager._router_config = llm_router.RouterConfig(cfg_path)
        agent_manager._router_config.save(cfg)
        monkeypatch.setenv("WEE_ROUTER_ENABLED", "1")
        monkeypatch.setattr(agent_manager.shutil, "which", lambda name: None)

        assert agent_manager.check_runtime_available("router") is False

    def test_env_override_wins_over_config_file(self, monkeypatch, tmp_path):
        cfg_path = tmp_path / "router_config.json"
        agent_manager._router_config = llm_router.RouterConfig(cfg_path)
        agent_manager._router_config.save(_cfg(enabled=False))
        monkeypatch.setenv("WEE_ROUTER_ENABLED", "true")
        assert agent_manager.is_router_enabled() is True

        monkeypatch.setenv("WEE_ROUTER_ENABLED", "false")
        assert agent_manager.is_router_enabled() is False


class TestRunRouter:
    def test_disabled_falls_back_to_agent_primary(self, monkeypatch):
        mgr = _make_mgr()
        monkeypatch.setattr(agent_manager, "is_router_enabled", lambda: False)
        monkeypatch.setattr(mgr, "get_or_create_session_data", lambda sid: {})
        monkeypatch.setattr(mgr, "_resume_state_for_runtime", lambda *a, **k: False)
        monkeypatch.setattr(mgr, "get_most_recent_session_id", lambda *a, **k: None)
        monkeypatch.setattr(mgr, "update_session_field", lambda *a, **k: None)

        captured = {}

        def fake_dispatch(runtime, prompt, model, agent, session_id, can_resume, n8n_sid, timeout, render, mode):
            captured["runtime"] = runtime
            captured["model"] = model
            return "ok"

        monkeypatch.setattr(mgr, "_dispatch_single_runtime", fake_dispatch)

        out = mgr.run_router("hello", "auto", "orchestrator", None, False, "sess1", 60, "plain")
        assert out == "ok"
        assert captured["runtime"] == "claude"
        assert captured["model"] == "haiku"

    def test_valid_route_dispatches_to_decision_and_persists_router_last(self, monkeypatch, tmp_path):
        mgr = _make_mgr()
        cfg_path = tmp_path / "router_config.json"
        agent_manager._router_config = llm_router.RouterConfig(cfg_path)
        agent_manager._router_config.save(_cfg(enabled=True))
        monkeypatch.setenv("WEE_ROUTER_ENABLED", "1")

        monkeypatch.setattr(mgr, "get_or_create_session_data", lambda sid: {"router_last": None})
        monkeypatch.setattr(mgr, "_resume_state_for_runtime", lambda *a, **k: False)
        monkeypatch.setattr(mgr, "get_most_recent_session_id", lambda *a, **k: None)
        monkeypatch.setattr(mgr, "get_model_from_name", lambda name, rt: name)
        monkeypatch.setattr(agent_manager, "check_runtime_available", lambda rt: True)
        monkeypatch.setattr(agent_manager, "get_disabled_runtimes_manager", lambda: MagicMock(is_disabled=lambda rt: False))

        persisted = {}
        monkeypatch.setattr(mgr, "update_session_field", lambda sid, field, value: persisted.__setitem__(field, value))

        dispatch_calls = []

        def fake_dispatch(runtime, prompt, model, agent, session_id, can_resume, n8n_sid, timeout, render, mode=None):
            dispatch_calls.append(runtime)
            if runtime == "claude-sdk":  # the real target
                return "solved it"
            return '{"runtime":"claude-sdk","model":"opus","reason":"coding task"}'  # the brain

        monkeypatch.setattr(mgr, "_dispatch_single_runtime", fake_dispatch)

        out = mgr.run_router("write me quicksort", "auto", "orchestrator", None, False, "sess2", 60, "plain")
        assert out == "solved it"
        assert "claude-sdk" in dispatch_calls
        assert persisted["router_last"]["runtime"] == "claude-sdk"

    def test_infra_failure_triggers_cooldown_and_single_fallback_retry(self, monkeypatch, tmp_path):
        mgr = _make_mgr()
        cfg_path = tmp_path / "router_config.json"
        cfg = _cfg(enabled=True, allowlist=[{"runtime": "claude-sdk", "model": "opus", "hint": "x"}])
        agent_manager._router_config = llm_router.RouterConfig(cfg_path)
        agent_manager._router_config.save(cfg)
        monkeypatch.setenv("WEE_ROUTER_ENABLED", "1")

        monkeypatch.setattr(mgr, "get_or_create_session_data", lambda sid: {"router_last": None})
        monkeypatch.setattr(mgr, "_resume_state_for_runtime", lambda *a, **k: False)
        monkeypatch.setattr(mgr, "get_most_recent_session_id", lambda *a, **k: None)
        monkeypatch.setattr(mgr, "get_model_from_name", lambda name, rt: name)
        monkeypatch.setattr(agent_manager, "check_runtime_available", lambda rt: True)
        monkeypatch.setattr(agent_manager, "get_disabled_runtimes_manager", lambda: MagicMock(is_disabled=lambda rt: False))
        monkeypatch.setattr(mgr, "update_session_field", lambda *a, **k: None)

        dispatch_calls = []

        def fake_dispatch(runtime, prompt, model, agent, session_id, can_resume, n8n_sid, timeout, render, mode=None):
            dispatch_calls.append(runtime)
            if runtime == "claude-sdk":
                return "Error: 429 rate limited"
            if runtime == "copilot":  # fallback
                return "recovered via fallback"
            return "unused"

        monkeypatch.setattr(mgr, "_dispatch_single_runtime", fake_dispatch)

        out = mgr.run_router("hi", "auto", "orchestrator", None, False, "sess3", 60, "plain")
        assert out == "recovered via fallback"
        assert dispatch_calls.count("claude-sdk") == 1  # single eligible pair -> no brain call, one attempt
        assert dispatch_calls.count("copilot") == 1  # exactly one fallback retry, not a loop

        tracker = agent_manager.get_runtime_cooldown_tracker()
        assert tracker.is_cooling("claude-sdk")


class TestRouterConfigSchemaIntegration:
    def test_config_schemas_validate_router_config_accepts_valid(self):
        from config_schemas import validate_router_config

        validate_router_config(_cfg())  # should not raise

    def test_config_schemas_validate_router_config_rejects_recursive_brain(self):
        from config_schemas import validate_router_config
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            validate_router_config(_cfg(brain={"runtime": "router", "model": "x"}))

    def test_config_schemas_validate_router_config_rejects_missing_placeholders(self):
        from config_schemas import validate_router_config
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            validate_router_config(_cfg(prompt_template="no placeholders"))


class TestNoAutoRuntimeReintroduced:
    """Guards against reintroducing the 'auto' runtime name removed in #84
    (see tests/test_auto_runtime_removed.py) while adding 'router'."""

    def test_auto_still_absent_router_present_when_enabled(self, monkeypatch, tmp_path):
        cfg_path = tmp_path / "router_config.json"
        agent_manager._router_config = llm_router.RouterConfig(cfg_path)
        agent_manager._router_config.save(_cfg(enabled=True, brain={"runtime": "copilot", "model": "auto"}))
        monkeypatch.setenv("WEE_ROUTER_ENABLED", "1")

        ids = [rt["id"] for rt in agent_manager.get_all_runtimes()]
        assert "auto" not in ids
        assert "router" in ids
