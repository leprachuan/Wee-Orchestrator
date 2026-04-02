"""Native per-agent memory system for Wee Orchestrator.

Each agent gets isolated memory under ``{agent_path}/memories/``.
Resolution chain (first match wins):
    1. WEE_MEMORY_DIR env var  — explicit override
    2. WEE_AGENT_DIR  env var  — ``{WEE_AGENT_DIR}/memories``
    3. agent_path parameter    — ``{agent_path}/memories``  (from agents.json)
    4. /opt/memories           — fallback (orchestrator default)
"""

from memory.daily import append_daily_note
from memory.inject import (
    build_context,
    detect_compaction,
    get_memory_context,
    prepend_memory,
    resolve_memory_dir,
)

__all__ = [
    "resolve_memory_dir",
    "build_context",
    "get_memory_context",
    "prepend_memory",
    "detect_compaction",
    "append_daily_note",
]
