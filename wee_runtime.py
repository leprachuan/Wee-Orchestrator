#!/usr/bin/env python3
"""Wee Native Runtime — OpenAI-compatible chat completion backend.

Standalone CLI for use in background tasks. Streams response tokens to stdout.
Supports Ollama, OpenRouter, LM Studio, and any OpenAI-compatible API.

Issue #107: Tool-calling agentic loop — detects tool calls, executes bash/python,
re-sends results to model, and streams the final response.

Issue #112: Empty synthesis fallback — if LLM returns no text after tool execution,
surfaces last tool result instead of empty response.

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

# Provider presets: prefix → (api_base, default_api_key)
PROVIDER_PRESETS = {
    "ollama": ("http://192.168.1.101:11434/v1", "ollama"),
    "openrouter": ("https://openrouter.ai/api/v1", None),
    "lmstudio": ("http://localhost:1234/v1", "lm-studio"),
}

# Tool definitions (Issue #107)
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

MAX_TOOL_ROUNDS = 10
TOOL_TIMEOUT = 120  # seconds per tool execution


def resolve_model_and_endpoint(model: str, api_base: str = None, api_key: str = None):
    """Parse model string and resolve API endpoint.

    Model format: [provider/]model_name
    Examples:
        ollama/gemma4:e4b  → api_base=ollama preset, model=gemma4:e4b
        openrouter/meta-llama/llama-4-scout
            → api_base=openrouter, model=meta-llama/llama-4-scout
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
        # Issue #144: Check OPENROUTER_API_KEY env var for OpenRouter first
        if "openrouter" in (resolved_base or "").lower():
            resolved_key = os.environ.get("OPENROUTER_API_KEY")
        if not resolved_key:
            resolved_key = os.environ.get("WEE_API_KEY")
        # Try keyring for OpenRouter
        if not resolved_key and "openrouter" in (resolved_base or "").lower():
            try:
                import keyring

                resolved_key = keyring.get_password("openrouter", "api_key")
            except Exception:
                pass
        # Issue #144: Raise clear error instead of defaulting to "ollama"
        if not resolved_key:
            if "openrouter" in (resolved_base or "").lower():
                print(
                    "Error: OpenRouter API key not found. Set "
                    "OPENROUTER_API_KEY env var or store via keyring.",
                    file=sys.stderr,
                )
                sys.exit(1)
            resolved_key = "ollama"

    return resolved_model, resolved_base, resolved_key


def execute_tool(func_name: str, func_args: dict, permission: str = "auto") -> str:
    """Execute a tool call and return its output (Issues #107, #111, #158).

    Args:
        permission: Controls execution gating.
            "restricted" — blocks all tool execution and returns an error.
            "auto" (default) — executes tools as requested by the model.
            "elevated" — treated identically to "auto" (no privilege escalation
            in CLI contexts).
    """
    if permission == "restricted":
        return (
            "Error: Tool execution blocked by permission level 'restricted'. "
            "Run with --permission auto to enable tool calls."
        )
    try:
        if func_name == "bash":
            command = func_args.get("command", "")
            if not command:
                return "Error: No command provided"
            # Issue #111: Sanitize SSH commands
            command = sanitize_bash_command(command)
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=TOOL_TIMEOUT,
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


# Issue #111: SSH sanitization now wired into execute_tool() (resolves #113 TODO).
def sanitize_bash_command(command: str) -> str:
    """Auto-inject SSH flags to prevent host key verification failures.

    Injects ``-o StrictHostKeyChecking=accept-new`` after ssh/scp/sftp
    when the flag is not already present.  ``accept-new`` is preferred
    over ``no`` because it still rejects CHANGED keys (potential MITM).

    Wired into execute_tool() by Issue #111. Called on every bash
    tool input before execution once wee_runtime.py gains a tool
    execution loop.
    once wee_runtime.py gains a tool execution loop.
    """
    if not command or not _SSH_BIN_RE.search(command):
        return command
    if "StrictHostKeyChecking" in command:
        return command

    def _inject(m):
        return m.group(0) + " -o StrictHostKeyChecking=accept-new"

    return _SSH_BIN_RE.sub(_inject, command, count=0)


# Issue #113: Anti-hallucination system prompt addendum
_ANTI_HALLUCINATION_PROMPT = (
    "\n\n[CRITICAL — Output Integrity Rules]\n"
    "1. NEVER fabricate, invent, or hallucinate command output. If a command "
    "fails or you cannot execute it, report the EXACT error message.\n"
    "2. NEVER provide example or placeholder output and present it as real. "
    'If you show an example, clearly label it as "EXAMPLE (not real output)".\n'
    "3. When a tool call returns an error, relay the error verbatim to the user. "
    "Do NOT attempt to guess what the successful output would have looked like.\n"
    "4. For SSH commands: ALWAYS use ``-o StrictHostKeyChecking=accept-new`` to "
    "avoid host-key verification failures on first connect.\n"
)

# Issue #111: Explicit tool capability prompt for Ollama models.
# Many Ollama models ignore JSON tool schemas unless the system prompt
# explicitly tells them about tool availability and usage instructions.
_WEE_TOOL_CAPABILITY_PROMPT = (
    "\n\n[Tool Capabilities]\n"
    "You have the following tools available. Use them to fulfil user requests.\n\n"
    '1. **bash** — Execute shell commands. Parameters: {"command": "<shell command>"}'  # noqa: E501
    "   Use this for: file operations, system commands, SSH, curl, git, etc.\n"
    '2. **python** — Execute Python code. Parameters: {"code": "<python code>"}\n'
    "   Use this for: data processing, calculations, scripting, etc.\n\n"
    "IMPORTANT: You are NOT sandboxed. You CAN and SHOULD use these tools to execute\n"
    "commands, SSH into remote hosts, read/write files, and interact with the system.\n"
    "NEVER claim you cannot execute commands or are in a sandbox"  # noqa: E501
    " — use the tools above.\n"
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

    model, api_base, api_key = resolve_model_and_endpoint(
        args.model, args.api_base, args.api_key
    )

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

    if not args.tools:
        # Simple streaming (no tool calling)
        create_kwargs = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if args.temperature is not None:
            create_kwargs["temperature"] = args.temperature

        try:
            stream = client.chat.completions.create(**create_kwargs)
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    sys.stdout.write(chunk.choices[0].delta.content)
                    sys.stdout.flush()
            sys.stdout.write("\n")
            sys.stdout.flush()
        except KeyboardInterrupt:
            sys.exit(130)
        except Exception as e:
            print(f"\nError: Wee native runtime failed: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # -- Tool-calling agentic loop (Issue #107) --
    collected_output = []
    tool_call_counter = 0

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
                                "id": (
                                    getattr(tc_delta, "id", None)
                                    or f"tc_wee_{tool_call_counter}"
                                ),
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

                tool_result = execute_tool(func_name, func_args)

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
