"""memory_context.py — Session-start memory injection for Wee Orchestrator.

Wraps /opt/foster-skills/flat-memory/memory_inject.py to produce a memory
context block (MEMORY.md + today/yesterday daily notes) that is prepended
to background task prompts on new sessions.
"""
import subprocess
from pathlib import Path

MEMORY_INJECT_SCRIPT = Path("/opt/foster-skills/flat-memory/memory_inject.py")
MAX_MEMORY_CHARS = 4000  # guard against oversized injection

# Phrases that indicate the model has lost its context (compaction occurred)
COMPACTION_SIGNALS = (
    "I don't have context about",
    "As a new session",
    "I don't have access to previous",
)


def get_memory_context() -> str:
    """Return memory context string, or empty string on any failure."""
    if not MEMORY_INJECT_SCRIPT.exists():
        return ""
    try:
        result = subprocess.run(
            ["python3", str(MEMORY_INJECT_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        ctx = result.stdout.strip()
        if len(ctx) > MAX_MEMORY_CHARS:
            # Fallback: MEMORY.md only (skip daily notes)
            core_path = Path("/opt/memories/MEMORY.md")
            if not core_path.exists():
                return ""
            core = core_path.read_text(encoding="utf-8").strip()
            return f"[MEMORY CONTEXT]\n{core}\n[END MEMORY CONTEXT]" if core else ""
        return ctx
    except Exception:
        return ""


def prepend_memory(prompt: str, memory_context: str) -> str:
    """Prepend memory context to prompt. Returns prompt unchanged if context is empty."""
    if not memory_context:
        return prompt
    return f"{memory_context}\n\n{prompt}"


def detect_compaction(last_response: str) -> bool:
    """Return True if the last response suggests context compaction occurred."""
    if not last_response:
        return False
    lower = last_response.lower()
    return any(signal.lower() in lower for signal in COMPACTION_SIGNALS)
