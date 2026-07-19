"""Regression coverage for Issue #425: Wee uses Copilot SDK BYOK."""

import asyncio
import sys
import types
from unittest.mock import MagicMock, patch

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


def test_api_wee_runtime_uses_shared_sdk_executor(monkeypatch):
    from agent_manager import SessionManager

    class Tool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setitem(sys.modules, "copilot", types.SimpleNamespace(Tool=Tool))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    mgr = SessionManager.__new__(SessionManager)
    mgr.AGENTS = {"orchestrator": {"path": "/tmp"}}
    mgr.command_timeout = 60
    mgr._stream_buffers = {}
    mgr.get_or_create_session_data = MagicMock(return_value={"channel": "webui"})
    mgr.build_agent_context_prompt = MagicMock(return_value="context")
    mgr._wee_augment_system_prompt_with_tools = MagicMock(
        side_effect=lambda prompt: prompt
    )
    mgr._wee_execute_tool = MagicMock(return_value="ok")
    mgr.update_session_field = MagicMock()

    with patch(
        "wee_copilot_sdk.execute_wee_copilot",
        return_value=("sdk api response", "wee-sdk-session"),
    ) as execute:
        result = mgr.run_wee_native(
            "hello",
            "openrouter/anthropic/claude-sonnet-4",
            "orchestrator",
            None,
            False,
            "api-session-425",
        )

    assert result == "sdk api response"
    assert execute.call_args.kwargs["route"].provider == "openrouter"
    assert execute.call_args.kwargs["route"].model == "anthropic/claude-sonnet-4"
    mgr.update_session_field.assert_called_once_with(
        "api-session-425", "wee_copilot_session_id", "wee-sdk-session"
    )


def test_legacy_api_endpoint_uses_configured_ollama(monkeypatch):
    from agent_manager import SessionManager

    monkeypatch.setenv("WEE_OLLAMA_HOST", "ollama-dev.example:11434")
    mgr = SessionManager.__new__(SessionManager)
    base_url, api_key, model = mgr._wee_resolve_endpoint(
        "ollama/qwen3:8b", None, None
    )

    assert model == "qwen3:8b"
    assert base_url == "http://ollama-dev.example:11434/v1"
    assert api_key == "ollama"


def test_cancellation_propagates_instead_of_being_swallowed(monkeypatch):
    """Issue #425 follow-up: asyncio cancellation during send_and_wait must
    propagate as CancelledError, not get wrapped into WeeCopilotSDKError by
    the broad except-Exception handler in execute_wee_copilot_async."""

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

    class Session:
        session_id = "sdk-session-cancel"

        async def send_and_wait(self, prompt, timeout):
            raise asyncio.CancelledError()

        def disconnect(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create_session(self, **kwargs):
            return Session()

    fake_copilot = types.SimpleNamespace(
        CopilotClient=Client,
        PermissionHandler=PermissionHandler,
        SessionEventType=Events,
    )
    monkeypatch.setitem(sys.modules, "copilot", fake_copilot)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    route = resolve_wee_provider("openrouter/meta-llama/llama-3.3-70b")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            execute_wee_copilot_async(
                prompt="hello",
                route=route,
                working_directory="/tmp",
                timeout=42,
                enable_tools=False,
            )
        )
