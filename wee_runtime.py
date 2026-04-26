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
# Use env vars for Ollama and LM Studio to allow customization
_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "192.168.1.101")
_OLLAMA_PORT = os.environ.get("OLLAMA_PORT", "11434")
_LMSTUDIO_HOST = os.environ.get("LMSTUDIO_HOST", "localhost")
_LMSTUDIO_PORT = os.environ.get("LMSTUDIO_PORT", "1234")

PROVIDER_PRESETS = {
    "ollama": (f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}/v1", "ollama"),
    "openrouter": ("https://openrouter.ai/api/v1", None),
    "lmstudio": (f"http://{_LMSTUDIO_HOST}:{_LMSTUDIO_PORT}/v1", "lm-studio"),
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
            "name": "call_agent",
            "description": "Call a Wee Orchestrator agent to execute a task. Use for delegating work to specialized agents (devops, email-triage, family-knowledge, research, smarthome, wee-dev, wee-qa, wee-doc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "description": "Agent name to call (e.g., 'devops', 'email-triage', 'research')",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Task prompt or instruction for the agent",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["quick", "background"],
                        "description": "Execution mode: 'quick' waits for result (sync), 'background' returns task_id immediately (async)",
                    },
                },
                "required": ["agent", "prompt"],
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
        _ollama_host = os.environ.get("OLLAMA_HOST", "192.168.1.101")
        _ollama_port = os.environ.get("OLLAMA_PORT", "11434")
        resolved_base = os.environ.get(
            "WEE_API_BASE", f"http://{_ollama_host}:{_ollama_port}/v1"
        )
    if not resolved_key:
        # Issue #153: Check OPENROUTER_API_KEY env var for OpenRouter first
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
        # Issue #153: Raise clear error instead of defaulting to "ollama"
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
        elif func_name == "call_agent":
            return _call_agent_handler(func_args)
        else:
            return f"Error: Unknown tool {func_name}"
    except subprocess.TimeoutExpired:
        return f"Error: Tool {func_name} timed out after {TOOL_TIMEOUT}s"
    except Exception as e:
        return f"Error executing tool {func_name}: {e}"


def _load_agents_config() -> dict:
    """Load agents.json from common locations."""
    import json
    
    locations = [
        os.path.join(os.getcwd(), "agents.json"),
        "/opt/n8n-copilot-shim/agents.json",
        os.path.expanduser("~/.wee/agents.json"),
    ]
    
    for path in locations:
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
    
    return {"agents": []}


def _get_agent_config(agent_name: str) -> dict:
    """Get config for a specific agent from agents.json.
    
    Supports both old format (runtime/model) and new format (primary_runtime/primary_model).
    """
    config = _load_agents_config()
    for agent in config.get("agents", []):
        if agent.get("name") == agent_name:
            # Handle both old and new config formats
            agent_config = agent.copy()
            
            # If new format (primary_runtime), use it as-is
            if "primary_runtime" in agent_config:
                return agent_config
            
            # Convert old format (runtime/model) to new format for consistency
            if "runtime" in agent_config:
                agent_config["primary_runtime"] = agent_config.pop("runtime")
            if "model" in agent_config:
                agent_config["primary_model"] = agent_config.pop("model")
            
            # Provide sensible fallbacks if not specified
            if "fallback_runtime" not in agent_config:
                agent_config["fallback_runtime"] = "copilot" if agent_config.get("primary_runtime") == "claude" else "claude"
            if "fallback_model" not in agent_config:
                agent_config["fallback_model"] = "auto"
            
            return agent_config
    return {}


def _call_agent_handler(func_args: dict) -> str:
    """Handle call_agent tool calls to invoke Wee Orchestrator agents.

    Args:
        func_args: Dict with 'agent', 'prompt', and optional 'mode' ('quick'|'background')

    Returns:
        Result string or task ID
    
    On infrastructure errors (429, 503, 502, 401, timeout), automatically retries
    with agent's fallback_runtime and fallback_model from agents.json.
    """
    import json
    import urllib.request
    import urllib.error

    agent = func_args.get("agent", "").strip()
    prompt = func_args.get("prompt", "").strip()
    mode = func_args.get("mode", "quick").strip().lower()

    if not agent:
        return "Error: agent parameter required"
    if not prompt:
        return "Error: prompt parameter required"
    if mode not in ("quick", "background"):
        return "Error: mode must be 'quick' or 'background'"

    # Get agent config for fallback info
    agent_config = _get_agent_config(agent)
    
    api_url = os.environ.get("WEE_ORCHESTRATOR_API", "https://127.0.0.1:8000")
    token = os.environ.get("WEE_ORCHESTRATOR_TOKEN", "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU")
    
    # Try with primary runtime first, then fallback
    runtimes_to_try = [
        {
            "runtime": agent_config.get("primary_runtime", "copilot"),
            "model": agent_config.get("primary_model", "claude-haiku-4.5"),
            "is_fallback": False,
        }
    ]
    
    # Add fallback if different from primary
    fallback_runtime = agent_config.get("fallback_runtime")
    fallback_model = agent_config.get("fallback_model")
    if fallback_runtime and fallback_runtime != runtimes_to_try[0]["runtime"]:
        runtimes_to_try.append({
            "runtime": fallback_runtime,
            "model": fallback_model or "auto",
            "is_fallback": True,
        })
    
    last_error = None
    
    for runtime_config in runtimes_to_try:
        try:
            runtime = runtime_config["runtime"]
            model = runtime_config["model"]
            is_fallback = runtime_config["is_fallback"]
            
            if mode == "quick":
                endpoint = "/api/v1/query"
                task_data = {
                    "prompt": prompt,
                    "agent": agent,
                    "runtime": runtime,
                    "model": model,
                    "timeout": 60,
                }
            else:
                endpoint = "/api/v1/background-tasks"
                task_data = {
                    "prompt": prompt,
                    "agent": agent,
                    "runtime": runtime,
                    "model": model,
                    "timeout": 1800,
                }

            url = f"{api_url}{endpoint}"
            req = urllib.request.Request(url, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {token}")

            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(req, data=json.dumps(task_data).encode(), context=ctx, timeout=30) as response:
                result = json.loads(response.read().decode())
                if mode == "background":
                    task_id = result.get("id", result.get("task_id"))
                    prefix = "[Fallback] " if is_fallback else ""
                    return f"✓ {prefix}Task started: {agent}\nTask ID: {task_id}\nCheck status with: /background status {task_id}"
                else:
                    response_text = result.get('response', result.get('result', str(result)))
                    prefix = "[Fallback] " if is_fallback else ""
                    return f"✓ {prefix}{agent} agent response:\n{response_text}"

        except urllib.error.HTTPError as e:
            error_code = e.code
            error_text = e.read().decode() if e.fp else str(e)
            
            # Check if this is a retryable error
            retryable_codes = {429, 503, 502, 401, 408}
            if error_code in retryable_codes and not runtime_config["is_fallback"]:
                # Will retry with fallback on next iteration
                last_error = f"HTTP {error_code}: {error_text[:100]}"
                continue
            
            # Not retryable or already on fallback
            return f"Error calling {agent} ({runtime}/{model}): HTTP {error_code} - {error_text[:200]}"
        
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Network/timeout errors are retryable if not on fallback
            if not runtime_config["is_fallback"]:
                last_error = str(e)[:100]
                continue
            return f"Error calling {agent} ({runtime}/{model}): {e}"
        
        except Exception as e:
            return f"Error calling agent '{agent}': {e}"
    
    # All retries exhausted
    if last_error:
        return f"Error: All retry attempts failed for {agent}. Last error: {last_error}"
    return f"Error: No valid runtime configuration found for agent '{agent}'"


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


def list_available_models(provider: str = None):
    """List available models from all configured providers.
    
    Args:
        provider: Optional filter ('ollama', 'openrouter', 'lmstudio').
                 If None, shows all. Case-insensitive.
    """
    import httpx
    
    _ollama_host = os.environ.get("OLLAMA_HOST", "192.168.1.101")
    _ollama_port = os.environ.get("OLLAMA_PORT", "11434")
    
    if provider:
        provider = provider.lower()
    
    print("Available Models by Provider:")
    print("=" * 60)
    
    # Ollama models
    if not provider or provider == "ollama":
        print("\nOllama (http://%s:%s):" % (_ollama_host, _ollama_port))
        try:
            resp = httpx.get(
                f"http://{_ollama_host}:{_ollama_port}/api/tags",
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0),
            )
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                for m in models:
                    name = m.get("name", "")
                    size_bytes = m.get("size", 0)
                    size_gb = size_bytes / (1024**3)
                    print(f"  ollama/{name:<40} ({size_gb:.1f} GB)")
                if not models:
                    print("  (no models available)")
            else:
                print("  (unreachable)")
        except Exception as e:
            print(f"  (error: {e})")
    
    # OpenRouter models
    if not provider or provider == "openrouter":
        print("\nOpenRouter (https://openrouter.ai):")
        try:
            resp = httpx.get("https://openrouter.ai/api/v1/models", timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                if models:
                    print(f"  Found {len(models)} models. Use 'openrouter/<model-id>'")
                    providers_dict = {}
                    for m in models:
                        model_id = m.get("id", "")
                        if "/" in model_id:
                            prov = model_id.split("/")[0]
                            if prov not in providers_dict:
                                providers_dict[prov] = []
                            providers_dict[prov].append(model_id)
                    for prov in sorted(providers_dict.keys()):
                        print(f"    {prov}:")
                        for model_id in sorted(providers_dict[prov]):
                            print(f"      openrouter/{model_id}")
                else:
                    print("  (no models available)")
            else:
                print(f"  (error: HTTP {resp.status_code})")
        except Exception as e:
            print(f"  (error: {e})")
    
    # LM Studio
    if not provider or provider == "lmstudio":
        print("\nLM Studio (http://localhost:1234):")
        _lmstudio_host = os.environ.get("LMSTUDIO_HOST", "localhost")
        _lmstudio_port = os.environ.get("LMSTUDIO_PORT", "1234")
        try:
            resp = httpx.get(
                f"http://{_lmstudio_host}:{_lmstudio_port}/v1/models",
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0),
            )
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                if models:
                    for m in models:
                        model_id = m.get("id", "")
                        print(f"  lmstudio/{model_id}")
                else:
                    print("  (no models available)")
            else:
                print("  (unreachable or no models loaded)")
        except Exception as e:
            print(f"  (unreachable: {e})")


def main():
    parser = argparse.ArgumentParser(
        description="Wee Native Runtime — OpenAI-compatible chat completions"
    )
    parser.add_argument(
        "--list-models",
        nargs="?",
        const=True,
        metavar="PROVIDER",
        help="List available models (optional: ollama, openrouter, lmstudio)",
    )
    parser.add_argument(
        "--model", help="Model name (e.g., ollama/gemma4:e4b)"
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
    parser.add_argument("prompt", nargs="?", help="User prompt")
    args = parser.parse_args()
    
    # Handle --list-models
    if args.list_models is not None:
        provider = None if args.list_models is True else args.list_models
        list_available_models(provider)
        sys.exit(0)
    
    # Validate required arguments for normal operation
    if not args.model:
        parser.error("--model is required (unless using --list-models)")
    if not args.prompt:
        parser.error("prompt is required (unless using --list-models)")

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
