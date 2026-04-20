#!/usr/bin/env python3
"""Wee Native Runtime — OpenAI-compatible chat completion backend.

Standalone CLI for use in background tasks. Streams response tokens to stdout.
Supports Ollama, OpenRouter, LM Studio, and any OpenAI-compatible API.

Issue #107: Tool-calling agentic loop — detects tool calls, executes bash/python,
re-sends results to model, and streams the final response.

Usage:
    python3 wee_runtime.py --model MODEL --api-base URL [--api-key KEY] "PROMPT"
    python3 wee_runtime.py --model ollama/qwen3:8b --tools "ask what day it is"
"""

import argparse
import json
import os
import re
import subprocess
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
            resolved_model = model[len(prefix) + 1 :]
            if not resolved_base:
                resolved_base = preset_base
            if not resolved_key and preset_key:
                resolved_key = preset_key
            break

    # Defaults
    if not resolved_base:
        resolved_base = os.environ.get("WEE_API_BASE", "http://192.168.1.101:11434/v1")
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
            output = result.stdout
            if result.returncode != 0 and result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output.strip() or "(no output)"
        elif func_name == "python":
            code = func_args.get("code", "")
            if not code:
                return "Error: No code provided"
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=TOOL_TIMEOUT,
            )
            output = result.stdout
            if result.returncode != 0 and result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output.strip() or "(no output)"
        else:
            return f"Error: Unknown tool {func_name}"
    except subprocess.TimeoutExpired:
        return f"Error: Tool {func_name} timed out after {TOOL_TIMEOUT}s"
    except Exception as e:
        return f"Error executing tool {func_name}: {e}"


# Issue #113: SSH command sanitisation
_SSH_BIN_RE = re.compile(r"\b(ssh|scp|sftp)\b")


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

# Issue #111: Explicit tool capability prompt for Ollama models.
# Many Ollama models ignore JSON tool schemas unless the system prompt
# explicitly tells them about tool availability and usage instructions.
_WEE_TOOL_CAPABILITY_PROMPT = (
    "\n\n[Tool Capabilities]\n"
    "You have the following tools available. Use them to fulfil user requests.\n\n"
    '1. **bash** — Execute shell commands. Parameters: {"command": "<shell command>"}\n'
    "   Use this for: file operations, system commands, SSH, curl, git, etc.\n"
    '2. **python** — Execute Python code. Parameters: {"code": "<python code>"}\n'
    "   Use this for: data processing, calculations, scripting, etc.\n\n"
    "IMPORTANT: You are NOT sandboxed. You CAN and SHOULD use these tools to execute\n"
    "commands, SSH into remote hosts, read/write files, and interact with the system.\n"
    "NEVER claim you cannot execute commands or are in a sandbox — use the tools above.\n"
)


def main():
    parser = argparse.ArgumentParser(
        description="Wee Native Runtime — OpenAI-compatible chat completions"
    )
    parser.add_argument(
        "--model", required=True, help="Model name (e.g., ollama/gemma4:e4b)"
    )
    parser.add_argument("--api-base", default=None, help="API base URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--system-prompt", default="", help="System prompt")
    parser.add_argument(
        "--timeout", type=int, default=300, help="Request timeout in seconds"
    )
    parser.add_argument(
        "--temperature", type=float, default=None, help="Sampling temperature"
    )
    parser.add_argument(
        "--tools",
        action="store_true",
        default=False,
        help="Enable tool calling (bash, python)",
    )
    parser.add_argument("prompt", help="User prompt")
    args = parser.parse_args()

    try:
        from openai import OpenAI
    except ImportError:
        print(
            "Error: openai package not installed. Run: pip install openai",
            file=sys.stderr,
        )
        sys.exit(1)

    import httpx

    client = OpenAI(
        base_url=api_base,
        api_key=api_key,
        timeout=httpx.Timeout(
            timeout=float(args.timeout),
            connect=15.0,
        ),
        max_retries=0,
    )

    messages = []
    # Issue #113: Augment system prompt with anti-hallucination rules
    effective_system_prompt = (args.system_prompt or "") + _ANTI_HALLUCINATION_PROMPT
    # Issue #111: Include tool capability prompt when tools are enabled
    if args.tools:
        effective_system_prompt += _WEE_TOOL_CAPABILITY_PROMPT
    if effective_system_prompt.strip():
        messages.append({"role": "system", "content": effective_system_prompt})
    messages.append({"role": "user", "content": args.prompt})

    create_kwargs = {"stream": True}
    if args.temperature is not None:
        create_kwargs["temperature"] = args.temperature

    config = load_free_model_config()

    try:
        for round_num in range(MAX_TOOL_ROUNDS + 1):
            create_kwargs = {
                "model": model,
                "messages": messages,
                "stream": True,
            }
            if args.temperature is not None:
                create_kwargs["temperature"] = args.temperature
            if round_num < MAX_TOOL_ROUNDS:
                create_kwargs["tools"] = _WEE_TOOLS

            try:
                stream = client.chat.completions.create(**create_kwargs)
            except Exception as tools_err:
                if "tools" in create_kwargs:
                    print(
                        f"[Wee] Tools not supported, retrying without: {tools_err}",
                        file=sys.stderr,
                    )
                    create_kwargs.pop("tools", None)
                    stream = client.chat.completions.create(**create_kwargs)
                else:
                    raise

            round_content = []
            tool_calls_acc = {}

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    token = delta.content
                    round_content.append(token)
                    sys.stdout.write(token)
                    sys.stdout.flush()

                if getattr(delta, "tool_calls", None):
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_call_counter += 1
                            tool_calls_acc[idx] = {
                                "id": getattr(tc_delta, "id", None)
                                or f"tc_wee_{tool_call_counter}",
                                "name": "",
                                "arguments": "",
                            }
                        if tc_delta.id:
                            tool_calls_acc[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_acc[idx]["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_acc[idx][
                                    "arguments"
                                ] += tc_delta.function.arguments

            content_text = "".join(round_content)

            if not tool_calls_acc:
                collected_output.append(content_text)
                break

            # Tool calls detected
            print(
                f"\n[Wee] Round {round_num + 1}: {len(tool_calls_acc)} tool call(s)",
                file=sys.stderr,
            )

            assistant_tool_calls = []
            for idx in sorted(tool_calls_acc.keys()):
                tc = tool_calls_acc[idx]
                assistant_tool_calls.append(
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": content_text or None,
                    "tool_calls": assistant_tool_calls,
                }
            )

            for tc_entry in assistant_tool_calls:
                tc_id = tc_entry["id"]
                func_name = tc_entry["function"]["name"]
                func_args_str = tc_entry["function"]["arguments"]

                try:
                    func_args = json.loads(func_args_str)
                except (ValueError, json.JSONDecodeError):
                    func_args = {"raw": func_args_str}

                print(
                    f"[Wee] Tool: {func_name}({json.dumps(func_args)[:200]})",
                    file=sys.stderr,
                )
                # Issue #142: Emit structured JSON to stdout for bg task tool call tracking
                sys.stdout.write(
                    json.dumps(
                        {
                            "__wee_tc__": "start",
                            "id": tc_id,
                            "name": func_name,
                            "input": func_args,
                        }
                    )
                    + "\n"
                )
                sys.stdout.flush()

                tool_result = execute_tool(func_name, func_args)

                # Issue #142: Emit tool result to stdout
                sys.stdout.write(
                    json.dumps(
                        {
                            "__wee_tc__": "done",
                            "id": tc_id,
                            "name": func_name,
                            "output": (tool_result or "")[:500],
                        }
                    )
                    + "\n"
                )
                sys.stdout.flush()

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": tool_result or "No output",
                    }
                )
        else:
            # All rounds had tool calls with no final text
            last_results = [m["content"] for m in messages if m.get("role") == "tool"]
            if last_results:
                fallback = (
                    "Tool execution completed. Last result:\n" + last_results[-1][:2000]
                )
            else:
                fallback = "Max tool rounds reached without final response."
            collected_output.append(fallback)
            sys.stdout.write(fallback)

        sys.stdout.write("\n")
        sys.stdout.flush()

    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"\nError: Wee native runtime failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
