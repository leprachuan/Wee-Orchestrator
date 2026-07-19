"""Regression tests for Issue #429: Wee resolves WebUI-managed secrets."""

import sys
import types

import pytest

import wee_copilot_sdk
from secret_tool import secret_tool as secret_tool_module
from wee_copilot_sdk import resolve_wee_provider


def test_openrouter_route_uses_webui_secret_manager(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "keyring",
        types.SimpleNamespace(get_password=lambda *_args: None),
    )

    requested = []

    class FakeFileBackend:
        def get(self, name):
            requested.append(name)
            if name == "OPENROUTER_API_KEY":
                return {"status": "success", "credential": "stored-webui-key"}
            return {"status": "failure", "credential": None}

    monkeypatch.setattr(
        secret_tool_module,
        "FileBackend",
        FakeFileBackend,
    )

    route = resolve_wee_provider("openrouter/openai/gpt-5.6-terra")

    assert route.api_key == "stored-webui-key"
    assert requested == ["OPENROUTER_API_KEY"]
    assert route.sdk_provider()["api_key"] == "stored-webui-key"


def test_missing_openrouter_secret_stops_before_unauthenticated_fallback(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(wee_copilot_sdk, "_openrouter_key", lambda _key=None: None)

    with pytest.raises(ValueError, match="Save OPENROUTER_API_KEY in Wee Secrets"):
        resolve_wee_provider("openrouter/openai/gpt-5.6-terra")
