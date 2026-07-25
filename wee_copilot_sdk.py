"""Shared Copilot SDK BYOK execution for the Wee runtime.

The public model identifier remains provider-qualified (for example
``ollama/qwen3:8b``).  The provider prefix is removed only when a Copilot SDK
session is created, and the matching OpenAI-compatible endpoint is supplied in
the session's ``provider`` configuration.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional


class WeeCopilotSDKUnavailable(RuntimeError):
    """Raised when the Copilot SDK is disabled or cannot be imported."""


class WeeCopilotSDKError(RuntimeError):
    """Raised when a Copilot SDK BYOK session cannot complete."""


@dataclass(frozen=True)
class WeeProviderRoute:
    qualified_model: str
    model: str
    provider: str
    base_url: str
    api_key: Optional[str]
    wire_api: str = "completions"

    def sdk_provider(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "type": "openai",
            "base_url": self.base_url,
            "wire_api": self.wire_api,
        }
        if self.api_key:
            config["api_key"] = self.api_key
        return config


def _with_v1(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value if value.endswith("/v1") else f"{value}/v1"


def _webui_secret(name: str) -> Optional[str]:
    """Read a credential stored by the WebUI Secret Manager.

    The API deliberately keeps these credentials out of the service
    environment and stores them with ``secret_tool``'s encrypted file backend.
    Runtime consumers therefore need to use the same backend instead of
    assuming every credential is exported as an environment variable.
    """
    try:
        from secret_tool.secret_tool import FileBackend

        result = FileBackend().get(name)
        if result.get("status") == "success":
            credential = result.get("credential")
            if isinstance(credential, str) and credential.strip():
                return credential.strip()
    except Exception:
        # A missing/locked secret store is equivalent to an absent credential.
        # The caller raises the user-facing configuration error.
        pass
    return None


def _openrouter_key(explicit_key: Optional[str] = None) -> Optional[str]:
    key = explicit_key or os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    try:
        import keyring

        key = keyring.get_password("openrouter", "api_key")
    except Exception:
        key = None
    if key:
        return key

    # OPENROUTER_API_KEY is the documented Secret Manager name. Retain the
    # lowercase alias for installations that created it before this contract
    # was documented.
    return _webui_secret("OPENROUTER_API_KEY") or _webui_secret("openrouter_api_key")


def resolve_wee_provider(
    model: str,
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
) -> WeeProviderRoute:
    """Resolve a public Wee model ID to a Copilot SDK BYOK provider route."""
    qualified = (model or "").strip()
    if not qualified:
        raise ValueError("A Wee model is required")

    prefix, separator, remainder = qualified.partition("/")
    provider = prefix.lower() if separator else "custom"
    resolved_model = remainder if separator else qualified

    if provider == "ollama":
        configured = (
            os.environ.get("WEE_OLLAMA_BASE_URL")
            or os.environ.get("WEE_OLLAMA_HOST")
            or os.environ.get("OLLAMA_HOST")
        )
        if not configured:
            host = os.environ.get("OLLAMA_HOSTNAME", "192.168.1.101")
            port = os.environ.get("OLLAMA_PORT", "11434")
            configured = f"http://{host}:{port}"
        base = _with_v1(configured)
        key = api_key or os.environ.get("WEE_OLLAMA_API_KEY")
    elif provider == "openrouter":
        base = (
            os.environ.get("WEE_OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
        ).rstrip("/")
        key = _openrouter_key(api_key)
        if not key:
            raise ValueError(
                "OpenRouter API key not found. Save OPENROUTER_API_KEY in "
                "Wee Secrets, set it in the service environment, or store it "
                "in the system keyring"
            )
    elif provider == "lmstudio":
        configured = os.environ.get("WEE_LMSTUDIO_BASE_URL")
        if not configured:
            host = os.environ.get("LMSTUDIO_HOST", "localhost")
            port = os.environ.get("LMSTUDIO_PORT", "1234")
            configured = f"http://{host}:{port}"
        base = _with_v1(configured)
        key = api_key or os.environ.get("WEE_LMSTUDIO_API_KEY")
    else:
        # Unqualified models retain the legacy WEE_API_BASE behavior.
        base = api_base or os.environ.get("WEE_API_BASE")
        if not base:
            configured = (
                os.environ.get("WEE_OLLAMA_BASE_URL")
                or os.environ.get("WEE_OLLAMA_HOST")
                or "http://192.168.1.101:11434"
            )
            base = _with_v1(configured)
            provider = "ollama"
        else:
            base = base.rstrip("/")
        key = api_key or os.environ.get("WEE_API_KEY")
        if "openrouter" in base.lower():
            provider = "openrouter"
            key = _openrouter_key(key)
            if not key:
                raise ValueError("OpenRouter API key not found")

    wire_api = os.environ.get("WEE_COPILOT_WIRE_API", "completions").lower()
    if wire_api not in ("completions", "responses"):
        wire_api = "completions"
    return WeeProviderRoute(
        qualified_model=qualified,
        model=resolved_model,
        provider=provider,
        base_url=base,
        api_key=key,
        wire_api=wire_api,
    )


def copilot_sdk_enabled() -> bool:
    return os.environ.get("WEE_COPILOT_SDK_ENABLED", "1").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _event_content(event: Any) -> str:
    data = getattr(event, "data", None)
    if isinstance(data, str):
        return data
    if data is None:
        return ""
    for name in ("delta_content", "content", "delta", "text", "message"):
        value = getattr(data, name, None)
        if value:
            return str(value)
        if isinstance(data, dict) and data.get(name):
            return str(data[name])
    return ""


def prefer_longer_text(final_text: Optional[str], streamed_text: Optional[str]) -> str:
    """Choose between a transport's final message and its assembled stream.

    A complete assistant message is normally authoritative, but the
    OpenAI-compatible transport used for Ollama can end a turn on a single
    delta fragment (observed: a turn whose stream was "OK"/"I"/"I" reported
    just "I"). The deltas were forwarded to the caller for live rendering but
    never accumulated, so that fragment became the answer and downstream
    length checks rejected it as unusable.

    Preferring whichever is longer keeps the normal case untouched — a full
    final message is never shorter than its own stream — while recovering the
    content when the final event is only a fragment.
    """
    final = (final_text or "").strip()
    assembled = (streamed_text or "").strip()
    return assembled if len(assembled) > len(final) else final


def _ollama_num_ctx(base_url: str, model: str) -> Optional[int]:
    """Return the effective num_ctx for an Ollama model, or None if unknown.

    Ollama reports a model's *architecture* context (e.g. gemma4.context_length
    = 131072) regardless of what it will actually allocate. What matters is the
    `num_ctx` parameter baked into the model's Modelfile — without it Ollama
    falls back to its own small default, which is the difference between a
    working turn and a degenerate one.
    """
    root = (base_url or "").rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    if not root:
        return None
    try:
        request = urllib.request.Request(
            f"{root}/api/show",
            data=json.dumps({"model": model}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            document = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return None

    for line in str(document.get("parameters") or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "num_ctx":
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


ADEQUATE_NUM_CTX = 16_384


def short_ollama_reply_is_degenerate(num_ctx: Optional[int]) -> bool:
    """Decide whether a very short Ollama reply is breakage or a real answer.

    Length alone cannot tell those apart. "391", "OK", "42" and "Yes" are
    correct answers to questions that ask for brevity, and the previous
    `len < 4` rule discarded all of them. What actually distinguishes the
    failure is *why* the reply is short: a request that does not fit the model's
    allocated context leaves no room to generate, so the turn dies after roughly
    one token.

    So treat a short reply as broken only when the context is missing or too
    small to have produced a real one. When the context is adequate, return the
    model's answer — surfacing a genuinely odd reply beats throwing away a
    correct terse one.
    """
    return num_ctx is None or num_ctx < ADEQUATE_NUM_CTX


def describe_degenerate_ollama_turn(
    model: str, prompt_chars: int, num_ctx: Optional[int]
) -> str:
    """Explain a turn that produced no usable content, and how to fix it.

    The previous message blamed response length ("unusably short response"),
    which is misleading: the usual cause is that the request does not fit the
    model's allocated context, so Ollama has no room left to generate and stops
    after roughly one token. Naming num_ctx turns a mystery into an action.
    """
    detail = (
        f"Ollama model '{model}' returned no usable content. The Wee system "
        f"prompt alone is ~{prompt_chars} characters"
    )
    if num_ctx is None:
        return (
            detail + ", and this model has no num_ctx set in its Modelfile, so "
            "Ollama falls back to its small default and has no room left to "
            "generate. Use a variant with a large num_ctx baked in, or set "
            "OLLAMA_CONTEXT_LENGTH on the Ollama host."
        )
    if num_ctx < 16_384:
        return (
            detail + f", but this model allocates only num_ctx={num_ctx}. That "
            "is too small for the agent prompt, leaving no room to generate. "
            "Use a larger-context variant of this model."
        )
    # Reachable only if a caller asks for a description despite an adequate
    # context; the guard itself no longer rejects those turns.
    return (
        detail + f" and num_ctx={num_ctx} looks adequate, so the cause is "
        "elsewhere — check the Ollama host logs for this request."
    )


async def execute_wee_copilot_async(
    *,
    prompt: str,
    route: WeeProviderRoute,
    working_directory: Optional[str] = None,
    timeout: float = 300,
    session_id: Optional[str] = None,
    resume: bool = False,
    tools: Optional[list[Any]] = None,
    enable_tools: bool = True,
    event_callback: Optional[Callable[[str, Any], None]] = None,
) -> tuple[str, Optional[str]]:
    """Execute one Wee turn through the Copilot SDK BYOK transport."""
    if not copilot_sdk_enabled():
        raise WeeCopilotSDKUnavailable("Copilot SDK execution is disabled")

    try:
        from copilot import CopilotClient, PermissionHandler, SessionEventType
    except ImportError as exc:
        raise WeeCopilotSDKUnavailable(
            "github-copilot-sdk>=1.0.7 is not installed"
        ) from exc

    collected: list[str] = []
    errors: list[str] = []
    # Streaming deltas were forwarded to the caller but never accumulated, so a
    # provider that only ever emits deltas (Ollama, via the OpenAI-compatible
    # transport) left `result_text` holding whatever single fragment
    # send_and_wait happened to return. Keep the assembled stream as a fallback.
    streamed: list[str] = []

    def on_event(event: Any) -> None:
        event_type = getattr(event, "type", None)
        if event_type in (
            SessionEventType.ASSISTANT_STREAMING_DELTA,
            SessionEventType.ASSISTANT_MESSAGE_DELTA,
        ):
            content = _event_content(event)
            if content:
                streamed.append(content)
                if event_callback:
                    event_callback("chunk", content)
        elif event_type == SessionEventType.ASSISTANT_MESSAGE:
            content = _event_content(event)
            if content:
                collected.append(content)
        elif event_type in (
            SessionEventType.TOOL_EXECUTION_START,
            SessionEventType.TOOL_EXECUTION_COMPLETE,
            SessionEventType.COMMAND_EXECUTE,
        ):
            if event_callback:
                event_callback("tool_call", event)
        elif event_type in (
            SessionEventType.SESSION_ERROR,
            SessionEventType.MODEL_CALL_FAILURE,
        ):
            message = _event_content(event) or str(getattr(event, "data", event))
            errors.append(message)

    client = CopilotClient()
    async with client:
        session_kwargs: dict[str, Any] = {
            "on_permission_request": PermissionHandler.approve_all,
            "model": route.model,
            "provider": route.sdk_provider(),
            "working_directory": working_directory,
            "on_event": on_event,
            "streaming": True,
        }
        if tools:
            session_kwargs["tools"] = tools
        if not enable_tools:
            session_kwargs["available_tools"] = []

        try:
            if resume and session_id:
                session = await client.resume_session(session_id, **session_kwargs)
            else:
                session = await client.create_session(**session_kwargs)
            actual_session_id = getattr(session, "session_id", None)
            result = await session.send_and_wait(prompt, timeout=float(timeout))
            result_text = _event_content(result) if result else ""
            if not result_text and collected:
                result_text = collected[-1]
            result_text = prefer_longer_text(result_text, "".join(streamed))
            if not result_text and errors:
                raise WeeCopilotSDKError(errors[-1])
            if event_callback:
                event_callback("done", result_text)
            disconnect_result = session.disconnect()
            if asyncio.iscoroutine(disconnect_result):
                await disconnect_result
            if route.provider == "ollama" and len(result_text.strip()) < 4:
                num_ctx = _ollama_num_ctx(route.base_url, route.model)
                if short_ollama_reply_is_degenerate(num_ctx):
                    raise WeeCopilotSDKError(
                        describe_degenerate_ollama_turn(
                            route.model, len(prompt or ""), num_ctx
                        )
                    )
            return result_text, str(actual_session_id) if actual_session_id else None
        except Exception as exc:
            if isinstance(exc, WeeCopilotSDKError):
                raise
            raise WeeCopilotSDKError(f"{type(exc).__name__}: {exc}") from exc


def execute_wee_copilot(**kwargs: Any) -> tuple[str, Optional[str]]:
    """Synchronous wrapper used by the API runtime and standalone CLI."""
    return asyncio.run(execute_wee_copilot_async(**kwargs))
