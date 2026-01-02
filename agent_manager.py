#!/usr/bin/env python3
"""
Unified AI Session Wrapper for N8N Integration
Wraps GitHub Copilot CLI and OpenCode CLI
Manages session ID mapping between N8N chat sessions and AI backend sessions
"""

import sys
import os
import json
import subprocess
import re
import signal
import time
import argparse
from pathlib import Path
from uuid import uuid4


# Environment-based configuration
def get_default_agent() -> str:
    """Get default agent from environment or use orchestrator"""
    return os.environ.get("COPILOT_DEFAULT_AGENT", "orchestrator")


def get_default_model() -> str:
    """Get default model from environment or use gpt-5-mini"""
    return os.environ.get("COPILOT_DEFAULT_MODEL", "gpt-5-mini")


def get_default_runtime() -> str:
    """Get default runtime from environment or use copilot"""
    return os.environ.get("COPILOT_DEFAULT_RUNTIME", "copilot")


def get_command_timeout() -> int:
    """Get command execution timeout from environment or use default 300 seconds"""
    try:
        timeout_str = os.environ.get("COMMAND_TIMEOUT", "300")
        timeout = int(timeout_str)
        # Ensure minimum timeout of 30 seconds
        if timeout < 30:
            print(
                f"Warning: COMMAND_TIMEOUT must be at least 30 seconds, using 30",
                file=sys.stderr,
            )
            return 30
        return timeout
    except ValueError:
        print(
            f"Warning: COMMAND_TIMEOUT must be an integer, using default 300 seconds",
            file=sys.stderr,
        )
        return 300


class SessionManager:
    """Manages AI CLI sessions (Copilot & OpenCode) for N8N integration"""

    # Query tracking constants
    MAX_PROMPT_LENGTH = 200  # Maximum chars to store from prompt
    MAX_OUTPUT_LENGTH = 500  # Maximum chars to store from output
    MAX_OUTPUT_DISPLAY = 300  # Maximum chars to display in status output

    # Model configurations
    # Note: Claude Code CLI does not support dynamic model listing via flag.
    # We use CLI aliases (sonnet, haiku, opus) as primary IDs to let the CLI resolve to the latest versions.
    CLAUDE_MODELS = {
        "Anthropic Models": [
            (
                "sonnet",
                "Claude Sonnet (Latest)",
                ["claude-sonnet", "claude-sonnet-4.5", "sonnet-4.5"],
            ),
            (
                "haiku",
                "Claude Haiku (Latest)",
                ["claude-haiku", "claude-haiku-4.5", "haiku-4.5"],
            ),
            (
                "opus",
                "Claude Opus (Latest)",
                ["claude-opus", "claude-opus-4.5", "opus-4.5"],
            ),
        ]
    }

    OPENCODE_MODELS = {}

    # Gemini models configuration
    # Note: These are common Gemini models; the CLI may support additional models
    GEMINI_MODELS = {
        "Google Models": [
            (
                "gemini-2.0-flash-exp",
                "Gemini 2.0 Flash (Experimental)",
                ["gemini-2.0-flash", "flash-2.0"],
            ),
            ("gemini-1.5-pro", "Gemini 1.5 Pro", ["gemini-pro-1.5", "pro-1.5"]),
            ("gemini-1.5-flash", "Gemini 1.5 Flash", ["gemini-flash-1.5", "flash-1.5"]),
            ("gemini-pro", "Gemini Pro", ["gemini-1.0-pro"]),
        ]
    }

    # CODEX models configuration
    CODEX_MODELS = {
        "OpenAI Models": [
            ("gpt-5.1-codex-max", "GPT-5.1 Codex Max", ["gpt-5.1", "codex-max"]),
            ("gpt-5-codex", "GPT-5 Codex", ["gpt-5", "codex"]),
            ("gpt-4-turbo", "GPT-4 Turbo", ["gpt-4-turbo-preview", "gpt-4"]),
        ]
    }

    def __init__(self, config_file: str | None = None):
        # Copilot Paths
        self.copilot_home = Path.home() / ".copilot"
        self.session_map_file = self.copilot_home / "n8n-session-map.json"
        self.session_state_dir = self.copilot_home / "session-state"
        self.logs_dir = self.copilot_home / "logs"
        self.running_queries_file = self.copilot_home / "running-queries.json"

        # OpenCode Paths
        self.opencode_home = Path.home() / ".opencode"
        self.opencode_bin = self.opencode_home / "bin" / "opencode"
        self.opencode_session_storage = (
            Path.home()
            / ".local"
            / "share"
            / "opencode"
            / "storage"
            / "session"
            / "global"
        )

        # Claude Paths
        self.claude_home = Path.home() / ".claude"
        self.claude_debug_dir = self.claude_home / "debug"

        # Gemini Paths
        self.gemini_home = Path.home() / ".gemini"
        self.gemini_session_dir = self.gemini_home / "sessions"

        # CODEX Paths
        self.codex_home = Path.home() / ".codex"
        self.codex_session_dir = self.codex_home / "sessions"

        # Ensure directories exist
        self.copilot_home.mkdir(exist_ok=True)
        self.session_state_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        self.opencode_home.mkdir(exist_ok=True)
        self.claude_home.mkdir(exist_ok=True)
        self.gemini_home.mkdir(exist_ok=True)
        self.gemini_session_dir.mkdir(exist_ok=True)
        self.codex_home.mkdir(exist_ok=True)
        self.codex_session_dir.mkdir(exist_ok=True)

        # Load agents from config file
        self.AGENTS = self._load_agents_config(config_file)

        # Load command timeout from environment
        self.command_timeout = get_command_timeout()

    def _load_agents_config(self, config_file: str | None = None) -> dict:
        """Load agents configuration from JSON file"""
        if config_file is None:
            # Look for agents.json in current directory or script directory
            config_path = (
                Path(config_file) if config_file else Path.cwd() / "agents.json"
            )
            if not config_path.exists():
                config_path = Path(__file__).parent / "agents.json"
        else:
            config_path = Path(config_file)

        if not config_path.exists():
            print(
                f"[Warning] Agents config file not found at {config_path}. Using empty agents.",
                file=sys.stderr,
            )
            return {}

        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                agents = {}
                for agent in config.get("agents", []):
                    name = agent.get("name")
                    if not name:
                        print(
                            f"[Warning] Agent entry missing 'name' field",
                            file=sys.stderr,
                        )
                        continue
                    agents[name] = {
                        "path": agent.get("path", ""),
                        "description": agent.get("description", ""),
                    }
                return agents
        except json.JSONDecodeError as e:
            print(f"[Error] Failed to parse agents config: {e}", file=sys.stderr)
            return {}
        except Exception as e:
            print(f"[Error] Failed to load agents config: {e}", file=sys.stderr)
            return {}

    def load_running_queries(self) -> dict:
        """Load the running queries tracking data"""
        if not self.running_queries_file.exists():
            return {}

        try:
            with open(self.running_queries_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def save_running_queries(self, queries: dict):
        """Save the running queries tracking data"""
        with open(self.running_queries_file, "w") as f:
            json.dump(queries, f, indent=2)

    def track_running_query(
        self, n8n_session_id: str, pid: int, runtime: str, agent: str, prompt: str
    ):
        """Track a running query with its PID and session info"""
        queries = self.load_running_queries()
        queries[n8n_session_id] = {
            "pid": pid,
            "runtime": runtime,
            "agent": agent,
            "prompt": prompt[: self.MAX_PROMPT_LENGTH],
            "start_time": time.time(),
            "last_output": "",
        }
        self.save_running_queries(queries)
        print(
            f"[Track] Started tracking query for session {n8n_session_id}, PID: {pid}",
            file=sys.stderr,
        )

    def update_query_output(self, n8n_session_id: str, output_snippet: str):
        """Update the last output snippet for a running query"""
        queries = self.load_running_queries()
        if n8n_session_id in queries:
            queries[n8n_session_id]["last_output"] = output_snippet[
                -self.MAX_OUTPUT_LENGTH :
            ]
            self.save_running_queries(queries)

    def clear_running_query(self, n8n_session_id: str):
        """Clear tracking for a completed/cancelled query"""
        queries = self.load_running_queries()
        if n8n_session_id in queries:
            del queries[n8n_session_id]
            self.save_running_queries(queries)
            print(
                f"[Track] Cleared tracking for session {n8n_session_id}",
                file=sys.stderr,
            )

    def get_running_query(self, n8n_session_id: str) -> dict | None:
        """Get tracking info for a running query"""
        queries = self.load_running_queries()
        return queries.get(n8n_session_id)

    def is_process_running(self, pid: int) -> bool:
        """Check if a process with given PID is still running

        Uses os.kill with signal 0 to test process existence without
        actually sending a signal to the process.
        """
        try:
            os.kill(pid, 0)  # Signal 0 tests existence without affecting the process
            return True
        except OSError:
            return False

    def kill_process(self, pid: int) -> bool:
        """Kill a process with given PID"""
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except OSError as e:
            print(f"[Error] Failed to kill process {pid}: {e}", file=sys.stderr)
            return False

    def fetch_copilot_models(self) -> dict:
        """Fetch available models from copilot CLI help text"""
        try:
            # Use --no-color to ensure clean text
            cmd = ["/usr/bin/copilot", "--help", "--no-color"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Copilot help command failed: {result.stderr}", file=sys.stderr)
                return {}

            # Method 1: Robust Regex
            # Look for --model, then content, then (choices: ... )
            # We use [\s\S] instead of . with re.DOTALL for explicit multiline matching
            match = re.search(
                r"--model\s+<model>[\s\S]*?\(choices:\s*([\s\S]*?)\)", result.stdout
            )

            models = []
            if match:
                raw_content = match.group(1)
                models = re.findall(r'"([^"]+)"', raw_content)

            # Method 2: Fallback (if regex fails due to layout changes)
            if not models:
                # Look for known models as a sanity check/fallback
                fallback_models = ["gpt-5.2", "claude-sonnet-4.5", "gpt-5"]
                found_fallbacks = [m for m in fallback_models if m in result.stdout]
                if found_fallbacks:
                    # If we found known models but regex failed, try a looser regex
                    loose_match = re.findall(r'"([a-zA-Z0-9\-\.]+)"', result.stdout)
                    # Filter for likely model names (heuristic)
                    models = [
                        m
                        for m in loose_match
                        if "gpt" in m or "claude" in m or "gemini" in m
                    ]

            if not models:
                return {}

            # Categorize
            categorized = {}
            for m in models:
                cat = "Other Models"
                if "claude" in m.lower():
                    cat = "Claude Models"
                elif "gpt" in m.lower() or re.match(r"^o\d", m.lower()):
                    cat = "GPT Models"
                elif "gemini" in m.lower():
                    cat = "Google Models"

                if cat not in categorized:
                    categorized[cat] = []
                categorized[cat].append(m)

            return categorized
        except Exception as e:
            print(f"Error fetching copilot models: {e}", file=sys.stderr)
            return {}

    def fetch_opencode_models(self) -> dict:
        """Fetch available models from opencode CLI"""
        try:
            cmd = [str(self.opencode_bin), "models"]
            # Increased timeout to 30s as remote checks might be slow
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                print(
                    f"[Error] opencode models failed (exit {result.returncode}): {result.stderr}",
                    file=sys.stderr,
                )
                return {}

            if not result.stdout.strip():
                print(
                    "[Warning] opencode models returned empty output", file=sys.stderr
                )
                return {}

            models_by_provider = {}
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue

                parts = line.split("/", 1)
                if len(parts) == 2:
                    provider, model = parts
                else:
                    provider = "other"
                    model = line

                if provider not in models_by_provider:
                    models_by_provider[provider] = []
                models_by_provider[provider].append(line)

            return models_by_provider
        except subprocess.TimeoutExpired:
            print(
                "[Error] opencode models command timed out after 30s", file=sys.stderr
            )
            return {}
        except Exception as e:
            print(f"Error fetching opencode models: {e}", file=sys.stderr)
            return {}

    def load_session_map(self) -> dict:
        """Load the N8N -> Session ID mapping"""
        if not self.session_map_file.exists():
            return {}

        try:
            with open(self.session_map_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def save_session_map(self, session_map: dict):
        """Save the N8N -> Session ID mapping"""
        with open(self.session_map_file, "w") as f:
            json.dump(session_map, f, indent=2)

    def get_or_create_session_data(self, n8n_session_id: str) -> dict:
        """
        Get existing session data or create new default
        Returns dict with keys: session_id, model, agent, runtime
        """
        session_map = self.load_session_map()

        default_runtime = get_default_runtime()
        default_model = get_default_model()

        # Adjust default model based on runtime if using defaults
        # This handles the case where a new session is created with a non-Copilot runtime
        if default_runtime == "claude":
            default_model = "haiku"
        elif default_runtime == "opencode":
            default_model = "opencode/gpt-5-nano"
        elif default_runtime == "gemini":
            default_model = "gemini-1.5-flash"
        elif default_runtime == "codex":
            default_model = "gpt-5.1-codex-max"

        default_data = {
            "session_id": str(uuid4()),
            "model": default_model,
            "agent": get_default_agent(),
            "runtime": default_runtime,
        }

        if n8n_session_id in session_map:
            data = session_map[n8n_session_id]
            # Normalize old format (string ID or dict without runtime)
            if isinstance(data, str):
                return {**default_data, "session_id": data, "is_new": False}
            elif isinstance(data, dict):
                # Ensure all fields exist
                merged = {**default_data, **data}

                # If the runtime is set but model isn't (or is wrong for the runtime),
                # set a model appropriate for that runtime
                runtime = merged.get("runtime", default_runtime)
                if runtime == "claude":
                    if (
                        not merged.get("model")
                        or "gpt" in merged.get("model", "").lower()
                    ):
                        merged["model"] = "haiku"
                elif runtime == "opencode":
                    if not merged.get("model") or not merged.get(
                        "model", ""
                    ).startswith("opencode"):
                        merged["model"] = "opencode/gpt-5-nano"
                elif runtime == "gemini":
                    if (
                        not merged.get("model")
                        or "gemini" not in merged.get("model", "").lower()
                    ):
                        merged["model"] = "gemini-1.5-flash"
                elif runtime == "codex":
                    if (
                        not merged.get("model")
                        or "codex" not in merged.get("model", "").lower()
                    ):
                        merged["model"] = "gpt-5.1-codex-max"

                # Validate and fix session_id if corrupted
                # OpenCode uses "ses_*" format, others use UUID format
                session_id = merged.get("session_id", "")
                if runtime in ["claude", "gemini", "codex", "copilot"]:
                    # These should be UUIDs (36 chars with dashes)
                    if not session_id or not (
                        len(session_id) == 36 and "-" in session_id
                    ):
                        merged["session_id"] = str(uuid4())
                elif runtime == "opencode":
                    # OpenCode should be "ses_*" format
                    if not session_id or not session_id.startswith("ses_"):
                        merged["session_id"] = str(uuid4())

                # If we had to add/change fields, save it back
                if merged != data:
                    session_map[n8n_session_id] = merged
                    self.save_session_map(session_map)
                merged["is_new"] = False
                return merged

        # New session
        session_map[n8n_session_id] = default_data
        self.save_session_map(session_map)
        print(
            f"[Session] Created new session: {default_data['session_id']} (N8N: {n8n_session_id})",
            file=sys.stderr,
        )
        return {**default_data, "is_new": True}

    def update_session_field(self, n8n_session_id: str, field: str, value: str):
        """Update a specific field in the session map"""
        session_map = self.load_session_map()

        if n8n_session_id not in session_map:
            # Create new if doesn't exist
            self.get_or_create_session_data(n8n_session_id)
            session_map = self.load_session_map()

        if isinstance(session_map[n8n_session_id], str):
            # Convert old string format to dict
            session_map[n8n_session_id] = {
                "session_id": session_map[n8n_session_id],
                "model": get_default_model(),
                "agent": get_default_agent(),
                "runtime": get_default_runtime(),
            }

        session_map[n8n_session_id][field] = value

        # If switching runtime, we might want to reset the internal session ID or handle it
        # But for now we'll just update the field.
        # The execute method will handle generating a new underlying session ID if needed.

        self.save_session_map(session_map)

    def get_effective_timeout(self, session_data: dict) -> int:
        """Get the effective timeout for a session (session-specific or default)"""
        session_timeout = session_data.get("timeout")
        if session_timeout:
            try:
                return int(session_timeout)
            except ValueError:
                pass
        return self.command_timeout

    def get_render_type(self, session_data: dict) -> str:
        """Get the render type for a session (session-specific or default)"""
        return session_data.get("render_type", "text")

    def validate_telegram_html(self, text: str) -> tuple[bool, str]:
        """
        Validate that text only uses Telegram-supported HTML tags.
        Returns (is_valid, error_message)
        """
        import re

        # Supported tags in Telegram HTML mode
        supported_tags = {
            "b",
            "strong",
            "i",
            "em",
            "u",
            "ins",
            "s",
            "strike",
            "del",
            "span",
            "a",
            "code",
            "pre",
            "blockquote",
            "tg-spoiler",
            "tg-emoji",
        }

        # Find all HTML-like tags in the text
        # Pattern: <tag_name ...> or <tag_name>
        tag_pattern = r"</?([a-zA-Z][a-zA-Z0-9\-]*)"
        matches = re.finditer(tag_pattern, text)

        unsupported_tags = set()
        for match in matches:
            tag_name = match.group(1).lower()
            # For span with class, check if it's tg-spoiler
            if (
                tag_name == "span"
                and "tg-spoiler" in text[match.start() : match.start() + 50]
            ):
                continue
            if tag_name not in supported_tags:
                unsupported_tags.add(tag_name)

        if unsupported_tags:
            return (
                False,
                f"Unsupported HTML tags for Telegram: {', '.join(sorted(unsupported_tags))}",
            )

        return True, ""

    def sanitize_telegram_html(self, text: str) -> str:
        """
        Remove or escape unsupported HTML tags for Telegram compatibility.
        This is a fallback when the model generates unsupported tags.

        Escapes:
        - Double angle brackets (<<, >>) used in bash/scripting
        - Unsupported HTML tags
        Preserves:
        - Supported Telegram HTML tags
        """
        import re

        # Supported tags
        supported_tags = {
            "b",
            "strong",
            "i",
            "em",
            "u",
            "ins",
            "s",
            "strike",
            "del",
            "span",
            "a",
            "code",
            "pre",
            "blockquote",
            "tg-spoiler",
            "tg-emoji",
        }

        # First, escape double angle brackets (<<EOF, >>, etc.) that are used in scripting
        # Replace << with &lt;&lt; and >> with &gt;&gt;
        text = text.replace("<<", "&lt;&lt;")
        text = text.replace(">>", "&gt;&gt;")

        # Pattern to find HTML-like tags (single < followed by tag name, not double)
        # This won't match &lt; or already-escaped sequences
        def replace_tag(match):
            tag_full = match.group(0)
            tag_name = match.group(1).lower()

            # Check if this is a supported tag
            if tag_name in supported_tags:
                return tag_full  # Keep supported tags

            # For unsupported tags, convert to escaped text or remove
            # If it's a closing tag, just remove it
            if tag_full.startswith("</"):
                return ""

            # For opening tags, escape the angle brackets
            return tag_full.replace("<", "&lt;").replace(">", "&gt;")

        # Replace all tags - only match single < not preceded by &
        # This matches proper HTML tags but not &lt; or <<
        result = re.sub(r"(?<!&)</?([a-zA-Z][a-zA-Z0-9\-:]*)[^>]*>", replace_tag, text)
        return result

    def get_capabilities(self) -> str:
        """Get available capabilities based on configured agents"""
        if not self.AGENTS:
            return "No agents configured. Add agents to agents.json to extend capabilities."

        out = "# 🤖 Orchestrator Capabilities\n\n"
        out += "I can help with the following agents:\n\n"
        for agent_name, agent_info in self.AGENTS.items():
            description = agent_info.get("description", "No description")
            path = agent_info.get("path", "")
            out += f"### {agent_name}\n- **Description:** {description}\n- **Location:** `{path}`\n\n"
        out += "#### How to use\n"
        out += "- `/agent set <agent_name>` — switch to an agent and work with it.\n"
        out += "- `/agent list` — show all available agents and their locations.\n"

        return out

    def set_agent(self, n8n_session_id: str, agent: str) -> str:
        """Switch to a different agent"""
        if agent not in self.AGENTS:
            available = ", ".join(self.AGENTS.keys())
            return f"Unknown agent: '{agent}'. Available agents: {available}"

        session_map = self.load_session_map()

        # Generate a new session ID for the backend because sessions are often project-scoped
        new_backend_session_id = str(uuid4())

        if n8n_session_id not in session_map:
            session_map[n8n_session_id] = {
                "session_id": new_backend_session_id,
                "model": "gpt-5-mini",
                "agent": agent,
                "runtime": "copilot",
            }
        else:
            if isinstance(session_map[n8n_session_id], dict):
                session_map[n8n_session_id]["agent"] = agent
                session_map[n8n_session_id]["session_id"] = new_backend_session_id
            else:
                # Convert old format
                session_map[n8n_session_id] = {
                    "session_id": new_backend_session_id,
                    "model": "gpt-5-mini",
                    "agent": agent,
                    "runtime": "copilot",
                }

        self.save_session_map(session_map)
        agent_info = self.AGENTS[agent]
        print(
            f"[Agent] Switched to '{agent}' agent. New backend session: {new_backend_session_id}",
            file=sys.stderr,
        )
        return f"✓ Switched to **{agent}** agent\n\n{agent_info['description']}\n\nLocation: `{agent_info['path']}`"

    def detect_agent_delegation(self, prompt: str) -> tuple[str | None, str]:
        """Detect if user is asking for a specific agent to help with something

        Patterns detected:
        - "ask the family agent..."
        - "have the devops agent..."
        - "this is in the family agent"
        - "in the projects agent..."
        - "from the family agent..."

        Returns: (agent_name, modified_prompt) or (None, original_prompt)
        """
        prompt_lower = prompt.lower()

        # List of agent names to detect
        agent_keywords = {
            "family": ["family agent", "family knowledge"],
            "devops": ["devops agent", "devops"],
            "projects": ["projects agent", "projects"],
            "orchestrator": ["orchestrator agent", "orchestrator"],
        }

        # Delegation phrases
        delegation_phrases = [
            "ask the",
            "have the",
            "this is in the",
            "in the",
            "from the",
            "use the",
            "check the",
            "find in the",
            "search the",
        ]

        # Check if prompt contains delegation request
        for agent_name, keywords in agent_keywords.items():
            for keyword in keywords:
                if keyword in prompt_lower:
                    # Check if it's a delegation request
                    for phrase in delegation_phrases:
                        pattern = f"{phrase} {keyword}"
                        if pattern in prompt_lower:
                            # Extract just the actual question part
                            # Remove the "ask the family agent" part
                            modified = re.sub(
                                rf"\b{phrase}\s+{re.escape(keyword)}[,.]?\s*",
                                "",
                                prompt,
                                flags=re.IGNORECASE,
                            )
                            return agent_name, modified

        return None, prompt

    def parse_slash_command(self, prompt: str) -> tuple[str | None, str | None]:
        """Parse slash commands from the prompt."""
        if not prompt.startswith("/"):
            return None, None

        parts = prompt.split(None, 1)
        command = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else None

        return command, argument

    def get_model_from_name(self, name: str, runtime: str) -> str | None:
        """Convert model name/alias to full model ID based on runtime."""
        name_lower = name.lower().strip("\"'")

        if runtime == "claude":
            for category, models in self.CLAUDE_MODELS.items():
                for model_id, desc, aliases in models:
                    if name_lower == model_id.lower() or name_lower in aliases:
                        return model_id
            return None

        if runtime == "gemini":
            for category, models in self.GEMINI_MODELS.items():
                for model_id, desc, aliases in models:
                    aliases_lower = [a.lower() for a in aliases]
                    if name_lower == model_id.lower() or name_lower in aliases_lower:
                        return model_id
            return None

        if runtime == "codex":
            for category, models in self.CODEX_MODELS.items():
                for model_id, desc, aliases in models:
                    aliases_lower = [a.lower() for a in aliases]
                    if name_lower == model_id.lower() or name_lower in aliases_lower:
                        return model_id
            return None

        all_models = []
        if runtime == "opencode":
            models_by_provider = self.fetch_opencode_models()
            all_models = [m for sublist in models_by_provider.values() for m in sublist]
        else:  # copilot
            models_by_cat = self.fetch_copilot_models()
            all_models = [m for sublist in models_by_cat.values() for m in sublist]

        # 1. Exact match (case insensitive)
        for m in all_models:
            if m.lower() == name_lower:
                return m

        # 2. Suffix/Substring matching
        matches = []
        for m in all_models:
            if runtime == "opencode":
                # Check substring match
                if name_lower in m.lower():
                    matches.append(m)
            else:
                # Copilot specific aliases / substring
                if name_lower in m.lower():
                    matches.append(m)

        if len(matches) == 1:
            return matches[0]

        # Preference logic for ambiguous matches
        if matches:
            # Sort by length (descending) or alphanumeric?
            # Usually we want the latest version.
            matches.sort(reverse=True)
            return matches[0]

        return None

    def strip_thinking_tags(self, text: str) -> str:
        """Remove content within <think> tags"""
        # Remove complete think blocks
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        # Remove unclosed think blocks (from start of tag to end of string)
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
        return text.strip()

    def strip_metadata(self, text: str, runtime: str) -> str:
        """Remove CLI metadata from output"""
        # First, strip thinking tags from the raw output
        text = self.strip_thinking_tags(text)

        lines = text.split("\n")
        result = []

        if runtime == "copilot":
            in_metadata = False
            for line in lines:
                if re.match(r"^Total usage est:|^Total duration", line):
                    in_metadata = True
                    continue
                if not in_metadata:
                    result.append(line)

        elif runtime == "opencode":
            skip_banner = True
            for line in lines:
                clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line)

                # Skip banner/ASCII art
                if skip_banner and (
                    "█" in clean_line
                    or "▄" in clean_line
                    or (clean_line.strip() == "" and len(result) == 0)
                ):
                    continue
                skip_banner = False

                # Skip tool invocation lines (e.g., "|  Glob", "|  Read", "|  Write", etc.)
                if re.match(
                    r"^\|\s+(Glob|Read|Write|Bash|Edit|bash|grep|find)", clean_line
                ):
                    continue

                # Skip stats
                if any(
                    k in clean_line.lower()
                    for k in [
                        "tokens used:",
                        "total cost:",
                        "session id:",
                        "commands:",
                        "positionals:",
                        "options:",
                    ]
                ):
                    continue

                result.append(clean_line)

        elif runtime == "claude":
            for line in lines:
                result.append(line)

        elif runtime == "gemini":
            for line in lines:
                # Skip Gemini CLI metadata patterns - be very specific to avoid false positives
                line_lower = line.lower()

                # Skip lines that are clearly debug/startup output
                # Must match Gemini's specific debug format to avoid filtering user content
                if any(
                    pattern in line_lower
                    for pattern in [
                        "[startup]",  # Gemini startup profiler messages
                        "recording metric for phase:",  # Startup metrics
                        "loaded cached credentials",  # Authentication logs
                        "session:",
                        "model:",
                        "tokens:",
                        "usage:",  # Standard metadata
                    ]
                ):
                    continue
                result.append(line)

        elif runtime == "codex":
            # CODEX output format (when stripped of headers):
            # 1. First response line (often echoed/repeated)
            # 2. Header section (OpenAI Codex...)
            # 3. Metadata (workdir, model, etc.)
            # 4. "user" marker + user input/context
            # 5. File listings
            # 6. "thinking" marker + reasoning
            # 7. "codex" marker + actual response(s)
            # 8. "tokens used" metadata

            found_codex_marker = False
            response_lines = []
            skip_next_n_lines = 0

            for i, line in enumerate(lines):
                line_lower = line.lower()

                # Track if we've hit the "codex" marker - only keep content after this
                if line_lower.strip() == "codex":
                    found_codex_marker = True
                    continue

                # Stop at tokens metadata
                if "tokens" in line_lower and "used" in line_lower:
                    break

                # Before codex marker, skip everything
                if not found_codex_marker:
                    continue

                # After codex marker, skip empty lines at start
                if not line.strip() and not response_lines:
                    continue

                # Keep the actual response content
                response_lines.append(line)

            # Clean up trailing empty lines
            while response_lines and not response_lines[-1].strip():
                response_lines.pop()

            result.extend(response_lines)

        # Remove trailing empty lines
        while result and not result[-1].strip():
            result.pop()

        return "\n".join(result)

    def _execute_bash_command(self, command: str, agent: str = "orchestrator") -> str:
        """Execute a bash command directly without hitting any runtime

        Args:
            command: The bash command to execute (without the ! prefix)
            agent: The agent name whose directory to execute in

        Returns:
            The output from stdout/stderr, or an error message
        """
        if not command:
            return "Error: No command provided. Usage: !<command>"

        # Get agent directory
        agent_info = self.AGENTS.get(agent)
        if not agent_info:
            agent_dir = str(Path.cwd())
        else:
            agent_dir = agent_info["path"]

        print(f"[Shell] Executing in {agent_dir}: {command}", file=sys.stderr)

        try:
            # Execute the command with a reasonable timeout (10 seconds)
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
                cwd=agent_dir,
            )

            # Combine stdout and stderr
            output = result.stdout
            if result.stderr:
                output += result.stderr

            # If there's no output, indicate success
            if not output.strip():
                if result.returncode == 0:
                    output = f"✓ Command executed successfully (exit code: 0)"
                else:
                    output = f"✗ Command failed with exit code: {result.returncode}"

            return output.strip()

        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after 10 seconds"
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _execute_with_context(
        self, prompt: str, delegation_data: dict, n8n_session_id: str
    ) -> str:
        """Execute a prompt with specific agent context (for sub-agent delegation)"""
        session_id = delegation_data.get("session_id")
        model = delegation_data.get("model", "gpt-5-mini")
        agent = delegation_data.get("agent", "orchestrator")
        runtime = delegation_data.get("runtime", "copilot")

        # Check if we can resume (for delegation, usually no)
        can_resume = False

        output = ""
        if runtime == "copilot":
            output = self.run_copilot(prompt, model, agent, None, False, n8n_session_id)
        elif runtime == "opencode":
            output = self.run_opencode(
                prompt, model, agent, None, False, n8n_session_id
            )
        elif runtime == "claude":
            output = self.run_claude(
                prompt, model, agent, session_id, False, n8n_session_id
            )
        elif runtime == "gemini":
            output = self.run_gemini(prompt, model, agent, None, False, n8n_session_id)
        elif runtime == "codex":
            output = self.run_codex(prompt, model, agent, None, False, n8n_session_id)

        return output

    def build_agent_context_prompt(
        self,
        agent: str,
        prompt: str,
        n8n_session_id: str,
        render_type: str = "text",
        timeout: int | None = None,
    ) -> str:
        """Build a context-aware prompt that includes agent information and execution deadline"""
        if agent not in self.AGENTS:
            agent = "devops"

        agent_info = self.AGENTS[agent]
        agent_name = agent
        agent_desc = agent_info.get("description", "No description")
        agent_path = agent_info.get("path", "")

        # Try to list files in agent directory for context
        files_context = ""
        try:
            agent_path_obj = Path(agent_path)
            if agent_path_obj.exists():
                files = list(agent_path_obj.glob("*"))[:10]  # First 10 items
                if files:
                    files_list = "\n".join([f"  - {f.name}" for f in files])
                    files_context = f"\n\nAvailable resources in this agent's workspace:\n{files_list}"
        except Exception:
            pass

        # Add render type instruction to the context
        render_instruction = ""
        if render_type == "markdown":
            render_instruction = "\n[Output Format: markdown]"
        elif render_type == "html":
            render_instruction = "\n[Output Format: html]"
        elif render_type == "telegram_html":
            render_instruction = """
[Output Format: Telegram HTML - STRICT]
⚠️ CRITICAL: You MUST use ONLY these exact supported HTML tags:
1. <b>text</b> or <strong>text</strong> - bold
2. <i>text</i> or <em>text</em> - italic
3. <u>text</u> or <ins>text</ins> - underline
4. <s>text</s>, <strike>text</strike>, or <del>text</del> - strikethrough
5. <tg-spoiler>text</tg-spoiler> or <span class="tg-spoiler">text</span> - spoiler/hidden
6. <a href="URL">text</a> - hyperlinks (URL must be valid)
7. <code>text</code> - inline code/monospace
8. <pre>code block</pre> - multiline code blocks
9. <blockquote>text</blockquote> - quotes
10. <blockquote expandable>text</blockquote> - collapsible quotes
11. <tg-emoji emoji-id="ID">🎉</tg-emoji> - custom emoji

ABSOLUTELY NO OTHER TAGS ALLOWED:
❌ Do NOT use: <p>, <div>, <span> (without class="tg-spoiler"), <br>, <status>, or any custom tags
❌ Never create new tag names like <proxmox-node>, <b-Status>, <code-block>, etc.
❌ Do NOT nest unsupported tags inside supported ones

HOW TO FORMAT:
- Use \n (newline) to separate paragraphs, NOT <p> tags
- Escape these characters: < becomes &lt;, > becomes &gt;, & becomes &amp;
- Always close tags properly: <b>text</b> not <b>text<b>
- For line breaks in output, use plain \n characters]"""
        else:  # text (default)
            render_instruction = ""

        # Add timeout/deadline information with 15% buffer for overhead
        timeout_instruction = ""
        if timeout is not None:
            # Apply 15% buffer to account for subprocess overhead, I/O, etc.
            buffer_percent = 0.15
            agent_timeout = timeout * (1 - buffer_percent)
            agent_timeout_min = agent_timeout / 60
            timeout_instruction = f"\n[⏱️ EXECUTION DEADLINE: You have {agent_timeout:.0f} seconds ({agent_timeout_min:.1f} minutes) to complete this task. Plan your approach efficiently and wrap up before this deadline. If an operation might take too long, skip it or provide a summary instead.]"

        context = f"""[Session ID: {n8n_session_id}]
[Agent Context: {agent_name}]
{agent_desc}{files_context}{render_instruction}{timeout_instruction}

User Request:
{prompt}"""
        return context

    def _execute_subprocess_with_tracking(
        self,
        cmd: list,
        cwd: str,
        timeout: int,
        runtime: str,
        agent: str,
        prompt: str,
        n8n_session_id: str,
    ) -> str:
        """Execute a subprocess with PID tracking

        This method:
        1. Starts the process with Popen to get the PID
        2. Tracks the running query
        3. Waits for completion with timeout
        4. Cleans up tracking when done
        """
        try:
            # Start process and get PID
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
            )

            # Track the running query
            self.track_running_query(
                n8n_session_id, process.pid, runtime, agent, prompt
            )

            # Wait for completion with timeout
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                output = stdout + (stderr if stderr else "")

                # Update with final output snippet
                self.update_query_output(n8n_session_id, output)

                return output
            except subprocess.TimeoutExpired:
                # Process timed out, kill it and wait for termination
                process.kill()
                process.wait()  # Wait for process to actually terminate
                timeout_min = timeout / 60
                return f"Error: Command timed out (exceeded {timeout}s / {timeout_min:.1f}min)"
            finally:
                # Always clear tracking when done (success or failure)
                self.clear_running_query(n8n_session_id)

        except Exception as e:
            self.clear_running_query(n8n_session_id)
            return f"Error: Failed to execute command: {e}"

    def run_copilot(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: str | None,
        resume: bool,
        n8n_session_id: str,
        timeout: int | None = None,
        render_type: str = "text",
    ) -> str:
        """Execute Copilot CLI with full tool access

        Uses --allow-all-tools and --allow-all-paths to enable:
        - Read/write/execute permissions for all files and directories
        - All MCP tools and shell commands without approval prompts
        """
        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        context_prompt = self.build_agent_context_prompt(
            agent, prompt, n8n_session_id, render_type, effective_timeout
        )

        cmd = [
            "/usr/bin/copilot",
            "-p",
            context_prompt,
            "--allow-all-tools",
            "--allow-all-paths",
            "--no-color",
            "--silent",
            "--model",
            model,
        ]

        if resume and session_id:
            cmd.extend(["--resume", session_id])
            print(f"[Session] Resuming Copilot session: {session_id}", file=sys.stderr)
        else:
            print(f"[Session] Starting new Copilot session", file=sys.stderr)

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "copilot", agent, prompt, n8n_session_id
        )
        return self.strip_metadata(output, "copilot")

    def run_opencode(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: str | None,
        resume: bool,
        n8n_session_id: str,
        timeout: int | None = None,
        render_type: str = "text",
    ) -> str:
        """Execute OpenCode CLI with full tool access

        OpenCode uses opencode.json for permission configuration.
        By default, it should allow read/write/edit/bash execution.
        The configuration file should be set up with:
        - "edit": "allow"
        - "write": "allow"
        - "bash": "allow"
        """
        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        context_prompt = self.build_agent_context_prompt(
            agent, prompt, n8n_session_id, render_type, effective_timeout
        )

        cmd = [str(self.opencode_bin), "run", "--model", model]

        if resume and session_id:
            cmd.extend(["--session", session_id])
            print(f"[Session] Resuming OpenCode session: {session_id}", file=sys.stderr)
        else:
            print(f"[Session] Starting new OpenCode session", file=sys.stderr)

        cmd.append(context_prompt)

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "opencode", agent, prompt, n8n_session_id
        )

        # Check for session errors
        if "NotFoundError" in output or "Resource not found" in output:
            return f"NotFoundError: {output}"

        return self.strip_metadata(output, "opencode")

    def run_claude(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: str | None,
        resume: bool,
        n8n_session_id: str,
        timeout: int | None = None,
        render_type: str = "text",
    ) -> str:
        """Execute Claude CLI with full tool access

        Uses --permission-mode bypassPermissions to:
        - Auto-approve all file edits, writes, and reads
        - Execute shell commands without approval
        - Access web/network tools without prompts
        Note: This is equivalent to YOLO mode in Claude Code and dontAsk mode
        """
        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        context_prompt = self.build_agent_context_prompt(
            agent, prompt, n8n_session_id, render_type, effective_timeout
        )

        cmd = [
            "/usr/bin/claude",
            "-p",
            context_prompt,
            "--permission-mode",
            "bypassPermissions",
            "--model",
            model,
        ]

        if resume and session_id:
            cmd.extend(["--resume", session_id])
            print(f"[Session] Resuming Claude session: {session_id}", file=sys.stderr)
        elif session_id:
            # Force specific session ID for new session
            cmd.extend(["--session-id", session_id])
            print(
                f"[Session] Starting new Claude session: {session_id}", file=sys.stderr
            )
        else:
            print(f"[Session] Starting new Claude session (auto-ID)", file=sys.stderr)

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "claude", agent, prompt, n8n_session_id
        )

        if "Error: Claude command failed" in output:
            return output

        return self.strip_metadata(output, "claude")

    def run_gemini(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: str | None,
        resume: bool,
        n8n_session_id: str,
        timeout: int | None = None,
        render_type: str = "text",
    ) -> str:
        """Execute Gemini CLI with full tool access

        Gemini CLI tools (read_file, write_file, run_shell_command) are enabled by default.
        For maximum automation without prompts, use --yolo flag to auto-approve all actions.
        This enables:
        - Read/write file operations without confirmation
        - Shell command execution without approval
        - All built-in tools unrestricted access
        """
        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        context_prompt = self.build_agent_context_prompt(
            agent, prompt, n8n_session_id, render_type, effective_timeout
        )

        cmd = ["gemini", "--yolo", context_prompt]

        # Note: Gemini CLI appears to have model handling issues with specified model names
        # For now, we use the default model and do not pass --model flag
        # TODO: Investigate correct model names for --model flag with Gemini CLI

        if resume and session_id:
            cmd.extend(["--resume", session_id])
            print(f"[Session] Resuming Gemini session: {session_id}", file=sys.stderr)
        else:
            print(f"[Session] Starting new Gemini session", file=sys.stderr)

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "gemini", agent, prompt, n8n_session_id
        )

        if "Error: Gemini command failed" in output:
            return output

        return self.strip_metadata(output, "gemini")

    def run_codex(
        self,
        prompt: str,
        model: str,
        agent: str,
        session_id: str | None,
        resume: bool,
        n8n_session_id: str,
        timeout: int | None = None,
        render_type: str = "text",
    ) -> str:
        """Execute CODEX CLI with full tool access

        Uses --dangerously-bypass-approvals-and-sandbox (also known as --yolo) to:
        - Disable all approval prompts
        - Remove sandbox restrictions (full file system access)
        - Enable read/write/execute for all files and directories
        - Allow all shell commands and tools without confirmation

        This provides maximum automation but should only be used in trusted environments.
        """
        agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]
        effective_timeout = timeout if timeout is not None else self.command_timeout

        context_prompt = self.build_agent_context_prompt(
            agent, prompt, n8n_session_id, render_type, effective_timeout
        )

        if resume and session_id:
            # Resume existing session
            # Usage: codex exec resume [SESSION_ID] [PROMPT]
            # Note: resume does not support --dangerously-bypass-approvals-and-sandbox flag
            cmd = [
                "codex",
                "exec",
                "resume",
                session_id,
                context_prompt,
            ]
            print(f"[Session] Resuming CODEX session: {session_id}", file=sys.stderr)
        else:
            # Start new session with full permissions
            cmd = [
                "codex",
                "exec",
                context_prompt,
                "--dangerously-bypass-approvals-and-sandbox",
            ]
            print(f"[Session] Starting new CODEX session", file=sys.stderr)

        output = self._execute_subprocess_with_tracking(
            cmd, agent_dir, effective_timeout, "codex", agent, prompt, n8n_session_id
        )

        if "Error: CODEX command failed" in output:
            return output

        return self.strip_metadata(output, "codex")

    def session_exists(self, session_id: str, runtime: str) -> bool:
        """Check if session state exists for runtime"""
        if runtime == "copilot":
            return (self.session_state_dir / f"{session_id}.jsonl").exists()
        elif runtime == "opencode":
            # OpenCode stores sessions in nested directories: ~/.local/share/opencode/storage/session/HASH/ses_*.json
            # We need to search for the session file in any project directory
            try:
                session_dir = (
                    Path.home()
                    / ".local"
                    / "share"
                    / "opencode"
                    / "storage"
                    / "session"
                )
                if session_dir.exists():
                    for session_file in session_dir.glob(f"*/{session_id}.json"):
                        return True
            except Exception:
                pass
            return False
        elif runtime == "claude":
            path = self.claude_debug_dir / f"{session_id}.txt"
            # print(f"DEBUG: checking claude session at {path} -> {path.exists()}", file=sys.stderr)
            return path.exists()
        elif runtime == "gemini":
            return (self.gemini_session_dir / f"{session_id}.json").exists()
        elif runtime == "codex":
            # CODEX stores sessions in nested date-based directories
            # Format: ~/.codex/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDTHH-MM-SS-SESSION_ID.jsonl
            # Session ID is a UUID at the end of the filename
            try:
                for session_file in self.codex_session_dir.glob(
                    "*/*/*/rollout-*.jsonl"
                ):
                    # Extract the UUID from the filename (last 36 chars before .jsonl)
                    filename = session_file.name.replace(".jsonl", "")
                    file_session_id = (
                        filename[-36:] if len(filename) >= 36 else filename
                    )
                    if file_session_id == session_id:
                        return True
            except Exception:
                pass
            return False
        return False

    def get_most_recent_session_id(
        self, runtime: str, agent: str = "devops"
    ) -> str | None:
        """Get most recent session ID from storage or CLI"""
        try:
            if runtime == "copilot":
                files = sorted(
                    self.session_state_dir.glob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                return files[0].stem if files else None
            elif runtime == "opencode":
                # For OpenCode, we list sessions in the agent's directory
                agent_dir = self.AGENTS.get(agent, self.AGENTS["orchestrator"])["path"]

                # Use | cat to bypass pager by setting PAGER env var
                env = os.environ.copy()
                env["PAGER"] = "cat"

                cmd = [str(self.opencode_bin), "session", "list"]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, cwd=agent_dir, env=env
                )

                if result.returncode != 0:
                    return None

                lines = result.stdout.splitlines()
                # Output format:
                # Session ID ...
                # ─────── ...
                # ses_123 ...

                for line in lines:
                    if line.strip().startswith("ses_"):
                        # Found the first session ID
                        return line.split()[0]

                return None
            elif runtime == "gemini":
                files = sorted(
                    self.gemini_session_dir.glob("*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                return files[0].stem if files else None
            elif runtime == "codex":
                # CODEX stores sessions in nested date directories
                # Filenames: rollout-YYYY-MM-DDTHH-MM-SS-SESSION_ID.jsonl
                files = sorted(
                    self.codex_session_dir.glob("*/*/*/rollout-*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if files:
                    # Extract session ID from filename
                    # Format: rollout-2025-12-15T22-39-34-019b242b-476d-7f90-8bfa-4eb0c7095532.jsonl
                    # The session ID is the UUID at the end (last 36 chars before .jsonl)
                    filename = files[0].name
                    # Remove .jsonl extension and get the last 36 characters (UUID)
                    name_without_ext = filename.replace(".jsonl", "")
                    # Session ID should be the last UUID-like part
                    session_id = (
                        name_without_ext[-36:]
                        if len(name_without_ext) >= 36
                        else name_without_ext
                    )
                    return session_id
                return None
        except Exception as e:
            print(f"Error getting recent session ID: {e}", file=sys.stderr)
            return None

    def execute(self, prompt: str, n8n_session_id: str) -> str:
        """Main execution logic"""
        # Get session data first
        session_data = self.get_or_create_session_data(n8n_session_id)
        current_runtime = session_data.get("runtime", "copilot")
        current_agent = session_data.get("agent", "orchestrator")

        # Check for bash command (prompts starting with !)
        if prompt.startswith("!"):
            return self._execute_bash_command(prompt[1:].strip(), current_agent)

        # First check for explicit slash commands
        command, argument = self.parse_slash_command(prompt)

        # If not a slash command, check for implicit agent delegation
        if command is None:
            delegated_agent, cleaned_prompt = self.detect_agent_delegation(prompt)
            if delegated_agent and delegated_agent in self.AGENTS:
                # User asked for specific agent help - auto-delegate
                print(
                    f"[Auto-Delegate] Detected request for '{delegated_agent}' agent",
                    file=sys.stderr,
                )
                return self._execute_with_context(
                    cleaned_prompt,
                    {
                        "session_id": str(uuid4()),
                        "model": session_data.get("model", "gpt-5-mini"),
                        "agent": delegated_agent,
                        "runtime": current_runtime,
                        "is_delegation": True,
                    },
                    n8n_session_id,
                )

        # --- Slash Commands ---

        if command == "/help":
            return """🆘 **Available Commands**

**Orchestrator:**
   • `/capabilities` - Show what the orchestrator can help with

**Bash Commands:**
   • `!<command>` - Execute bash command directly (e.g., !pwd, !ls -la)
   • Commands run in current agent's directory with 10s timeout

**Runtime Management:**
   • `/runtime list` - Show available runtimes
   • `/runtime set <copilot|opencode|claude|gemini>` - Switch runtime
   • `/runtime current` - Show current runtime

**Model Management:**
   • `/model list` - Show available models for current runtime
   • `/model set \"<model>\"` - Switch model
   • `/model current` - Show current model

**Agent Management:**
   • `/agent list` - Show available agents
   • `/agent set \"<agent>\"` - Switch agent
   • `/agent current` - Show current agent
   • `/agent invoke \"<agent>\" \"<prompt>\"` - Delegate to sub-agent

**Session:**
   • `/session reset` - Reset current session
    • `/timeout` or `/timeout current` - Show current timeout
    • `/timeout set [seconds]` - Set timeout (30-3600 seconds / 1 hour max)
   • `/render` or `/render current` - Show current render type
   • `/render set [text|markdown|html|telegram_html]` - Set render type

**Query Management:**
   • `/status` - Check status of running query for this session
   • `/cancel` - Cancel running query for this session

**Auto-Delegation:**
You can mention an agent in your prompt and it will auto-delegate:
   • \"ask the family agent for Parker's Christmas ideas\"
   • \"have the devops agent check production status\"
   • \"this is in the projects agent, find the auth code\"

**Examples:**
   /capabilities
   !pwd
   !echo "Hello World"
   !ls -la
   /runtime set gemini
   /model set \"gpt-5.2\"
   /agent set \"family\"
   /agent invoke family \"Find Christmas ideas for Parker\"
   ask the family agent what are Parker's Christmas ideas
   have the devops agent check the server status
"""

        elif command == "/status":
            # Check if there's a running query for this session
            query_info = self.get_running_query(n8n_session_id)

            if not query_info:
                return "✓ No running query for this session"

            # Check if process is still running
            pid = query_info["pid"]
            if not self.is_process_running(pid):
                # Process finished but tracking wasn't cleaned up
                self.clear_running_query(n8n_session_id)
                return "✓ No running query for this session (last query has completed)"

            # Process is running - show status
            runtime = query_info.get("runtime", "unknown")
            agent = query_info.get("agent", "unknown")
            prompt_snippet = query_info.get("prompt", "")[:100]
            start_time = query_info.get("start_time", 0)
            elapsed = int(time.time() - start_time)
            elapsed_min = elapsed // 60
            elapsed_sec = elapsed % 60
            last_output = query_info.get("last_output", "")

            status_msg = f"""🔄 **Query Running**

**Runtime:** {runtime}
**Agent:** {agent}
**PID:** {pid}
**Elapsed Time:** {elapsed_min}m {elapsed_sec}s
**Prompt:** {prompt_snippet}...

**Recent Output:**
{last_output[-self.MAX_OUTPUT_DISPLAY :] if last_output else "(no output yet)"}
"""
            return status_msg

        elif command == "/cancel":
            # Find and cancel running query
            query_info = self.get_running_query(n8n_session_id)

            if not query_info:
                return "❌ No running query to cancel for this session"

            pid = query_info["pid"]

            # Check if process is still running
            if not self.is_process_running(pid):
                self.clear_running_query(n8n_session_id)
                return "✓ No running query to cancel (query has already completed)"

            # Kill the process
            if self.kill_process(pid):
                self.clear_running_query(n8n_session_id)
                runtime = query_info.get("runtime", "unknown")
                return f"✓ Cancelled running query (PID: {pid}, Runtime: {runtime})"
            else:
                return f"❌ Failed to cancel query (PID: {pid}). Process may have already terminated."

        elif command == "/capabilities":
            return self.get_capabilities()

        elif command == "/runtime":
            if not argument:
                return "Usage: /runtime [list|set|current]"
            if argument == "list":
                return "🤖 **Available Runtimes**\n\n• `copilot` (GitHub Copilot)\n• `opencode` (OpenCode CLI)\n• `claude` (Claude Code CLI)\n• `gemini` (Google Gemini CLI)\n• `codex` (Codex CLI)"
            elif argument == "current":
                return f"🤖 **Current Runtime:** `{current_runtime}`"
            elif argument.startswith("set "):
                new_runtime = argument[4:].strip().lower()
                if new_runtime not in [
                    "copilot",
                    "opencode",
                    "claude",
                    "gemini",
                    "codex",
                ]:
                    return f"Unknown runtime: '{new_runtime}'. Use 'copilot', 'opencode', 'claude', 'gemini', or 'codex'."
                self.update_session_field(n8n_session_id, "runtime", new_runtime)

                # When switching runtime, reset the session ID to a new UUID since session formats are incompatible
                # (e.g., OpenCode uses "ses_*" format, Claude uses UUID format, CODEX uses UUID format, etc.)
                new_session_id = str(uuid4())
                self.update_session_field(n8n_session_id, "session_id", new_session_id)

                # When switching runtime, also reset the model to a default for that runtime
                default_model = "gpt-5-mini"  # Default fallback
                if new_runtime == "copilot":
                    default_model = "gpt-5-mini"
                elif new_runtime == "opencode":
                    default_model = "opencode/gpt-5-nano"
                elif new_runtime == "claude":
                    default_model = "haiku"
                elif new_runtime == "gemini":
                    default_model = "gemini-1.5-flash"
                elif new_runtime == "codex":
                    default_model = "gpt-5.1-codex-max"

                self.update_session_field(n8n_session_id, "model", default_model)
                return f"✓ Switched runtime to **{new_runtime}**. Model set to `{default_model}`. Session reset."

        elif command == "/agent":
            if not argument:
                return "Usage: /agent [list|set|current|invoke]"
            if argument == "list":
                out = "# 🤖 Available Agents\n\n"
                for k, v in self.AGENTS.items():
                    out += f"### {k}\n{v['description']}\n\n**Location:** `{v['path']}`\n\n"
                return out
            elif argument == "current":
                ag = session_data.get("agent", "devops")
                info = self.AGENTS.get(ag, self.AGENTS["orchestrator"])
                return f"Current Agent: **{ag}**\n{info['description']}"
            elif argument.startswith("set "):
                agent = argument[4:].strip().strip("\"'")
                return self.set_agent(n8n_session_id, agent)
            elif argument.startswith("invoke "):
                # Parse: /agent invoke <agent_name> <prompt...>
                invoke_args = argument[7:].strip()  # Remove 'invoke '
                parts = invoke_args.split(None, 1)  # Split on first space
                if len(parts) < 2:
                    return "Usage: /agent invoke [agent_name] [prompt]"

                agent_name = parts[0].strip("\"'")
                sub_prompt = parts[1]

                if agent_name not in self.AGENTS:
                    available = ", ".join(self.AGENTS.keys())
                    return f"Unknown agent: '{agent_name}'. Available: {available}"

                # Invoke the sub-agent with a new session
                print(
                    f"[Agent] Invoking sub-agent '{agent_name}' with delegation",
                    file=sys.stderr,
                )
                sub_session_id = str(uuid4())

                # Save delegation context
                delegation_data = {
                    "session_id": sub_session_id,
                    "model": session_data.get("model", "gpt-5-mini"),
                    "agent": agent_name,
                    "runtime": current_runtime,
                    "is_delegation": True,
                }

                # Execute in sub-agent context
                return self._execute_with_context(
                    sub_prompt, delegation_data, n8n_session_id
                )

        elif command == "/model":
            if not argument:
                argument = "list"  # Default to list if no argument provided
            if argument == "list" or argument.startswith("list "):
                if current_runtime == "opencode":
                    models_by_provider = self.fetch_opencode_models()
                    out = f"📋 **Available Models ({current_runtime})**\n\n"
                    if not models_by_provider:
                        return (
                            out
                            + "❌ No models available. Check that OpenCode is properly configured."
                        )
                    for provider in sorted(models_by_provider.keys()):
                        out += f"**{provider}:**\n"
                        for model_id in sorted(models_by_provider[provider]):
                            out += f"  • `{model_id}`\n"
                    return out
                elif current_runtime == "claude":
                    out = f"📋 **Available Models ({current_runtime})**\n\n"
                    for cat, models in self.CLAUDE_MODELS.items():
                        out += f"**{cat}:**\n"
                        for mid, desc, _ in models:
                            out += f"  • `{mid}` - {desc}\n"
                    return out
                elif current_runtime == "gemini":
                    out = f"📋 **Available Models ({current_runtime})**\n\n"
                    for cat, models in self.GEMINI_MODELS.items():
                        out += f"**{cat}:**\n"
                        for mid, desc, _ in models:
                            out += f"  • `{mid}` - {desc}\n"
                    return out
                elif current_runtime == "codex":
                    out = f"📋 **Available Models ({current_runtime})**\n\n"
                    for cat, models in self.CODEX_MODELS.items():
                        out += f"**{cat}:**\n"
                        for mid, desc, _ in models:
                            out += f"  • `{mid}` - {desc}\n"
                    return out
                else:
                    models_dict = self.fetch_copilot_models()
                    out = f"📋 **Available Models ({current_runtime})**\n\n"
                    if not models_dict:
                        return (
                            out
                            + "❌ No models available. Check that Copilot CLI is properly configured."
                        )
                    for cat in sorted(models_dict.keys()):
                        out += f"**{cat}:**\n"
                        for mid in sorted(models_dict[cat]):
                            out += f"  • `{mid}`\n"
                    return out

            elif argument == "current":
                return (
                    f"Current Model: `{session_data.get('model')}` ({current_runtime})"
                )
            elif argument.startswith("set "):
                model_name = argument[4:].strip()
                model_id = self.get_model_from_name(model_name, current_runtime)
                if not model_id:
                    return f"Unknown model '{model_name}' for runtime {current_runtime}"
                self.update_session_field(n8n_session_id, "model", model_id)
                return f"✓ Switched to model `{model_id}`"

        elif command == "/session":
            if argument == "reset":
                # Remove session from map (or clear session_id)
                # Actually, simpler to just delete the entry and let next call create new
                session_map = self.load_session_map()
                if n8n_session_id in session_map:
                    del session_map[n8n_session_id]
                    self.save_session_map(session_map)
                return "✓ Session reset. Next message starts fresh."

        elif command == "/timeout":
            if not argument:
                argument = "current"  # Default to showing current timeout

            if argument == "current":
                # Get timeout from session, or show default
                session_timeout = session_data.get("timeout")
                if session_timeout:
                    return f"⏱️ **Current Timeout:** `{session_timeout}` seconds"
                else:
                    return f"⏱️ **Current Timeout:** `{self.command_timeout}` seconds (default)"

            elif argument.startswith("set "):
                timeout_str = argument[4:].strip()
                try:
                    timeout_seconds = int(timeout_str)
                    # Validate timeout (minimum 30 seconds, maximum 3600 seconds / 1 hour)
                    if timeout_seconds < 30:
                        return f"❌ Timeout must be at least 30 seconds. You specified: {timeout_seconds}s"
                    if timeout_seconds > 3600:
                        return f"❌ Timeout must not exceed 3600 seconds (1 hour). You specified: {timeout_seconds}s"

                    # Store timeout in session
                    self.update_session_field(
                        n8n_session_id, "timeout", str(timeout_seconds)
                    )
                    return (
                        f"✓ Timeout set to `{timeout_seconds}` seconds for this session"
                    )
                except ValueError:
                    return f"❌ Invalid timeout value '{timeout_str}'. Please provide a number (30-600 seconds)"
            else:
                return "Usage: `/timeout` or `/timeout current` to show current timeout\n       `/timeout set [seconds]` to set a new timeout (30-3600 seconds)"

        elif command == "/render":
            if not argument:
                argument = "current"  # Default to showing current render type

            if argument == "current":
                # Get render type from session, or show default
                render_type = session_data.get("render_type", "text")
                return f"🎨 **Current Render Type:** `{render_type}`"

            elif argument.startswith("set "):
                render_type = argument[4:].strip().lower()
                valid_types = ["text", "markdown", "html", "telegram_html"]
                if render_type not in valid_types:
                    return f"❌ Invalid render type '{render_type}'. Valid options: {', '.join(valid_types)}"

                # Store render type in session
                self.update_session_field(n8n_session_id, "render_type", render_type)
                return f"✓ Render type set to `{render_type}` for this session"
            else:
                return "Usage: `/render` or `/render current` to show current render type\n       `/render set [text|markdown|html|telegram_html]` to set render type"

        # --- Execution ---

        # Prepare for execution
        session_id = session_data.get("session_id")
        model = session_data.get("model", "gpt-5-mini")
        agent = session_data.get("agent", "orchestrator")
        effective_timeout = self.get_effective_timeout(session_data)
        render_type = self.get_render_type(session_data)

        # Check if we can resume
        can_resume = (
            self.session_exists(session_id, current_runtime) if session_id else False
        )

        output = ""
        if current_runtime == "copilot":
            if can_resume:
                output = self.run_copilot(
                    prompt,
                    model,
                    agent,
                    session_id,
                    True,
                    n8n_session_id,
                    effective_timeout,
                    render_type,
                )
            else:
                output = self.run_copilot(
                    prompt,
                    model,
                    agent,
                    None,
                    False,
                    n8n_session_id,
                    effective_timeout,
                    render_type,
                )
                # Copilot auto-generates session ID, we need to find it and map it
                # Logic: Copilot writes to session-state dir. We find newest file.
                new_id = self.get_most_recent_session_id("copilot", agent)
                if new_id:
                    self.update_session_field(n8n_session_id, "session_id", new_id)

        elif current_runtime == "opencode":
            if can_resume:
                output = self.run_opencode(
                    prompt,
                    model,
                    agent,
                    session_id,
                    True,
                    n8n_session_id,
                    effective_timeout,
                    render_type,
                )
                # Check for session loss / resource not found
                if "Resource not found" in output or "NotFoundError" in output:
                    print(
                        f"[Session] Session {session_id} lost/corrupted. Starting new session.",
                        file=sys.stderr,
                    )
                    output = self.run_opencode(
                        prompt,
                        model,
                        agent,
                        None,
                        False,
                        n8n_session_id,
                        effective_timeout,
                        render_type,
                    )
                    new_id = self.get_most_recent_session_id("opencode", agent)
                    if new_id:
                        self.update_session_field(n8n_session_id, "session_id", new_id)
            else:
                output = self.run_opencode(
                    prompt,
                    model,
                    agent,
                    None,
                    False,
                    n8n_session_id,
                    effective_timeout,
                    render_type,
                )
                new_id = self.get_most_recent_session_id("opencode", agent)
                if new_id:
                    self.update_session_field(n8n_session_id, "session_id", new_id)

        elif current_runtime == "claude":
            if can_resume:
                output = self.run_claude(
                    prompt,
                    model,
                    agent,
                    session_id,
                    True,
                    n8n_session_id,
                    effective_timeout,
                    render_type,
                )
            else:
                output = self.run_claude(
                    prompt,
                    model,
                    agent,
                    session_id,
                    False,
                    n8n_session_id,
                    effective_timeout,
                    render_type,
                )

        elif current_runtime == "gemini":
            if can_resume:
                output = self.run_gemini(
                    prompt,
                    model,
                    agent,
                    session_id,
                    True,
                    n8n_session_id,
                    effective_timeout,
                    render_type,
                )
            else:
                output = self.run_gemini(
                    prompt,
                    model,
                    agent,
                    None,
                    False,
                    n8n_session_id,
                    effective_timeout,
                    render_type,
                )
                # Gemini auto-generates session IDs, we need to find and map it
                new_id = self.get_most_recent_session_id("gemini", agent)
                if new_id:
                    self.update_session_field(n8n_session_id, "session_id", new_id)

        elif current_runtime == "codex":
            if can_resume:
                output = self.run_codex(
                    prompt,
                    model,
                    agent,
                    session_id,
                    True,
                    n8n_session_id,
                    effective_timeout,
                    render_type,
                )
            else:
                output = self.run_codex(
                    prompt,
                    model,
                    agent,
                    None,
                    False,
                    n8n_session_id,
                    effective_timeout,
                    render_type,
                )
                # CODEX auto-generates session IDs, we need to find and map it
                new_id = self.get_most_recent_session_id("codex", agent)
                if new_id:
                    self.update_session_field(n8n_session_id, "session_id", new_id)

        # Post-process output for telegram_html to ensure Telegram compatibility
        if render_type == "telegram_html":
            # Sanitize output to remove unsupported tags
            output = self.sanitize_telegram_html(output)

        return output


def _check_command_result(result: str, error_keywords: list[str]) -> None:
    """Helper function to check command results and exit on error

    Args:
        result: The output from executing a command
        error_keywords: List of keywords that indicate an error occurred

    Raises:
        SystemExit: If any error keywords are found in the result
    """
    for keyword in error_keywords:
        if keyword in result:
            print(result, file=sys.stderr)
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="AI Session Wrapper for N8N Integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Execute a prompt with default settings
  %(prog)s "What is the status of the cluster?"
  
  # Set agent via CLI
  %(prog)s --agent devops "Check server status"
  
  # Set model and runtime via CLI
  %(prog)s --runtime gemini --model gemini-1.5-pro "Analyze this code"
  
  # Use custom configuration file
  %(prog)s --config my-agents.json "What can you do?"
  
  # List available agents
  %(prog)s --list-agents
  
  # List available agents with custom config
  %(prog)s --list-agents --config my-agents.json
  
  # List available models for current runtime
  %(prog)s --list-models
  
  # List available runtimes
  %(prog)s --list-runtimes
  
  # Combine multiple options
  %(prog)s --agent family --runtime claude --model sonnet "Find recipes"
  
  # Backwards compatible: positional arguments
  %(prog)s "What's the weather?" my_session my-config.json
""",
    )

    # Positional arguments (for backwards compatibility)
    parser.add_argument(
        "prompt",
        nargs="?",
        help="The prompt to execute (required unless using --list-* options)",
    )
    parser.add_argument(
        "session_id",
        nargs="?",
        default="default",
        help="N8N session ID (default: 'default')",
    )

    # Configuration file - can be positional or named
    parser.add_argument(
        "config_file_positional",
        nargs="?",
        help=argparse.SUPPRESS,  # Hide from help as we have --config below
    )
    parser.add_argument(
        "-c",
        "--config",
        dest="config_file",
        help="Path to agents.json configuration file",
    )

    # Agent options
    agent_group = parser.add_argument_group("agent options")
    agent_group.add_argument(
        "--agent",
        metavar="NAME",
        help="Set the agent to use (e.g., devops, family, projects)",
    )
    agent_group.add_argument(
        "--list-agents", action="store_true", help="List all available agents and exit"
    )

    # Model options
    model_group = parser.add_argument_group("model options")
    model_group.add_argument(
        "--model", metavar="NAME", help="Set the model to use (e.g., gpt-5, sonnet)"
    )
    model_group.add_argument(
        "--list-models",
        action="store_true",
        help="List all available models for current runtime and exit",
    )

    # Runtime options
    runtime_group = parser.add_argument_group("runtime options")
    runtime_group.add_argument(
        "--runtime",
        metavar="NAME",
        choices=["copilot", "opencode", "claude", "gemini", "codex"],
        help="Set the runtime to use (choices: copilot, opencode, claude, gemini, codex)",
    )
    runtime_group.add_argument(
        "--list-runtimes",
        action="store_true",
        help="List all available runtimes and exit",
    )

    args = parser.parse_args()

    # Handle backwards compatibility: if config_file_positional is provided, use it
    if args.config_file_positional and not args.config_file:
        args.config_file = args.config_file_positional

    # Initialize manager
    manager = SessionManager(args.config_file)

    # Apply runtime setting first if provided (so list commands use the correct runtime)
    if args.runtime:
        result = manager.execute(f"/runtime set {args.runtime}", args.session_id)
        _check_command_result(result, ["Unknown runtime", "Error"])

    # Apply agent setting if provided (so list commands use the correct agent context)
    if args.agent:
        result = manager.execute(f'/agent set "{args.agent}"', args.session_id)
        _check_command_result(result, ["Unknown agent", "Error"])

    # Handle list commands (these don't require a prompt but may use runtime/agent settings)
    if args.list_agents:
        output = manager.execute("/agent list", args.session_id)
        print(output)
        sys.exit(0)

    if args.list_models:
        output = manager.execute("/model list", args.session_id)
        print(output)
        sys.exit(0)

    if args.list_runtimes:
        output = manager.execute("/runtime list", args.session_id)
        print(output)
        sys.exit(0)

    # If no prompt provided and no list command, show error
    if not args.prompt:
        parser.error("prompt is required unless using --list-* options")

    # Apply model setting if provided (after list commands since we don't need it for lists)
    if args.model:
        result = manager.execute(f'/model set "{args.model}"', args.session_id)
        _check_command_result(result, ["Unknown model", "Error"])

    # Execute the main prompt
    output = manager.execute(args.prompt, args.session_id)
    print(output)


if __name__ == "__main__":
    main()
