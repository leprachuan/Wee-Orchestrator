#!/usr/bin/env python3
"""Wee CLI — Standalone command-line interface for Wee Runtime.

A CLI tool similar to GitHub Copilot CLI, Claude Code CLI, and Codex CLI.
Supports single-shot prompts, interactive REPL, piping, and tool calling.

Usage:
    wee "What is the capital of France?"
    wee exec "What is the capital of France?"
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
import re
import sys
import tempfile

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
__version__ = "0.2.0"

# ---------------------------------------------------------------------------
# Re-use core runtime functions from wee_runtime.py
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from wee_runtime import _WEE_TOOLS  # noqa: E402
from wee_runtime import (  # noqa: E402
    _ANTI_HALLUCINATION_PROMPT,
    COMPACT_TRIGGER_FRACTION,
    MAX_TOOL_ROUNDS,
    compact_messages,
    count_message_tokens,
    execute_tool,
    get_context_window,
    list_available_models,
    resolve_model_and_endpoint,
)
from wee_copilot_sdk import (  # noqa: E402
    execute_wee_copilot,
    resolve_wee_provider,
)

_TOOL_PREAMBLE_PROMPT = (
    "\n\n[Tool Use Style]\n"
    "Immediately before each tool call, write exactly one brief sentence "
    "explaining what you are about to do.\n"
)

# ---------------------------------------------------------------------------
# Config file support (~/.wee/config.json)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG_DIR = os.path.expanduser("~/.wee")
DEFAULT_CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, "config.json")
HISTORY_FILE = os.path.join(DEFAULT_CONFIG_DIR, "history")
SESSION_DIR = os.path.join(DEFAULT_CONFIG_DIR, "sessions")
_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MODEL_STATUS_ALIASES = {"current", "show", "get"}


def _normalize_model_identifier(value: str) -> str:
    """Repair model IDs persisted by the pre-Issue #426 `/model set` parser."""
    model = str(value or "").strip()
    if model.lower().startswith("set "):
        model = model[4:].strip()
    if model.lower() in _MODEL_STATUS_ALIASES:
        return ""
    return model


def _chat_via_copilot_sdk(
    *,
    messages: list,
    qualified_model: str,
    api_base: str,
    api_key: str,
    timeout: float,
    tools_enabled: bool,
    stream_output: bool = True,
    activity_callback=None,
) -> str:
    """Run a Wee CLI turn through the shared Copilot SDK BYOK path."""
    route = resolve_wee_provider(qualified_model, api_base=api_base, api_key=api_key)
    transcript = []
    for message in messages:
        role = str(message.get("role", "user")).upper()
        content = message.get("content")
        if content:
            transcript.append(f"[{role}]\n{content}")
    prompt = "\n\n".join(transcript)
    streamed = [False]

    def on_event(kind, payload):
        if kind == "chunk" and stream_output:
            streamed[0] = True
            sys.stdout.write(str(payload))
            sys.stdout.flush()
        if activity_callback and kind in {"tool_call", "done"}:
            activity_callback(kind, payload)

    response, _ = execute_wee_copilot(
        prompt=prompt,
        route=route,
        working_directory=os.getcwd(),
        timeout=float(timeout),
        enable_tools=tools_enabled,
        event_callback=on_event,
    )
    if streamed[0]:
        print()
    return response


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


def _build_effective_system_prompt(system_prompt: str = "") -> str:
    """Build the effective system prompt for wee-cli sessions."""
    return (system_prompt or "") + _ANTI_HALLUCINATION_PROMPT + _TOOL_PREAMBLE_PROMPT


def _validate_session_name(name: str) -> str:
    """Validate and normalize a wee-cli local session name."""
    if not name or not _SESSION_NAME_RE.match(name):
        raise ValueError(
            "Invalid session name. Use 1-64 chars: letters, numbers, '.', '_' or '-'."
        )
    return name


def _session_path(name: str) -> str:
    """Return the on-disk path for a local wee-cli session."""
    safe_name = _validate_session_name(name)
    return os.path.join(SESSION_DIR, f"{safe_name}.json")


def load_session_data(name: str) -> dict:
    """Load a persisted wee-cli session from disk."""
    path = _session_path(name)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    messages = data.get("messages")
    if not isinstance(messages, list):
        return {}
    return data


def save_session_data(name: str, data: dict) -> None:
    """Persist a wee-cli session atomically to disk."""
    path = _session_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".wee-session-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _clone_messages(messages: list) -> list:
    """Return a JSON-safe deep copy of the message history."""
    return json.loads(json.dumps(messages))


def _prepare_session_messages(
    system_prompt: str, existing_messages: list = None
) -> tuple:
    """Build or resume a message list and return (messages, effective_system)."""
    effective_system = _build_effective_system_prompt(system_prompt).lstrip()
    if existing_messages:
        messages = _clone_messages(existing_messages)
        if messages and messages[0].get("role") == "system":
            if system_prompt:
                messages[0]["content"] = effective_system
            else:
                effective_system = messages[0].get("content", effective_system)
        else:
            messages.insert(0, {"role": "system", "content": effective_system})
        return messages, effective_system
    return [{"role": "system", "content": effective_system}], effective_system


def _session_payload(
    messages: list,
    model: str,
    system_prompt: str,
    tools_enabled: bool,
    temperature: float,
    timeout: float,
    output_format: str,
    permission: str,
) -> dict:
    """Build the persisted wee-cli session payload."""
    return {
        "version": 1,
        "model": model,
        "system_prompt": system_prompt or "",
        "tools_enabled": bool(tools_enabled),
        "temperature": temperature,
        "timeout": timeout,
        "output_format": output_format,
        "permission": permission,
        "messages": _clone_messages(messages),
    }


# ---------------------------------------------------------------------------
# Agent & Skill Discovery
# ---------------------------------------------------------------------------
def load_agents_json(search_cwd: bool = True) -> dict:
    """Load agents.json from CWD first, then fallback to script folder.

    Args:
        search_cwd: If True, search CWD first. If False, only use script folder.

    Returns:
        Parsed agents.json content or empty dict if not found
    """
    # Try CWD first if requested
    if search_cwd:
        cwd_agents = os.path.join(os.getcwd(), "agents.json")
        try:
            with open(cwd_agents) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    # Fall back to script folder
    agents_file = os.path.join(_HERE, "agents.json")
    try:
        with open(agents_file) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"agents": []}


def discover_skills(search_cwd: bool = True) -> dict:
    """Discover available skills from skill repositories and CWD.

    Args:
        search_cwd: If True, search CWD first. If False, only use default dirs.

    Returns:
        Dict mapping skill_name -> {description, path, loaded}
    """
    skills = {}
    skill_dirs = []

    # Add CWD first if requested
    if search_cwd:
        cwd_skills_dir = os.path.join(os.getcwd(), "skills")
        if os.path.isdir(cwd_skills_dir):
            skill_dirs.append(cwd_skills_dir)

    # Always add default directories
    skill_dirs.extend(
        [
            "/opt/foster-skills",
            "/opt/skills",
        ]
    )

    for skill_dir in skill_dirs:
        if not os.path.isdir(skill_dir):
            continue
        try:
            for entry in os.listdir(skill_dir):
                skill_path = os.path.join(skill_dir, entry)

                # Check for skill_metadata.json (standard format)
                metadata_file = os.path.join(skill_path, "skill_metadata.json")
                if os.path.isfile(metadata_file):
                    try:
                        with open(metadata_file) as f:
                            metadata = json.load(f)
                        skills[entry] = {
                            "description": metadata.get(
                                "description", "No description"
                            ),
                            "path": skill_path,
                            "loaded": False,
                        }
                    except (json.JSONDecodeError, OSError):
                        pass

                # Check for .skill file (alternate format)
                skill_file = os.path.join(skill_dir, f"{entry}.skill")
                if os.path.isfile(skill_file) and entry not in skills:
                    try:
                        with open(skill_file, encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            # Extract description from first lines
                            desc_line = next(
                                (
                                    line
                                    for line in content.split("\n")
                                    if "description" in line.lower()
                                ),
                                "",
                            )
                            skills[entry] = {
                                "description": (
                                    desc_line.strip() if desc_line else "Custom skill"
                                ),
                                "path": skill_file,
                                "loaded": False,
                            }
                    except OSError:
                        pass
        except OSError:
            pass

    return skills


def get_agent_info() -> list:
    """Get list of agents from agents.json.

    Returns:
        List of agent dicts with name and description
    """
    agents_data = load_agents_json()
    agents = []
    for agent in agents_data.get("agents", []):
        agents.append(
            {
                "name": agent.get("name", "unknown"),
                "description": agent.get("description", "No description"),
                "path": agent.get("path", ""),
            }
        )
    return agents


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
    from rich import box
    from rich.console import Console
    from rich.console import Group
    from rich.markdown import Markdown
    from rich.markup import escape
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text

    _rich_available = True
    _console = Console(stderr=True, highlight=False)
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


def _interactive_ui_enabled() -> bool:
    """Return whether the full Rich interactive experience can be rendered."""
    if not _rich_available:
        return False
    if os.environ.get("WEE_PLAIN_UI", "").lower() in {"1", "true", "yes", "on"}:
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return bool(sys.stdin.isatty() and sys.stderr.isatty())


def _provider_label(qualified_model: str) -> str:
    """Return a friendly provider name for a provider-qualified model ID."""
    provider = (qualified_model or "").partition("/")[0].lower()
    return {
        "ollama": "Ollama",
        "openrouter": "OpenRouter",
        "lmstudio": "LM Studio",
    }.get(provider, provider.title() or "Custom")


def _render_interactive_banner(
    model: str, permission: str, tools_enabled: bool, session_name: str = None
) -> None:
    """Render the compact Wee startup card used for interactive terminals."""
    if not _interactive_ui_enabled():
        _print_info(f"Wee CLI v{__version__} — model: {model}")
        _print_info("Type /help for commands, /exit to quit.\n")
        return

    provider = _provider_label(model)
    details = Text()
    details.append(provider, style="bold bright_cyan")
    details.append("  •  ", style="grey50")
    details.append(model, style="bright_white")
    details.append("\n")
    details.append("tools ", style="grey70")
    details.append(
        "on" if tools_enabled else "off",
        style="green" if tools_enabled else "yellow",
    )
    details.append("  •  mode ", style="grey70")
    details.append(permission, style="bright_magenta")
    if session_name:
        details.append("  •  session ", style="grey70")
        details.append(session_name, style="bright_blue")

    hint = Text.from_markup(
        "[grey58]/help[/] commands   [grey58]/model list[/] models   "
        "[grey58]Ctrl+D[/] exit"
    )
    _console.print(
        Panel(
            Group(details, hint),
            title=f"[bold bright_green]🍀 Wee[/] [grey58]v{__version__}[/]",
            title_align="left",
            border_style="bright_green",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


def _read_interactive_prompt(
    model: str, permission: str, tools_enabled: bool, token_tracker
) -> str:
    """Read one user turn from a high-contrast, status-aware prompt area."""
    if not _interactive_ui_enabled():
        return input("wee> ")

    context = ""
    if token_tracker.context_window:
        context = f"  {token_tracker.percent_used():.0f}% context"
    status = (
        f"{escape(model)}  •  {'tools on' if tools_enabled else 'tools off'}"
        f"  •  {escape(permission)}{context}"
    )
    _console.print()
    _console.print(
        Rule(
            f"[bold bright_cyan] You [/][grey58]{status}[/]",
            align="left",
            style="cyan",
        )
    )
    return _console.input("[bold bright_cyan]❯[/] ")


def _render_assistant_response(response: str, output_format: str = "text") -> None:
    """Render a completed assistant turn as readable Markdown."""
    if not _interactive_ui_enabled():
        return
    response = response or "[No response]"
    if output_format == "json":
        body = Text(json.dumps({"response": response}, indent=2), style="white")
    else:
        body = Markdown(response)
    _console.print(
        Panel(
            body,
            title="[bold bright_green]🍀 Wee[/]",
            title_align="left",
            border_style="green",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def _sdk_tool_name(event) -> str:
    """Extract a concise tool name from a Copilot SDK event."""
    data = getattr(event, "data", None)
    return str(
        getattr(data, "tool_name", None)
        or getattr(data, "name", None)
        or getattr(data, "command", None)
        or "tool"
    )


def _render_repl_help() -> None:
    """Render slash commands as a compact, scannable command palette."""
    if not _interactive_ui_enabled():
        _print_info(REPL_HELP)
        return
    commands = [
        ("/model list", "Browse models"),
        ("/model set ID", "Switch provider/model"),
        ("/model current", "Show active model"),
        ("/clear", "Clear conversation"),
        ("/history", "Show conversation history"),
        ("/tools on|off", "Toggle agent tools"),
        ("/tools-output", "Inspect recent tool results"),
        ("/permission MODE", "restricted, auto, or elevated"),
        ("/thinking on|off", "Show or hide reasoning"),
        ("/tokens", "Show token and context usage"),
        ("/compact [PCT]", "Compact conversation context"),
        ("/config", "Show runtime configuration"),
        ("/agents", "List configured agents"),
        ("/skills", "Discover available skills"),
        ("/exit", "End the session"),
    ]
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold bright_cyan", no_wrap=True)
    table.add_column(style="grey74")
    for command, description in commands:
        table.add_row(command, description)
    _console.print(
        Panel(
            table,
            title="[bold bright_green]Command palette[/]",
            title_align="left",
            border_style="green",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


def _render_config_panel(items) -> None:
    """Render runtime configuration as a two-column status card."""
    if not _interactive_ui_enabled():
        for label, value in items:
            _print_info(f"{label + ':':12} {value}")
        return
    table = Table.grid(padding=(0, 2))
    table.add_column(style="grey58", no_wrap=True)
    table.add_column(style="bright_white")
    for label, value in items:
        table.add_row(label, str(value))
    _console.print(
        Panel(
            table,
            title="[bold bright_green]Session configuration[/]",
            title_align="left",
            border_style="green",
            box=box.ROUNDED,
        )
    )


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks from text."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
    return text.strip()


class ThinkingBuffer:
    """State machine that filters <think>...</think> blocks during streaming.

    When show=False (default): thinking content is buffered and discarded;
    only non-thinking content is returned from feed().

    When show=True: all content is returned unchanged; thinking blocks are
    additionally recorded in thinking_blocks for inspection.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self, show: bool = False):
        self.show = show
        self.in_thinking = False
        self._buf = ""
        self._current_thinking: list = []
        self.thinking_blocks: list = []

    @staticmethod
    def _longest_prefix_suffix(s: str, tag: str) -> int:
        """Return len of longest suffix of s that is a prefix of tag (< len(tag))."""
        max_len = min(len(tag) - 1, len(s))
        for length in range(max_len, 0, -1):
            if s[-length:] == tag[:length]:
                return length
        return 0

    def feed(self, token: str) -> str:
        """Process a streaming token. Returns text that should be written to stdout."""
        self._buf += token
        output_parts: list = []

        while self._buf:
            if self.in_thinking:
                pos = self._buf.find(self._CLOSE)
                if pos == -1:
                    tail = self._longest_prefix_suffix(self._buf, self._CLOSE)
                    safe = self._buf[:-tail] if tail else self._buf
                    self._current_thinking.append(safe)
                    if self.show:
                        output_parts.append(safe)
                    self._buf = self._buf[-tail:] if tail else ""
                    break
                else:
                    thinking_text = self._buf[:pos]
                    self._current_thinking.append(thinking_text)
                    if self.show:
                        output_parts.append(thinking_text + self._CLOSE)
                    self.thinking_blocks.append("".join(self._current_thinking).strip())
                    self._current_thinking = []
                    self.in_thinking = False
                    self._buf = self._buf[pos + len(self._CLOSE) :]
            else:
                pos = self._buf.find(self._OPEN)
                if pos == -1:
                    tail = self._longest_prefix_suffix(self._buf, self._OPEN)
                    safe = self._buf[:-tail] if tail else self._buf
                    output_parts.append(safe)
                    self._buf = self._buf[-tail:] if tail else ""
                    break
                else:
                    output_parts.append(self._buf[:pos])
                    if self.show:
                        output_parts.append(self._OPEN)
                    self.in_thinking = True
                    self._buf = self._buf[pos + len(self._OPEN) :]

        return "".join(output_parts)

    def flush(self) -> str:
        """Flush remaining buffer at stream end.

        Returns any pending non-thinking text.
        """
        remaining = self._buf
        self._buf = ""
        if self.in_thinking:
            self._current_thinking.append(remaining)
            self.thinking_blocks.append("".join(self._current_thinking).strip())
            self._current_thinking = []
            self.in_thinking = False
            return remaining if self.show else ""
        return remaining


# ---------------------------------------------------------------------------
# Token usage tracking
# ---------------------------------------------------------------------------
class TokenTracker:
    """Track token usage across turns."""

    def __init__(self, context_window: int = 0):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.turns = 0
        self.session_total = 0  # cumulative across all API turns
        self.last_prompt_tokens = (
            0  # prompt tokens from the most recent turn (actual current context)
        )
        self.context_window = context_window  # 0 means unknown

    def update(self, usage):
        """Update from an OpenAI usage object."""
        if usage:
            pt = getattr(usage, "prompt_tokens", 0) or 0
            ct = getattr(usage, "completion_tokens", 0) or 0
            tt = getattr(usage, "total_tokens", 0) or 0
            self.prompt_tokens += pt
            self.completion_tokens += ct
            self.total_tokens += tt
            self.session_total += tt if tt else (pt + ct)
            self.last_prompt_tokens = pt  # tracks actual current context size
        self.turns += 1

    def percent_used(self) -> float:
        """Return percentage of context window consumed (0 if window unknown).

        Uses last_prompt_tokens (the prompt token count from the most recent API
        call) rather than session_total to avoid quadratic over-counting: each
        turn re-sends the full message history, so session_total grows without
        bound even when the actual context usage is stable.
        """
        if not self.context_window:
            return 0.0
        return min(100.0, self.last_prompt_tokens / self.context_window * 100)

    def summary(self) -> str:
        lines = [
            f"Tokens — prompt: {self.prompt_tokens}, "
            f"completion: {self.completion_tokens}, "
            f"total: {self.total_tokens}, "
            f"turns: {self.turns}"
        ]
        if self.context_window:
            pct = self.percent_used()
            lines.append(
                f"Context window: {self.last_prompt_tokens:,}"
                f"/{self.context_window:,} tokens ({pct:.1f}% used)"
            )
        return "\n".join(lines)


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
    tool_results_buffer: dict = None,
    show_thinking: bool = False,
) -> str:
    """Send a chat completion request and stream the response.

    Args:
        stream_output: When False, suppress stdout streaming (used for JSON/markdown
            output modes where the caller formats and prints the final response).
        permission: Tool execution permission level. "restricted" blocks all tool
            execution. "auto" (default) executes tools as requested. "elevated" is
            treated the same as "auto" (no additional privilege escalation in CLI).
        tool_results_buffer: Optional dict to collect tool results. Will be populated
            with {tool_name: result} entries during tool execution.
        show_thinking: When True, stream <think>...</think> blocks to stdout.
            When False (default), thinking blocks are suppressed from stdout but
            the full response (including thinking) is still returned for history.
    Returns the full response text including thinking. Handles tool-calling loops.
    """
    tool_call_counter = 0
    collected_output = []
    if tool_results_buffer is None:
        tool_results_buffer = {}
    thinking_filter = ThinkingBuffer(show=show_thinking)

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
                    filtered = thinking_filter.feed(token)
                    if filtered:
                        sys.stdout.write(filtered)
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

        if stream_output:
            remaining = thinking_filter.flush()
            if remaining:
                sys.stdout.write(remaining)
                sys.stdout.flush()

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

            _print_info(
                f"[Wee] Executing: {func_name}({json.dumps(func_args)[:300]})"
                + ("..." if len(json.dumps(func_args)) > 300 else "")
            )
            tool_result = execute_tool(func_name, func_args, permission=permission)

            # Display tool result to user
            if tool_result:
                result_preview = (
                    tool_result[:500] if len(str(tool_result)) > 500 else tool_result
                )
                _print_info(f"[Wee] Result: {result_preview}")

            # Store tool result for later viewing
            if func_name not in tool_results_buffer:
                tool_results_buffer[func_name] = []
            tool_results_buffer[func_name].append(
                {
                    "args": func_args,
                    "result": tool_result or "No output",
                    "tool_id": tc_id,
                }
            )

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
  /clear              Clear conversation history
  /history            Show conversation history
  /model MODEL         Switch model
  /model set MODEL     Switch model (explicit form)
  /model current       Show the current provider-qualified model
  /model list          List all available models
  /model list PROVIDER List models from specific provider (ollama, openrouter, lmstudio)
  /tokens             Show token usage
  /context            Show context window usage and compaction trigger point
  /compact [PCT]      Summarize old context to target percent of window (default 50)
  /system PROMPT      Set system prompt
  /config             Show current configuration
  /tools              Show tool status
  /tools on|off       Enable/disable tool calling
  /tools-output       Show last tool call outputs
  /thinking           Show thinking visibility status
  /thinking on|off    Show or hide model thinking output (default: off)
  /permission MODE    Set permission level (restricted, auto, elevated)
  /agents             List agents. Use call_agent tool to dispatch work
  /skills             List available skills (alias: /discover-skills)
  /version            Show version
  /help               Show this help
  /exit, /quit        Exit interactive mode

Keyboard Shortcuts:
  Ctrl+O              Show last tool call outputs (same as /tools-output)
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
    model_str: str = None,
    session_name: str = None,
    existing_messages: list = None,
    show_thinking: bool = False,
) -> str:
    """Run the interactive REPL.

    Args:
        model: Resolved model name (without provider prefix)
        model_str: Original model string from CLI (with provider prefix if used)

    Returns:
        Updated model string (to persist across sessions, in original form)
    """
    _init_readline()

    client = _make_client(api_base, api_key, timeout)
    context_window = get_context_window(model)
    token_tracker = TokenTracker(context_window=context_window)
    messages, effective_system = _prepare_session_messages(
        system_prompt, existing_messages
    )

    # Track if this is the first message (for CWD context injection)
    first_message = not any(m.get("role") == "user" for m in messages)
    # Buffer to store tool results from the last interaction
    tool_results_buffer = {}
    # Track original user input for model persistence (with provider prefix if used)
    # If model_str wasn't passed, default to the resolved model name
    model_for_persistence = model_str if model_str else model
    rich_ui = _interactive_ui_enabled()
    _render_interactive_banner(
        model_for_persistence, permission, tools_enabled, session_name
    )

    while True:
        try:
            user_input = _read_interactive_prompt(
                model_for_persistence, permission, tools_enabled, token_tracker
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            break

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
                if session_name:
                    save_session_data(
                        session_name,
                        _session_payload(
                            messages,
                            model_for_persistence,
                            system_prompt,
                            tools_enabled,
                            temperature,
                            timeout,
                            output_format,
                            permission,
                        ),
                    )
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
                model_arg = arg.strip()
                if model_arg.lower() == "set":
                    _print_error("Usage: /model set PROVIDER/MODEL")
                    continue
                if model_arg.lower().startswith("set "):
                    model_arg = model_arg[4:].strip()

                if not model_arg or model_arg.lower() in {"current", "show", "get"}:
                    _print_info(f"Current model: {model_for_persistence}")
                elif model_arg.lower() == "list":
                    list_available_models()
                elif model_arg.lower().startswith("list "):
                    # /model list <provider>
                    provider = model_arg[5:].strip()
                    list_available_models(provider)
                else:
                    old_model = model_for_persistence
                    model_for_persistence = (
                        model_arg  # Keep provider-qualified ID for persistence
                    )
                    resolved_model, api_base, api_key = resolve_model_and_endpoint(
                        model_arg
                    )
                    client = _make_client(api_base, api_key, timeout)
                    model = resolved_model  # Use resolved model for API calls
                    token_tracker.context_window = get_context_window(model)
                    _print_info(
                        f"Model switched: {old_model} → {model_for_persistence}"
                    )
                continue

            elif cmd == "/tokens":
                if rich_ui:
                    _console.print(
                        Panel(
                            Text(token_tracker.summary(), style="bright_white"),
                            title="[bold bright_green]Usage[/]",
                            title_align="left",
                            border_style="green",
                            box=box.ROUNDED,
                        )
                    )
                else:
                    _print_info(token_tracker.summary())
                continue

            
            elif cmd == "/context":
                # Display context window usage and compaction trigger point
                if not token_tracker.context_window:
                    _print_error("Context window size unknown. Run a query first.")
                    continue
                
                trigger_pct = COMPACT_TRIGGER_FRACTION * 100
                current_pct = token_tracker.percent_used()
                trigger_tokens = int(token_tracker.context_window * COMPACT_TRIGGER_FRACTION)
                remaining_tokens = token_tracker.context_window - token_tracker.last_prompt_tokens
                tokens_to_trigger = trigger_tokens - token_tracker.last_prompt_tokens
                
                lines = [
                    f"Context Window: {token_tracker.last_prompt_tokens:,} / {token_tracker.context_window:,} tokens",
                    f"Usage: {current_pct:.1f}%",
                    f"Compaction Trigger: {trigger_pct:.0f}% ({trigger_tokens:,} tokens)",
                ]
                
                if tokens_to_trigger > 0:
                    lines.append(f"Tokens until trigger: {tokens_to_trigger:,} ({(tokens_to_trigger / token_tracker.context_window * 100):.1f}%)")
                else:
                    lines.append(f"⚠ TRIGGER POINT REACHED — consider running /compact")
                
                lines.append(f"Tokens remaining: {remaining_tokens:,}")
                
                _print_info("\n".join(lines))
                continue
                
            elif cmd == "/compact":
                if arg:
                    try:
                        target_pct = int(arg)
                    except ValueError:
                        _print_error("Invalid compact target. Use an integer 10-90.")
                        continue
                else:
                    target_pct = 50

                if target_pct < 10 or target_pct > 90:
                    _print_error("Compact target must be between 10 and 90.")
                    continue

                current_window = token_tracker.context_window or get_context_window(
                    model
                )
                token_tracker.context_window = current_window
                before_tokens = count_message_tokens(messages, model)
                target_tokens = max(1, int(current_window * (target_pct / 100.0)))

                compacted, summary = compact_messages(
                    messages=messages,
                    target_tokens=target_tokens,
                    model=model,
                    client=client,
                )
                if compacted == messages and not summary:
                    _print_info("Compaction skipped — history is already short.")
                    continue

                after_tokens = count_message_tokens(compacted, model)
                before_count = len(messages)
                messages = compacted
                token_tracker = TokenTracker(context_window=current_window)
                if session_name:
                    save_session_data(
                        session_name,
                        _session_payload(
                            messages,
                            model_for_persistence,
                            system_prompt,
                            tools_enabled,
                            temperature,
                            timeout,
                            output_format,
                            permission,
                        ),
                    )
                _print_info(
                    f"Compacted: {before_count} → {len(messages)} messages, "
                    f"~{before_tokens:,} → ~{after_tokens:,} tokens"
                )
                if summary:
                    preview = summary if len(summary) <= 240 else summary[:240] + "..."
                    _print_info(f"Summary: {preview}")
                continue

            elif cmd == "/system":
                if not arg:
                    _print_info(f"System prompt: {effective_system[:200]}...")
                else:
                    system_prompt = arg
                    effective_system = _build_effective_system_prompt(arg)
                    messages[0] = {"role": "system", "content": effective_system}
                    if session_name:
                        save_session_data(
                            session_name,
                            _session_payload(
                                messages,
                                model_for_persistence,
                                system_prompt,
                                tools_enabled,
                                temperature,
                                timeout,
                                output_format,
                                permission,
                            ),
                        )
                    _print_info("System prompt updated.")
                continue

            elif cmd == "/config":
                status = "enabled" if tools_enabled else "disabled"
                _render_config_panel(
                    [
                        ("Model", model_for_persistence),
                        ("Provider", _provider_label(model_for_persistence)),
                        ("API base", api_base),
                        ("Tools", status),
                        ("Temperature", temperature or "default"),
                        ("Timeout", f"{timeout}s"),
                        ("Permission", permission),
                        ("Output", output_format),
                    ]
                )
                continue

            elif cmd == "/tools":
                if not arg:
                    status = "enabled" if tools_enabled else "disabled"
                    _print_info(f"Tool calling is {status}.")
                elif arg.lower() in ("on", "enable", "yes", "true"):
                    tools_enabled = True
                    _print_info("Tool calling enabled.")
                elif arg.lower() in ("off", "disable", "no", "false"):
                    tools_enabled = False
                    _print_info("Tool calling disabled.")
                else:
                    _print_error(f"Invalid argument: {arg}. Use 'on' or 'off'.")
                continue

            elif cmd == "/thinking":
                if not arg:
                    status = "on" if show_thinking else "off"
                    _print_info(f"Model thinking visibility is {status}.")
                elif arg.lower() in ("on", "show", "yes", "true"):
                    show_thinking = True
                    _print_info("Thinking visibility enabled.")
                elif arg.lower() in ("off", "hide", "no", "false"):
                    show_thinking = False
                    _print_info("Thinking visibility disabled.")
                else:
                    _print_error(f"Invalid argument: {arg}. Use 'on' or 'off'.")
                continue

            elif cmd == "/tools-output":
                if not tool_results_buffer:
                    _print_info("No tool results from recent calls.")
                else:
                    _print_info("Tool Execution Results:")
                    for tool_name, results in tool_results_buffer.items():
                        for i, entry in enumerate(results, 1):
                            _print_info(f"\n  [{tool_name}#{i}]")
                            _print_info(f"    Args: {json.dumps(entry['args'])[:150]}")
                            if len(json.dumps(entry["args"])) > 150:
                                _print_info("           ...")
                            result_lines = str(entry["result"]).split("\n")
                            for line in result_lines[:20]:
                                _print_info(f"    {line}")
                            if len(result_lines) > 20:
                                _print_info(
                                    f"    ... ({len(result_lines) - 20} more lines)"
                                )
                continue

            elif cmd == "/permission":
                if not arg:
                    _print_info(f"Permission level: {permission}")
                elif arg.lower() in ("restricted", "auto", "elevated"):
                    old_perm = permission
                    permission = arg.lower()
                    _print_info(f"Permission: {old_perm} → {permission}")
                else:
                    _print_error(
                        f"Invalid permission: {arg}. "
                        "Use 'restricted', 'auto', or 'elevated'."
                    )
                continue

            elif cmd == "/version":
                _print_info(f"Wee CLI v{__version__}")
                continue

            elif cmd == "/agents":
                agents = get_agent_info()
                if agents:
                    _print_info("Available Agents (from agents.json):")
                    for agent in agents:
                        print(f"  {agent['name']:20} — {agent['description'][:70]}")
                else:
                    _print_info("No agents configured")
                continue

            elif cmd == "/skills" or (cmd == "/discover-skills"):
                skills = discover_skills(search_cwd=True)
                if skills:
                    _print_info(f"Available Skills ({len(skills)} found):")
                    for skill_name, skill_info in sorted(skills.items()):
                        status = "✓ loaded" if skill_info["loaded"] else ""
                        print(f"  {skill_name:30} {status}")
                        print(f"    {skill_info['description'][:70]}")
                else:
                    _print_info("No skills discovered")
                continue

            elif cmd == "/help":
                _render_repl_help()
                continue

            else:
                _print_info(f"Unknown command: {cmd}. Type /help for commands.")
                continue

        # On first message, inject context about CWD agents/skills
        if first_message:
            first_message = False
            cwd_agents = load_agents_json(search_cwd=True)

            # Check if CWD has its own agents.json
            cwd_agents_file = os.path.join(os.getcwd(), "agents.json")
            cwd_has_agents = os.path.isfile(cwd_agents_file)

            # Check if CWD has AGENTS.md
            cwd_agents_md = os.path.join(os.getcwd(), "AGENTS.md")
            cwd_has_agents_md = os.path.isfile(cwd_agents_md)

            # Check if CWD/skills exists
            cwd_skills_dir = os.path.join(os.getcwd(), "skills")
            cwd_has_skills_dir = os.path.isdir(cwd_skills_dir)

            # If CWD has local agents/skills, add system context
            if cwd_has_agents or cwd_has_agents_md or cwd_has_skills_dir:
                context = "\nContext from current working directory:"

                # Read AGENTS.md if it exists
                if cwd_has_agents_md:
                    try:
                        with open(
                            cwd_agents_md, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            agents_md_content = f.read()
                        # Extract first 500 chars to avoid bloating the context
                        context += "\n\nAGENTS.md (available in CWD):\n"
                        context += agents_md_content[:500]
                        if len(agents_md_content) > 500:
                            context += "\n... (see full AGENTS.md in CWD)"
                    except (OSError, IOError):
                        pass

                if cwd_has_agents:
                    agents = cwd_agents.get("agents", [])
                    if agents:
                        context += f"\n\nAvailable agents ({len(agents)}):"
                        for agent in agents[:5]:
                            context += (
                                f"\n  - {agent.get('name', 'unknown')}: "
                                f"{agent.get('description', '')[:60]}"
                            )
                        if len(agents) > 5:
                            context += f"\n  ... and {len(agents) - 5} more"

                if cwd_has_skills_dir:
                    # Re-discover to get only CWD skills
                    cwd_only_skills = {}
                    if os.path.isdir(cwd_skills_dir):
                        try:
                            for entry in os.listdir(cwd_skills_dir):
                                skill_path = os.path.join(cwd_skills_dir, entry)
                                metadata_file = os.path.join(
                                    skill_path, "skill_metadata.json"
                                )
                                if os.path.isfile(metadata_file):
                                    try:
                                        with open(metadata_file) as f:
                                            metadata = json.load(f)
                                        cwd_only_skills[entry] = metadata.get(
                                            "description", ""
                                        )
                                    except (json.JSONDecodeError, OSError):
                                        pass
                        except OSError:
                            pass

                    if cwd_only_skills:
                        context += (
                            f"\n\nAvailable skills in ./skills"
                            f" ({len(cwd_only_skills)}):"
                        )
                        for skill_name in list(cwd_only_skills.keys())[:5]:
                            context += (
                                f"\n  - {skill_name}: "
                                f"{cwd_only_skills[skill_name][:50]}"
                            )
                        if len(cwd_only_skills) > 5:
                            context += f"\n  ... and {len(cwd_only_skills) - 5} more"

                # Prepend context to first user message
                user_input = context + "\n\n" + user_input

        # Regular prompt
        messages.append({"role": "user", "content": user_input})

        try:
            tool_results_buffer = {}  # Clear buffer for new interaction
            tool_names = []
            activity = None

            def on_activity(kind, payload):
                if kind != "tool_call":
                    return
                tool_name = _sdk_tool_name(payload)
                if tool_name not in tool_names:
                    tool_names.append(tool_name)
                if activity is not None:
                    activity.update(
                        f"[bold bright_yellow]⚙ Running {escape(tool_name)}…[/]"
                    )

            status_context = (
                _console.status(
                    "[bold bright_green]🍀 Wee is thinking…[/]", spinner="dots"
                )
                if rich_ui
                else None
            )
            if status_context:
                activity = status_context.__enter__()
            try:
                try:
                    response = _chat_via_copilot_sdk(
                        messages=messages,
                        qualified_model=model_for_persistence,
                        api_base=api_base,
                        api_key=api_key,
                        timeout=timeout,
                        tools_enabled=tools_enabled and permission != "restricted",
                        stream_output=not rich_ui,
                        activity_callback=on_activity,
                    )
                except Exception as sdk_error:
                    fallback_enabled = os.environ.get(
                        "WEE_OPENAI_FALLBACK_ENABLED", "1"
                    ).lower() not in {"0", "false", "no", "off"}
                    if not fallback_enabled:
                        raise
                    _print_info(
                        f"Copilot SDK unavailable ({sdk_error}); "
                        "using direct provider fallback."
                    )
                    response = chat_stream(
                        client=client,
                        model=model,
                        messages=messages,
                        tools_enabled=tools_enabled,
                        temperature=temperature,
                        token_tracker=token_tracker,
                        permission=permission,
                        stream_output=not rich_ui,
                        tool_results_buffer=tool_results_buffer,
                        show_thinking=show_thinking,
                    )
            finally:
                if status_context:
                    status_context.__exit__(None, None, None)

            if rich_ui:
                for tool_name in tool_names:
                    _console.print(
                        f"[bright_yellow]  ✓[/] [grey70]{escape(tool_name)}[/]"
                    )
                display_response = (
                    response if show_thinking else _strip_thinking(response)
                )
                _render_assistant_response(display_response, output_format)
            messages.append({"role": "assistant", "content": response})
            if session_name:
                save_session_data(
                    session_name,
                    _session_payload(
                        messages,
                        model_for_persistence,
                        system_prompt,
                        tools_enabled,
                        temperature,
                        timeout,
                        output_format,
                        permission,
                    ),
                )
            if (
                token_tracker.context_window
                and token_tracker.percent_used() >= COMPACT_TRIGGER_FRACTION * 100
            ):
                _print_info(
                    f"⚠ Context at {token_tracker.percent_used():.1f}% — "
                    "consider /compact to free space."
                )
        except KeyboardInterrupt:
            print("\n[interrupted]")
            messages.append({"role": "assistant", "content": "[interrupted]"})
            if session_name:
                save_session_data(
                    session_name,
                    _session_payload(
                        messages,
                        model_for_persistence,
                        system_prompt,
                        tools_enabled,
                        temperature,
                        timeout,
                        output_format,
                        permission,
                    ),
                )
        except Exception as e:
            _print_error(str(e))
            messages.append({"role": "assistant", "content": f"[error: {e}]"})
            if session_name:
                save_session_data(
                    session_name,
                    _session_payload(
                        messages,
                        model_for_persistence,
                        system_prompt,
                        tools_enabled,
                        temperature,
                        timeout,
                        output_format,
                        permission,
                    ),
                )

    _save_readline()
    if rich_ui:
        _console.print(Rule(style="grey23"))
        _console.print(
            Text(token_tracker.summary(), style="grey58"),
            justify="right",
        )
        _console.print("[bold bright_green]🍀 Goodbye![/]")
    else:
        _print_info(f"\n{token_tracker.summary()}")
        _print_info("Goodbye!")
    return model_for_persistence


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
    existing_messages: list = None,
    show_thinking: bool = False,
    qualified_model: str = None,
):
    """Run a single prompt and exit."""
    client = _make_client(api_base, api_key, timeout)
    token_tracker = TokenTracker(context_window=get_context_window(model))
    messages, _ = _prepare_session_messages(system_prompt, existing_messages)

    messages.append({"role": "user", "content": prompt})

    # For non-text output formats, suppress streaming so we can reformat
    stream_output = output_format == "text"

    try:
        try:
            response = _chat_via_copilot_sdk(
                messages=messages,
                qualified_model=qualified_model or model,
                api_base=api_base,
                api_key=api_key,
                timeout=timeout,
                tools_enabled=tools_enabled and permission != "restricted",
                stream_output=stream_output,
            )
        except Exception as sdk_error:
            fallback_enabled = os.environ.get(
                "WEE_OPENAI_FALLBACK_ENABLED", "1"
            ).lower() not in {"0", "false", "no", "off"}
            if not fallback_enabled:
                raise
            _print_info(
                f"Copilot SDK unavailable ({sdk_error}); using direct provider fallback."
            )
            response = chat_stream(
                client=client,
                model=model,
                messages=messages,
                tools_enabled=tools_enabled,
                temperature=temperature,
                token_tracker=token_tracker,
                permission=permission,
                stream_output=stream_output,
                show_thinking=show_thinking,
            )
        messages.append({"role": "assistant", "content": response})
        # For non-streaming formats (json/markdown), strip thinking here since
        # ThinkingBuffer only applies during streaming (text format).
        display_response = response if show_thinking else _strip_thinking(response)
        _print_markdown(display_response, output_format)
        return messages
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
            '  wee exec "Summarize this repository"\n'
            '  wee exec --session demo "Start a local persisted session"\n'
            '  wee exec --resume --session demo "Continue that session"\n'
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
        help="Enable tool calling (bash, python, edit_file, search, call_agent)",
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
        "--session",
        default=None,
        help="Persist conversation state to a named local session under ~/.wee/sessions/",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume an existing local session (defaults to 'default' if --session is omitted)",
    )
    parser.add_argument(
        "--list-models",
        nargs="?",
        const=True,
        metavar="PROVIDER",
        help="List available models (optional: ollama, openrouter, lmstudio)",
    )
    parser.add_argument(
        "--show-thinking",
        action="store_true",
        default=False,
        help=(
            "Show model thinking/reasoning output (<think>...</think> blocks) "
            "when the runtime emits it. By default, thinking is hidden."
        ),
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt text (omit for interactive mode or pipe from stdin)",
    )
    return parser


def _normalize_argv(argv=None):
    """Normalize convenience CLI forms before argparse processing.

    Supports:
    - `wee exec "prompt"` as a codex-style one-shot alias
    """
    if argv is None:
        argv = sys.argv[1:]
    normalized = list(argv)
    exec_mode = False

    if normalized and normalized[0] == "exec":
        exec_mode = True
        normalized = normalized[1:]

    return normalized, exec_mode


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main(argv=None):
    """Main entry point for wee CLI."""
    parser = build_parser()
    normalized_argv, exec_mode = _normalize_argv(argv)
    args = parser.parse_args(normalized_argv)

    # Handle --list-models
    if args.list_models is not None:
        from wee_runtime import list_available_models

        provider = None if args.list_models is True else args.list_models
        list_available_models(provider)
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

    default_interactive = args.interactive or not args.prompt
    session_name = None
    if args.session:
        try:
            session_name = _validate_session_name(args.session)
        except ValueError as e:
            parser.error(str(e))
    elif args.resume:
        session_name = "default"
    elif default_interactive and not exec_mode:
        session_name = "default"

    session_data = {}
    should_resume_session = bool(
        args.resume or (session_name == "default" and default_interactive)
    )
    if session_name and should_resume_session:
        session_data = load_session_data(session_name)

    # Resolve model: CLI arg > env var > config file > default
    model_candidates = (
        args.model,
        os.environ.get("WEE_MODEL"),
        cfg.get("model"),
        session_data.get("model"),
        "ollama/qwen3.5:4b",
    )
    model_str = next(
        (
            normalized
            for candidate in model_candidates
            if (normalized := _normalize_model_identifier(candidate))
        ),
        "ollama/qwen3.5:4b",
    )

    # Issue #426: persist the repaired default config immediately so a model
    # value written by the old `/model set` parser cannot break future starts.
    configured_model = cfg.get("model")
    normalized_configured_model = _normalize_model_identifier(configured_model)
    if configured_model and normalized_configured_model != configured_model:
        cfg["model"] = normalized_configured_model or model_str
        os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

    # Resolve other settings from config
    system_prompt = args.system or cfg.get("system_prompt", "")
    tools_enabled = args.tools or cfg.get("tools", False)
    permission = args.permission or cfg.get("permission", "auto")
    temperature = (
        args.temperature if args.temperature is not None else cfg.get("temperature")
    )
    timeout = args.timeout or cfg.get("timeout", 120.0)
    output_format = args.output or cfg.get("output", "text")

    existing_messages = session_data.get("messages") if should_resume_session else None

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
        updated_model = run_interactive(
            model=model,
            api_base=api_base,
            api_key=api_key,
            tools_enabled=tools_enabled,
            temperature=temperature,
            timeout=timeout,
            system_prompt=system_prompt,
            output_format=output_format,
            permission=permission,
            model_str=model_str,
            session_name=session_name,
            existing_messages=existing_messages,
            show_thinking=args.show_thinking,
        )
        # Persist model choice for next session
        cfg["model"] = updated_model
        save_config(cfg)
        return

    # Check for piped stdin
    stdin_is_pipe = not sys.stdin.isatty()

    if stdin_is_pipe and not prompt_text:
        # Read from stdin
        try:
            prompt_text = sys.stdin.read().strip()
        except KeyboardInterrupt:
            sys.exit(130)

    if exec_mode and not prompt_text:
        parser.error("exec requires a prompt or piped stdin")

    if not prompt_text and not stdin_is_pipe:
        # Interactive REPL (no args, tty)
        # Enable tools by default in interactive mode (unless explicitly disabled)
        if not args.tools and not cfg.get("tools"):
            tools_enabled = True
        updated_model = run_interactive(
            model=model,
            api_base=api_base,
            api_key=api_key,
            tools_enabled=tools_enabled,
            temperature=temperature,
            timeout=timeout,
            system_prompt=system_prompt,
            output_format=output_format,
            permission=permission,
            model_str=model_str,
            session_name=session_name,
            existing_messages=existing_messages,
            show_thinking=args.show_thinking,
        )
        # Persist model choice for next session
        cfg["model"] = updated_model
        save_config(cfg)
    elif prompt_text:
        # Single-shot mode
        messages = run_single_shot(
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
            existing_messages=existing_messages,
            show_thinking=args.show_thinking,
            qualified_model=model_str,
        )
        if session_name:
            save_session_data(
                session_name,
                _session_payload(
                    messages,
                    model_str,
                    system_prompt,
                    tools_enabled,
                    temperature,
                    timeout,
                    output_format,
                    permission,
                ),
            )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
