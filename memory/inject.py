"""memory.inject -- Memory context injection for Wee Orchestrator.

Provides per-agent memory resolution and context building for session-start
injection.  Called once per session from build_agent_context_prompt(); the
memory_injected flag on the session prevents double injection.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

MAX_MEMORY_CHARS = 4000
SEPARATOR = "\u2500" * 60

COMPACTION_SIGNALS = (
    "I don't have context about",
    "As a new session",
    "I don't have access to previous",
    "I wasn't given context",
)


def resolve_memory_dir(agent_path: Optional[str] = None) -> Path:
    """Resolve memory root for an agent.

    Resolution chain (first match wins):
        1. WEE_MEMORY_DIR env var  -- explicit override
        2. WEE_AGENT_DIR  env var  -- ``{dir}/memories``
        3. *agent_path* parameter  -- ``{path}/memories`` (from agents.json)
        4. ``/opt/memories``       -- fallback (orchestrator default)
    """
    explicit = os.environ.get("WEE_MEMORY_DIR", "")
    if explicit:
        return Path(explicit)
    agent_dir = os.environ.get("WEE_AGENT_DIR", "")
    if agent_dir:
        return Path(agent_dir) / "memories"
    if agent_path:
        return Path(agent_path) / "memories"
    return Path("/opt/memories")


def _read_file(path: Path) -> Optional[str]:
    """Return file contents stripped, or None if missing/empty."""
    try:
        content = path.read_text(encoding="utf-8").strip()
        return content if content else None
    except FileNotFoundError:
        return None
    except Exception as exc:
        print(
            f"[memory.inject] WARNING: could not read {path}: {exc}",
            file=sys.stderr,
        )
        return None


def build_context(agent_path: Optional[str] = None) -> str:
    """Build the memory context block for session-start injection.

    Reads MEMORY.md (long-term) plus today/yesterday daily notes.
    Returns empty string if no memory files exist.

    The returned string contains only the memory sections — no
    ``[MEMORY CONTEXT]`` wrapper.  The caller (build_agent_context_prompt)
    is responsible for placement inside the prompt.
    """
    memory_dir = resolve_memory_dir(agent_path)
    memory_md = memory_dir / "MEMORY.md"
    daily_dir = memory_dir / "daily"

    sections: list[str] = []

    core = _read_file(memory_md)
    if core:
        sections.append(f"## LONG-TERM MEMORY\n\n{core}")

    today = date.today()
    today_notes = _read_file(daily_dir / f"{today.isoformat()}.md")
    if today_notes:
        sections.append(f"## TODAY'S NOTES ({today.isoformat()})\n\n{today_notes}")

    yesterday = today - timedelta(days=1)
    yesterday_notes = _read_file(daily_dir / f"{yesterday.isoformat()}.md")
    if yesterday_notes:
        sections.append(
            f"## YESTERDAY'S NOTES ({yesterday.isoformat()})\n\n{yesterday_notes}"
        )

    if not sections:
        return ""

    return f"\n\n{SEPARATOR}\n\n".join(sections)


def get_memory_context(agent_path: Optional[str] = None) -> str:
    """Return memory context string, truncated to MAX_MEMORY_CHARS.

    Falls back to MEMORY.md only if the full context exceeds the limit.
    Returns empty string on any failure.
    """
    try:
        ctx = build_context(agent_path)
        if not ctx:
            return ""
        if len(ctx) <= MAX_MEMORY_CHARS:
            return ctx
        # Truncate: try MEMORY.md only
        memory_dir = resolve_memory_dir(agent_path)
        core = _read_file(memory_dir / "MEMORY.md")
        if core:
            return core[:MAX_MEMORY_CHARS]
        return ""
    except Exception:
        return ""


def build_executor_context(session_id: str = "", mode: str = "interactive") -> str:
    """Build Wee Executor usage guide for context injection.

    Returns a markdown section agents can reference to use wee_executor.py.
    """
    import subprocess as _sp
    script = Path(__file__).resolve().parent.parent / "scripts" / "wee_executor.py"
    if not script.exists():
        return ""

    # Dynamically fetch available capabilities
    caps_text = ""
    try:
        result = _sp.run(
            ["python3", str(script), "--list-capabilities", "--json"],
            capture_output=True, text=True, timeout=5,
            env={**__import__("os").environ, "WEE_SESSION_ID": session_id or "ctx"}
        )
        if result.returncode == 0:
            import json as _json
            caps = _json.loads(result.stdout)
            for cap in caps:
                req = ", ".join(cap.get("required_args", []))
                opt = ", ".join(cap.get("optional_args", []))
                caps_text += f"  - **{cap['name']}**: {cap['description']}\n"
                caps_text += f"    Required: {req}  Optional: {opt}\n"
    except Exception:
        caps_text = "  - create_background_task: Create background tasks\n"

    return (
        "## Wee Executor\n"
        "\n"
        "Use `wee_executor.py` to safely invoke privileged operations. "
        "Bearer tokens are handled internally — never exposed to agents.\n"
        "\n"
        f"**Current mode:** {mode}\n"
        "\n"
        "**Available capabilities:**\n"
        f"{caps_text}"
        "\n"
        "**Usage:**\n"
        "```bash\n"
        "python3 /opt/n8n-copilot-shim-dev/scripts/wee_executor.py "
        "--capability create_background_task "
        "--args '{\"agent\": \"research\", \"prompt\": \"look up X\"}'\n"
        "```\n"
        "\n"
        "**Security:** Bearer token is hidden. WEE_SESSION_ID is set automatically.\n"
    )

def detect_compaction(last_response: str) -> bool:
    """Return True if the response suggests context compaction occurred."""
    if not last_response:
        return False
    lower = last_response.lower()
    return any(signal.lower() in lower for signal in COMPACTION_SIGNALS)
