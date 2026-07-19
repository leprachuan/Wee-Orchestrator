"""Regression coverage for Issue #425: Wee uses Copilot SDK BYOK."""

import asyncio
import sys
import types

import pytest

import wee_copilot_sdk
from wee_copilot_sdk import execute_wee_copilot_async, resolve_wee_provider
from wee_model_discovery import WeeModelDiscovery, _load_hosts


def test_ollama_route_uses_configured_server(monkeypatch):
    monkeypatch.setenv("WEE_OLLAMA_HOST", "http://ollama-dev.example:11434")
    route = resolve_wee_provider("ollama/qwen3:8b")

    assert route.provider == "ollama"
    assert route.model == "qwen3:8b"
    assert route.base_url == "http://ollama-dev.example:11434/v1"
    assert route.sdk_provider()["wire_api"] == "completions"


def test_openrouter_route_strips_only_provider_prefix(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    route = resolve_wee_provider("openrouter/anthropic/claude-sonnet-4")

    assert route.provider == "openrouter"
    assert route.model == "anthropic/claude-sonnet-4"
    assert route.base_url == "https://openrouter.ai/api/v1"
    assert route.sdk_provider()["api_key"] == "test-key"


def test_openrouter_route_requires_secret(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(wee_copilot_sdk, "_openrouter_key", lambda explicit=None: None)

    with pytest.raises(ValueError, match="OpenRouter API key"):
        resolve_wee_provider("openrouter/openai/gpt-4.1")


def test_default_discovery_includes_configured_ollama_and_openrouter(monkeypatch):
    monkeypatch.delenv("WEE_DISCOVERY_HOSTS", raising=False)
    monkeypatch.setenv("WEE_OLLAMA_HOST", "http://dev-ollama:11434/v1")

    hosts = _load_hosts()

    assert hosts[0]["url"] == "http://dev-ollama:11434"
    assert hosts[0]["prefix"] == "ollama"
    assert any(host["prefix"] == "openrouter" for host in hosts)


def test_discovery_returns_collision_safe_qualified_ids(monkeypatch):
    discovery = WeeModelDiscovery()

    def fake_fetch(url):
        if url.endswith("/api/tags"):
            return {"models": [{"name": "qwen3:8b"}]}
        return {"data": [{"id": "anthropic/claude-sonnet-4"}]}

    monkeypatch.setattr(discovery, "_fetch_json", fake_fetch)
    monkeypatch.delenv("WEE_DISCOVERY_HOSTS", raising=False)
    result = discovery.discover_all(force=True)
    all_ids = [model for models in result.values() for model in models]

    assert "ollama/qwen3:8b" in all_ids
    assert "openrouter/anthropic/claude-sonnet-4" in all_ids


def test_sdk_session_receives_byok_provider_and_resolved_model(monkeypatch):
    recorded = {}

    class Events:
        ASSISTANT_STREAMING_DELTA = "assistant.delta"
        ASSISTANT_MESSAGE_DELTA = "assistant.message_delta"
        ASSISTANT_MESSAGE = "assistant.message"
        TOOL_EXECUTION_START = "tool.start"
        TOOL_EXECUTION_COMPLETE = "tool.complete"
        COMMAND_EXECUTE = "command.execute"
        SESSION_ERROR = "session.error"
        MODEL_CALL_FAILURE = "model.failure"

    class PermissionHandler:
        approve_all = object()

    class Result:
        data = types.SimpleNamespace(content="SDK response")

    class Session:
        session_id = "sdk-session-425"

        async def send_and_wait(self, prompt, timeout):
            recorded["prompt"] = prompt
            recorded["timeout"] = timeout
            return Result()

        def disconnect(self):
            recorded["disconnected"] = True

    class Client:
        def __init__(self, **kwargs):
            recorded["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create_session(self, **kwargs):
            recorded["session"] = kwargs
            return Session()

    fake_copilot = types.SimpleNamespace(
        CopilotClient=Client,
        PermissionHandler=PermissionHandler,
        SessionEventType=Events,
    )
    monkeypatch.setitem(sys.modules, "copilot", fake_copilot)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    route = resolve_wee_provider("openrouter/meta-llama/llama-3.3-70b")

    output, session_id = asyncio.run(
        execute_wee_copilot_async(
            prompt="hello",
            route=route,
            working_directory="/tmp",
            timeout=42,
            enable_tools=False,
        )
    )

    assert output == "SDK response"
    assert session_id == "sdk-session-425"
    assert recorded["session"]["model"] == "meta-llama/llama-3.3-70b"
    assert recorded["session"]["provider"] == {
        "type": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "wire_api": "completions",
        "api_key": "test-key",
    }
    assert recorded["session"]["available_tools"] == []
    assert recorded["disconnected"] is True
