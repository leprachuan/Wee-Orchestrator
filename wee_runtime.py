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
import re
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

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
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search the web using SearXNG meta-search engine and return results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5, max: 20)",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["json", "text"],
                        "description": "Output format: 'json' returns structured results, 'text' returns a plain summary",
                    },
                },
                "required": ["q"],
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
        openrouter/meta-llama/llama-4-scout → api_base=openrouter, model=meta-llama/llama-4-scout
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


SEARCH_TIMEOUT = 30  # seconds for SearXNG queries
SEARCH_MAX_CHARS = 2000  # max result chars to avoid context bloat


def _execute_search(func_args: dict) -> str:
    """Handle search tool calls via SearXNG (Issue #255).

    Queries the self-hosted SearXNG instance and returns results in the
    requested format. Returns an empty result gracefully on failure rather
    than raising an exception, to avoid breaking the agentic loop.

    Args:
        func_args: Dict with 'q' (required), 'count' (optional, default 5),
                   'format' (optional: 'json'|'text', default 'text').
    """
    import json as _json

    query = (func_args.get("q") or "").strip()
    if not query:
        return "Error: search query ('q') is required"

    count = min(int(func_args.get("count") or 5), 20)
    output_format = (func_args.get("format") or "text").lower()

    searxng_url = os.environ.get("WEE_SEARXNG_URL", "http://192.168.1.100:8888")
    searxng_url = searxng_url.rstrip("/")

    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "categories": "general",
        "language": "en",
    })
    url = f"{searxng_url}/search?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Wee-Runtime/1.0"})
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            data = _json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as e:
        return f"Search unavailable ({searxng_url}): {e.reason}"
    except Exception as e:
        return f"Search error: {e}"

    results = data.get("results", [])[:count]
    if not results:
        return "No results found."

    if output_format == "json":
        slim = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": (r.get("content", "") or "")[:300],
            }
            for r in results
        ]
        raw = _json.dumps(slim, ensure_ascii=False)
        return raw[:SEARCH_MAX_CHARS]

    # Plain text summary
    lines = [f"Search results for: {query}"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url_r = r.get("url", "")
        snippet = (r.get("content", "") or "").strip()[:200]
        lines.append(f"\n{i}. {title}\n   {url_r}\n   {snippet}")

    summary = "\n".join(lines)
    return summary[:SEARCH_MAX_CHARS]



def execute_tool(func_name: str, func_args: dict) -> str:
    """Execute a tool call and return its output (Issue #107)."""
    try:
        if func_name == "bash":
            command = func_args.get("command", "")
            if not command:
                return "Error: No command provided"
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
        elif func_name == "search":
            return _execute_search(func_args)
        else:
            return f"Error: Unknown tool {func_name}"
    except subprocess.TimeoutExpired:
        return f"Error: Tool {func_name} timed out after {TOOL_TIMEOUT}s"
    except Exception as e:
        return f"Error executing tool {func_name}: {e}"



# SSH command sanitisation (Issue #113)
_SSH_BIN_RE = re.compile(r"\b(ssh|scp|sftp)\b")


def sanitize_bash_command(command: str) -> str:
    """Auto-inject SSH flags to prevent host key verification failures.

    Injects -o StrictHostKeyChecking=accept-new after ssh/scp/sftp
    when the flag is not already present.
    """
    if not command or not _SSH_BIN_RE.search(command):
        return command
    if "StrictHostKeyChecking" in command:
        return command

    def _inject(m):
        return m.group(0) + " -o StrictHostKeyChecking=accept-new"

    return _SSH_BIN_RE.sub(_inject, command, count=0)



# Anti-hallucination system prompt addendum (Issue #113)
_ANTI_HALLUCINATION_PROMPT = (
    "\n\n[CRITICAL — Output Integrity Rules]\n"
    "1. NEVER fabricate, invent, or hallucinate command output. If a command "
    "fails or you cannot execute it, report the EXACT error message.\n"
    "2. NEVER provide example or placeholder output and present it as real. "
    'If you show an example, clearly label it as "EXAMPLE (not real output)".\n'
    "3. When a tool call returns an error, relay the error verbatim to the user. "
    "Do NOT attempt to guess what the successful output would have looked like.\n"
    "4. For SSH commands: ALWAYS use -o StrictHostKeyChecking=accept-new to "
    "avoid host-key verification failures on first connect.\n"
)

# Explicit tool capability prompt for models that ignore JSON tool schemas (Issue #111)
_WEE_TOOL_CAPABILITY_PROMPT = (
    "\n\n[Tool Capabilities]\n"
    "You have the following tools available. Use them to fulfil user requests.\n\n"
    '1. **bash** — Execute shell commands. Parameters: {"command": "<shell command>"}\n'
    "   Use this for: file operations, system commands, SSH, curl, git, etc.\n"
    '2. **python** — Execute Python code. Parameters: {"code": "<python code>"}\n'
    "   Use this for: data processing, calculations, scripting, etc.\n"
    '3. **search** — Web search via SearXNG. Parameters: {"q": "<query>", "count": 5, "format": "text|json"}\n'
    "   Use this for: current events, web lookups, product info, general knowledge queries.\n\n"
    "IMPORTANT: You are NOT sandboxed. You CAN and SHOULD use these tools to execute\n"
    "commands, SSH into remote hosts, read/write files, and interact with the system.\n"
    "NEVER claim you cannot execute commands or are in a sandbox"
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
        help="Enable tool calling (bash, python, search, call_agent)",
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

    client = OpenAI(
        base_url=api_base,
        api_key=api_key,
        timeout=args.timeout,
    )

    messages = []
    if args.system_prompt:
        messages.append({"role": "system", "content": args.system_prompt})
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
