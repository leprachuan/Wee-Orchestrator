#!/usr/bin/env python3
"""Wee CLI — Standalone command-line interface for Wee Runtime.

A CLI tool similar to GitHub Copilot CLI, Claude Code CLI, and Codex CLI.
Supports single-shot prompts, interactive REPL, piping, and tool calling.

Usage:
    wee "What is the capital of France?"
    wee --model openrouter/meta-llama/llama-2-70b "Explain quantum computing"
    wee --model ollama/gemma4:e4b --tools "List Python files in /opt"
    wee --interactive
    echo "summarize this" | wee --model ollama/qwen3.5:4b
    wee  # enters interactive mode by default

Issue #158: https://github.com/leprachuan/Wee-Orchestrator/issues/158
"""

import argparse
import json
import os
import readline
import sys

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Re-use core runtime functions from wee_runtime.py
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from wee_runtime import _WEE_TOOLS  # noqa: E402
from wee_runtime import (  # noqa: E402
    _ANTI_HALLUCINATION_PROMPT,
    MAX_TOOL_ROUNDS,
    execute_tool,
    resolve_model_and_endpoint,
    list_available_models,
)

# ---------------------------------------------------------------------------
# Config file support (~/.wee/config.json)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG_DIR = os.path.expanduser("~/.wee")
DEFAULT_CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, "config.json")
HISTORY_FILE = os.path.join(DEFAULT_CONFIG_DIR, "history")


def load_config() -> dict:
    """Load config from ~/.wee/config.json if it exists."""
    if os.path.isfile(DEFAULT_CONFIG_FILE):
        try:
            with open(DEFAULT_CONFIG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg: dict) -> None:
    """Save config to ~/.wee/config.json."""
    os.makedirs(DEFAULT_CONFIG_DIR, exist_ok=True)
    with open(DEFAULT_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------------
# Readline history
# ---------------------------------------------------------------------------
def _init_readline():
    """Set up readline with history file and tab completion."""
    os.makedirs(DEFAULT_CONFIG_DIR, exist_ok=True)
    try:
        readline.read_history_file(HISTORY_FILE)
    except FileNotFoundError:
        pass
    readline.set_history_length(1000)

    # Tab completion for slash commands
    _COMMANDS = [
        "/clear",
        "/history",
        "/model",
        "/tokens",
        "/system",
        "/help",
        "/config",
        "/version",
    ]

    def completer(text, state):
        if text.startswith("/"):
            options = [c for c in _COMMANDS if c.startswith(text)]
        else:
            options = []
        return options[state] if state < len(options) else None

    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")


def _save_readline():
    """Save readline history."""
    try:
        readline.write_history_file(HISTORY_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Rich console output (optional — falls back to plain text)
# ---------------------------------------------------------------------------
_rich_available = False
_console = None

try:
    from rich.console import Console
    from rich.markdown import Markdown

    _rich_available = True
    _console = Console(stderr=True)
except ImportError:
    pass


def _print_markdown(text: str, output_format: str = "text"):
    """Print response text with optional formatting."""
    if output_format == "json":
        json.dump({"response": text}, sys.stdout)
        sys.stdout.write("\n")
        return
    if output_format == "markdown" and _rich_available:
        _console.print(Markdown(text))
        return
    # Default: plain text (already streamed to stdout)
    pass


def _print_error(msg: str):
    """Print error to stderr with optional rich formatting."""
    if _rich_available:
        _console.print(f"[bold red]Error:[/bold red] {msg}")
    else:
        print(f"Error: {msg}", file=sys.stderr)


def _print_info(msg: str):
    """Print info to stderr."""
    if _rich_available:
        _console.print(f"[dim]{msg}[/dim]")
    else:
        print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Token usage tracking
# ---------------------------------------------------------------------------
class TokenTracker:
    """Track token usage across turns."""

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.turns = 0

    def update(self, usage):
        """Update from an OpenAI usage object."""
        if usage:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            self.total_tokens += getattr(usage, "total_tokens", 0) or 0
        self.turns += 1

    def summary(self) -> str:
        return (
            f"Tokens — prompt: {self.prompt_tokens}, "
            f"completion: {self.completion_tokens}, "
            f"total: {self.total_tokens}, "
            f"turns: {self.turns}"
        )


# ---------------------------------------------------------------------------
# Core chat function (streaming)
# ---------------------------------------------------------------------------
def chat_stream(
    client,
    model: str,
    messages: list,
    tools_enabled: bool = False,
    temperature: float = None,
    token_tracker: TokenTracker = None,
    permission: str = "auto",
    stream_output: bool = True,
) -> str:
    """Send a chat completion request and stream the response.

    Args:
        stream_output: When False, suppress stdout streaming (used for JSON/markdown
            output modes where the caller formats and prints the final response).
        permission: Tool execution permission level. "restricted" blocks all tool
            execution. "auto" (default) executes tools as requested. "elevated" is
            treated the same as "auto" (no additional privilege escalation in CLI).
    Returns the full response text. Handles tool-calling loops.
    """
    tool_call_counter = 0
    collected_output = []

    for round_num in range(MAX_TOOL_ROUNDS + 1):
        create_kwargs = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None:
            create_kwargs["temperature"] = temperature
        if tools_enabled and round_num < MAX_TOOL_ROUNDS:
            create_kwargs["tools"] = _WEE_TOOLS

        try:
            stream = client.chat.completions.create(**create_kwargs)
        except Exception as tools_err:
            if "tools" in create_kwargs:
                _print_info(f"[Wee] Tools not supported, retrying without: {tools_err}")
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
                if stream_output:
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

            # Track usage from the final chunk
            if hasattr(chunk, "usage") and chunk.usage and token_tracker:
                token_tracker.update(chunk.usage)

        content_text = "".join(round_content)

        if not tool_calls_acc:
            collected_output.append(content_text)
            break

        # Tool calls detected
        _print_info(f"[Wee] Round {round_num + 1}: {len(tool_calls_acc)} tool call(s)")

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

        for tc_info in assistant_tool_calls:
            tc_id = tc_info["id"]
            func_name = tc_info["function"]["name"]
            try:
                func_args = json.loads(tc_info["function"]["arguments"])
            except json.JSONDecodeError:
                func_args = {"command": tc_info["function"]["arguments"]}

            _print_info(f"[Wee] Executing: {func_name}({json.dumps(func_args)[:100]})")
            tool_result = execute_tool(func_name, func_args, permission=permission)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": tool_result or "No output",
                }
            )
    else:
        # All rounds had tool calls
        last_results = [m["content"] for m in messages if m.get("role") == "tool"]
        if last_results:
            fallback = (
                "Tool execution completed. Last result:\n" + last_results[-1][:2000]
            )
        else:
            fallback = "Max tool rounds reached without final response."
        collected_output.append(fallback)
        if stream_output:
            sys.stdout.write(fallback)

    if stream_output:
        sys.stdout.write("\n")
        sys.stdout.flush()

    if token_tracker and not token_tracker.turns:
        token_tracker.turns = 1

    return "".join(collected_output)


# ---------------------------------------------------------------------------
# OpenAI client factory
# ---------------------------------------------------------------------------
def _make_client(api_base: str, api_key: str, timeout: float):
    """Create an OpenAI client."""
    try:
        from openai import OpenAI
    except ImportError:
        _print_error("openai package not installed. Run: pip install openai")
        sys.exit(1)

    import httpx

    return OpenAI(
        base_url=api_base,
        api_key=api_key,
        timeout=httpx.Timeout(timeout=timeout, connect=15.0),
        max_retries=0,
    )


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------
REPL_HELP = """\
Wee CLI Interactive Mode — Commands:
  /clear          Clear conversation history
  /history        Show conversation history
  /model MODEL    Switch model
  /model list     List available models
  /tokens         Show token usage
  /system PROMPT  Set system prompt
  /config         Show current configuration
  /version        Show version
  /help           Show this help
  /exit, /quit    Exit interactive mode
"""


def run_interactive(
    model: str,
    api_base: str,
    api_key: str,
    tools_enabled: bool,
    temperature: float,
    timeout: float,
    system_prompt: str,
    output_format: str,
    permission: str,
):
    """Run the interactive REPL."""
    _init_readline()

    client = _make_client(api_base, api_key, timeout)
    token_tracker = TokenTracker()
    messages = []

    if system_prompt:
        effective_system = system_prompt + _ANTI_HALLUCINATION_PROMPT
    else:
        effective_system = _ANTI_HALLUCINATION_PROMPT.lstrip()
    messages.append({"role": "system", "content": effective_system})

    _print_info(f"Wee CLI v{__version__} — model: {model}")
    _print_info("Type /help for commands, /exit to quit.\n")

    while True:
        try:
            user_input = input("wee> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        # Handle slash commands
        if user_input.startswith("/"):
            parts = user_input.split(None, 1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "/exit" or cmd == "/quit":
                break

            elif cmd == "/clear":
                messages = [{"role": "system", "content": effective_system}]
                token_tracker = TokenTracker()
                _print_info("Conversation cleared.")
                continue

            elif cmd == "/history":
                for i, msg in enumerate(messages):
                    role = msg["role"]
                    content = msg.get("content", "")
                    if role == "system":
                        continue
                    prefix = "You" if role == "user" else "Wee"
                    display = (content[:120] + "...") if len(content) > 120 else content
                    _print_info(f"  [{i}] {prefix}: {display}")
                continue

            elif cmd == "/model":
                if not arg:
                    _print_info(f"Current model: {model}")
                elif arg.lower() == "list":
                    list_available_models()
                else:
                    old_model = model
                    model, api_base, api_key = resolve_model_and_endpoint(arg)
                    client = _make_client(api_base, api_key, timeout)
                    _print_info(f"Model switched: {old_model} → {model}")
                continue

            elif cmd == "/tokens":
                _print_info(token_tracker.summary())
                continue

            elif cmd == "/system":
                if not arg:
                    _print_info(f"System prompt: {effective_system[:200]}...")
                else:
                    effective_system = arg + _ANTI_HALLUCINATION_PROMPT
                    messages[0] = {"role": "system", "content": effective_system}
                    _print_info("System prompt updated.")
                continue

            elif cmd == "/config":
                _print_info(f"Model:       {model}")
                _print_info(f"API Base:    {api_base}")
                status = "enabled" if tools_enabled else "disabled"
                _print_info(f"Tools:       {status}")
                _print_info(f"Temperature: {temperature or 'default'}")
                _print_info(f"Timeout:     {timeout}s")
                _print_info(f"Permission:  {permission}")
                _print_info(f"Output:      {output_format}")
                continue

            elif cmd == "/version":
                _print_info(f"Wee CLI v{__version__}")
                continue

            elif cmd == "/help":
                _print_info(REPL_HELP)
                continue

            else:
                _print_info(f"Unknown command: {cmd}. Type /help for commands.")
                continue

        # Regular prompt
        messages.append({"role": "user", "content": user_input})

        try:
            response = chat_stream(
                client=client,
                model=model,
                messages=messages,
                tools_enabled=tools_enabled,
                temperature=temperature,
                token_tracker=token_tracker,
                permission=permission,
            )
            messages.append({"role": "assistant", "content": response})
        except KeyboardInterrupt:
            print("\n[interrupted]")
            messages.append({"role": "assistant", "content": "[interrupted]"})
        except Exception as e:
            _print_error(str(e))
            messages.append({"role": "assistant", "content": f"[error: {e}]"})

    _save_readline()
    _print_info(f"\n{token_tracker.summary()}")
    _print_info("Goodbye!")


# ---------------------------------------------------------------------------
# Single-shot mode
# ---------------------------------------------------------------------------
def run_single_shot(
    prompt: str,
    model: str,
    api_base: str,
    api_key: str,
    tools_enabled: bool,
    temperature: float,
    timeout: float,
    system_prompt: str,
    output_format: str,
    permission: str = "auto",
):
    """Run a single prompt and exit."""
    client = _make_client(api_base, api_key, timeout)
    token_tracker = TokenTracker()
    messages = []

    effective_system = (system_prompt or "") + _ANTI_HALLUCINATION_PROMPT
    if effective_system.strip():
        messages.append({"role": "system", "content": effective_system})

    messages.append({"role": "user", "content": prompt})

    # For non-text output formats, suppress streaming so we can reformat
    stream_output = output_format == "text"

    try:
        response = chat_stream(
            client=client,
            model=model,
            messages=messages,
            tools_enabled=tools_enabled,
            temperature=temperature,
            token_tracker=token_tracker,
            permission=permission,
            stream_output=stream_output,
        )
        _print_markdown(response, output_format)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        _print_error(str(e))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="wee",
        description="Wee CLI — Standalone command-line AI assistant",
        epilog=(
            "Examples:\n"
            '  wee --model ollama/qwen3.5:4b "What is 2+2?"\n'
            '  wee --model openrouter/meta-llama/llama-2-70b --tools "List files"\n'
            "  wee --interactive\n"
            '  echo "summarize" | wee --model ollama/gemma4:e4b\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="Model ID (e.g. ollama/qwen3.5:4b, openrouter/meta-llama/llama-2-70b). "
        "Default: $WEE_MODEL or ollama/qwen3.5:4b",
    )
    parser.add_argument(
        "--api-key",
        "-k",
        default=None,
        help=(
            "API key override (default: from env or keyring). "
            "WARNING: this value appears in the process list (ps aux). "
            "Prefer setting the WEE_API_KEY environment variable instead."
        ),
    )
    parser.add_argument(
        "--api-base",
        "-b",
        default=None,
        help="Custom API base URL",
    )
    parser.add_argument(
        "--tools",
        "-t",
        action="store_true",
        default=False,
        help="Enable tool calling (bash, python)",
    )
    parser.add_argument(
        "--permission",
        "-p",
        choices=["restricted", "auto", "elevated"],
        default="auto",
        help="Permission level for tool execution (default: auto)",
    )
    parser.add_argument(
        "--system",
        "-s",
        default=None,
        help="System prompt override",
    )
    parser.add_argument(
        "--temperature",
        "-T",
        type=float,
        default=None,
        help="Sampling temperature",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Request timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--output",
        "-o",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        default=False,
        help="Enter interactive REPL mode",
    )
    parser.add_argument(
        "--config",
        default=None,
        help=f"Config file path (default: {DEFAULT_CONFIG_FILE})",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List available models and exit",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt text (omit for interactive mode or pipe from stdin)",
    )
    return parser


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main(argv=None):
    """Main entry point for wee CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Handle --list-models
    if args.list_models:
        from wee_runtime import list_available_models
        list_available_models()
        sys.exit(0)

    # Load config file defaults
    config_path = args.config or DEFAULT_CONFIG_FILE
    cfg = {}
    if config_path and os.path.isfile(config_path):
        try:
            with open(config_path) as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Resolve model: CLI arg > env var > config file > default
    model_str = (
        args.model
        or os.environ.get("WEE_MODEL")
        or cfg.get("model")
        or "ollama/qwen3.5:4b"
    )

    # Resolve other settings from config
    system_prompt = args.system or cfg.get("system_prompt", "")
    tools_enabled = args.tools or cfg.get("tools", False)
    permission = args.permission or cfg.get("permission", "auto")
    temperature = (
        args.temperature if args.temperature is not None else cfg.get("temperature")
    )
    timeout = args.timeout or cfg.get("timeout", 120.0)
    output_format = args.output or cfg.get("output", "text")

    # Resolve model + endpoint
    model, api_base, api_key = resolve_model_and_endpoint(
        model_str,
        args.api_base or cfg.get("api_base"),
        args.api_key or cfg.get("api_key"),
    )

    # Determine mode: interactive, piped stdin, or single-shot
    prompt_text = " ".join(args.prompt) if args.prompt else ""

    # If --interactive explicitly set, go straight to REPL
    if args.interactive:
        # Enable tools by default in interactive mode (unless explicitly disabled)
        if not args.tools and not cfg.get("tools"):
            tools_enabled = True
        run_interactive(
            model=model,
            api_base=api_base,
            api_key=api_key,
            tools_enabled=tools_enabled,
            temperature=temperature,
            timeout=timeout,
            system_prompt=system_prompt,
            output_format=output_format,
            permission=permission,
        )
        return

    # Check for piped stdin
    stdin_is_pipe = not sys.stdin.isatty()

    if stdin_is_pipe and not prompt_text:
        # Read from stdin
        try:
            prompt_text = sys.stdin.read().strip()
        except KeyboardInterrupt:
            sys.exit(130)

    if not prompt_text and not stdin_is_pipe:
        # Interactive REPL (no args, tty)
        # Enable tools by default in interactive mode (unless explicitly disabled)
        if not args.tools and not cfg.get("tools"):
            tools_enabled = True
        run_interactive(
            model=model,
            api_base=api_base,
            api_key=api_key,
            tools_enabled=tools_enabled,
            temperature=temperature,
            timeout=timeout,
            system_prompt=system_prompt,
            output_format=output_format,
            permission=permission,
        )
    elif prompt_text:
        # Single-shot mode
        run_single_shot(
            prompt=prompt_text,
            model=model,
            api_base=api_base,
            api_key=api_key,
            tools_enabled=tools_enabled,
            temperature=temperature,
            timeout=timeout,
            system_prompt=system_prompt,
            output_format=output_format,
            permission=permission,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
