#!/usr/bin/env python3
"""Wee Native Runtime — OpenAI-compatible chat completion backend.

Standalone CLI for use in background tasks. Streams response tokens to stdout.
Supports Ollama, OpenRouter, LM Studio, and any OpenAI-compatible API.

Usage:
    python3 wee_runtime.py --model MODEL --api-base URL [--api-key KEY] "PROMPT"
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


# Provider presets: prefix → (api_base, default_api_key)
PROVIDER_PRESETS = {
    "ollama": ("http://192.168.1.101:11434/v1", "ollama"),
    "openrouter": ("https://openrouter.ai/api/v1", None),
    "lmstudio": ("http://localhost:1234/v1", "lm-studio"),
}

MAX_TOOL_ROUNDS = 10

_WEE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash shell command and return its output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Execute Python 3 code and return the output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The Python code to execute",
                    }
                },
                "required": ["code"],
            },
        },
    },
]


def execute_tool(func_name: str, func_args: dict) -> str:
    """Execute a tool call in the wee standalone runtime.

    Supports bash and python tools via subprocess.
    """
    import subprocess
    try:
        if func_name == "bash":
            command = func_args.get("command", "")
            if not command:
                return "Error: No command provided"
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=120
            )
            output = result.stdout
            if result.stderr:
                output += ("\n" if output else "") + result.stderr
            return output.strip() if output.strip() else f"exit code: {result.returncode}"
        elif func_name == "python":
            code = func_args.get("code", "")
            if not code:
                return "Error: No code provided"
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=120
            )
            output = result.stdout
            if result.stderr:
                output += ("\n" if output else "") + result.stderr
            return output.strip() if output.strip() else f"exit code: {result.returncode}"
        else:
            return f"Error: Unknown tool '{func_name}'. Available: bash, python"
    except subprocess.TimeoutExpired:
        return f"Error: Tool '{func_name}' timed out"
    except Exception as e:
        return f"Error executing {func_name}: {e}"


# Default free model config (used when wee_free_models.json is missing)
_DEFAULT_FREE_CONFIG = {
    "free_model_fallback_chain": [
        "openrouter/free",
        "openrouter/google/gemma-4-31b-it:free",
        "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "openrouter/google/gemma-3-27b-it:free",
        "openrouter/nousresearch/hermes-3-llama-3.1-405b:free",
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
        "openrouter/openai/gpt-oss-120b:free",
        "openrouter/google/gemma-3-12b-it:free",
        "openrouter/meta-llama/llama-3.2-3b-instruct:free",
        "openrouter/google/gemma-3-4b-it:free",
    ],
    "max_retries_per_model": 3,
    "retry_backoff_seconds": [2, 5, 10],
}


def load_free_model_config(config_path: str = None) -> dict:
    """Load wee_free_models.json; fall back to hardcoded defaults."""
    if config_path is None:
        config_path = Path(__file__).parent / "wee_free_models.json"
    try:
        with open(config_path) as f:
            data = json.load(f)
        # Merge with defaults to fill missing keys
        result = dict(_DEFAULT_FREE_CONFIG)
        result.update(data)
        return result
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(_DEFAULT_FREE_CONFIG)


def is_free_openrouter_model(model: str) -> bool:
    """Return True if model is an OpenRouter free model (ends with :free or is openrouter/free)."""
    m = model.lower()
    return m == "openrouter/free" or (m.startswith("openrouter/") and m.endswith(":free"))


def resolve_model_and_endpoint(model: str, api_base: str = None, api_key: str = None):
    """Parse model string and resolve API endpoint.

    Model format: [provider/]model_name
    Examples:
        ollama/gemma4:e4b  → api_base=ollama preset, model=gemma4:e4b
        openrouter/meta-llama/llama-4-scout → api_base=openrouter, model=meta-llama/llama-4-scout
        openrouter/free    → api_base=openrouter, model=free
        gemma4:e4b         → use explicit api_base or default to ollama
    """
    resolved_model = model
    resolved_base = api_base
    resolved_key = api_key

    # Check for provider prefix
    for prefix, (preset_base, preset_key) in PROVIDER_PRESETS.items():
        if model.lower().startswith(f"{prefix}/"):
            resolved_model = model[len(prefix) + 1:]
            if not resolved_base:
                resolved_base = preset_base
            if not resolved_key and preset_key:
                resolved_key = preset_key
            break

    # Defaults
    if not resolved_base:
        resolved_base = os.environ.get(
            "WEE_API_BASE", "http://192.168.1.101:11434/v1"
        )
    if not resolved_key:
        resolved_key = os.environ.get("WEE_API_KEY") or os.environ.get(
            "OPENROUTER_API_KEY"
        )
        if not resolved_key:
            if "openrouter" in (resolved_base or "").lower():
                try:
                    import keyring
                    resolved_key = keyring.get_password("openrouter", "api_key")
                except Exception:
                    pass
            if not resolved_key:
                resolved_key = "ollama"

    return resolved_model, resolved_base, resolved_key


def _call_with_retry(client, resolved_model: str, messages: list, create_kwargs: dict,
                     max_retries: int, backoff_seconds: list, status_prefix: str = ""):
    """Try a single model with exponential backoff on 429. Returns (output_text, error_or_None)."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            stream = client.chat.completions.create(
                model=resolved_model,
                messages=messages,
                **create_kwargs,
            )
            collected = []
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    collected.append(token)
                    sys.stdout.write(token)
                    sys.stdout.flush()
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(collected), None
        except Exception as e:
            err_str = str(e)
            # Only retry on 429; propagate everything else immediately
            if "429" not in err_str and "rate limit" not in err_str.lower():
                return None, e
            last_exc = e
            if attempt < max_retries - 1:
                wait = backoff_seconds[attempt] if attempt < len(backoff_seconds) else backoff_seconds[-1]
                retry_num = attempt + 1
                sys.stdout.write(
                    f"\n⚠️ Rate limited{status_prefix}, retrying in {wait}s... ({retry_num}/{max_retries})\n"
                )
                sys.stdout.flush()
                time.sleep(wait)

    return None, last_exc


def run_with_fallback(model: str, messages: list, create_kwargs: dict,
                      api_key: str, timeout: int, config: dict = None):
    """Main entry: run with 429 retry + fallback chain for free OpenRouter models."""
    from openai import OpenAI
    import httpx

    if config is None:
        config = load_free_model_config()

    max_retries = config.get("max_retries_per_model", 3)
    backoff = config.get("retry_backoff_seconds", [2, 5, 10])
    fallback_chain = config.get("free_model_fallback_chain", [])

    resolved_model, api_base, resolved_key = resolve_model_and_endpoint(
        model, api_key=api_key
    )
    if resolved_key == "ollama" and api_key and api_key != "ollama":
        resolved_key = api_key

    # Non-free models: single attempt, no retry/fallback
    if not is_free_openrouter_model(model):
        client = OpenAI(
            base_url=api_base,
            api_key=resolved_key,
            timeout=httpx.Timeout(timeout),
            max_retries=0,
        )
        stream = client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            **create_kwargs,
        )
        collected = []
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                collected.append(token)
                sys.stdout.write(token)
                sys.stdout.flush()
        sys.stdout.write("\n")
        sys.stdout.flush()
        return "".join(collected)

    # Free OpenRouter model: build the attempt chain
    # Start with the requested model, then continue with fallback chain (skipping requested)
    attempt_chain = [model] + [m for m in fallback_chain if m.lower() != model.lower()]

    openrouter_key = resolved_key
    if openrouter_key == "ollama":
        openrouter_key = os.environ.get("WEE_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
        if not openrouter_key:
            try:
                import keyring
                openrouter_key = keyring.get_password("openrouter", "api_key") or ""
            except Exception:
                openrouter_key = ""

    openrouter_base = "https://openrouter.ai/api/v1"
    client = OpenAI(
        base_url=openrouter_base,
        api_key=openrouter_key,
        timeout=httpx.Timeout(timeout),
        max_retries=0,
    )

    for idx, attempt_model in enumerate(attempt_chain):
        r_model, _, _ = resolve_model_and_endpoint(attempt_model)
        output, err = _call_with_retry(
            client, r_model, messages, create_kwargs,
            max_retries=max_retries,
            backoff_seconds=backoff,
            status_prefix=f" on {attempt_model}",
        )
        if output is not None:
            if idx > 0:
                short = attempt_model.split("/")[-1]
                sys.stdout.write(f"\n✓ Using {short}\n")
                sys.stdout.flush()
            return output

        # This model exhausted retries
        if idx + 1 < len(attempt_chain):
            next_model = attempt_chain[idx + 1]
            short_next = next_model.split("/")[-1]
            sys.stdout.write(f"\n⚠️ Still rate limited, falling back to {short_next}...\n")
            sys.stdout.flush()

    # All models exhausted
    sys.stdout.write(
        "\n❌ All free model fallbacks exhausted. "
        "Please try again later or switch to a paid model.\n"
    )
    sys.stdout.flush()
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="Wee Native Runtime — OpenAI-compatible chat completions"
    )
    parser.add_argument("--model", required=True, help="Model name (e.g., ollama/gemma4:e4b)")
    parser.add_argument("--api-base", default=None, help="API base URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--system-prompt", default="", help="System prompt")
    parser.add_argument("--timeout", type=int, default=300, help="Request timeout in seconds")
    parser.add_argument("--temperature", type=float, default=None, help="Sampling temperature")
    parser.add_argument("--tools", action="store_true", help="Enable tool calling (bash, python)")
    parser.add_argument("--session-id", default="", help="Session ID for usage logging")
    parser.add_argument("prompt", help="User prompt")
    args = parser.parse_args()

    try:
        from openai import OpenAI
    except ImportError:
        print("Error: openai package not installed. Run: pip install openai", file=sys.stderr)
        sys.exit(1)

    messages = []
    if args.system_prompt:
        messages.append({"role": "system", "content": args.system_prompt})
    messages.append({"role": "user", "content": args.prompt})

    create_kwargs = {"stream": True}
    if args.temperature is not None:
        create_kwargs["temperature"] = args.temperature

    config = load_free_model_config()

    try:
        run_with_fallback(
            model=args.model,
            messages=messages,
            create_kwargs=create_kwargs,
            api_key=args.api_key,
            timeout=args.timeout,
            config=config,
        )
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"\nError: Wee native runtime failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
