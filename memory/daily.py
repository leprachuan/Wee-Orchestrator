"""memory.daily -- Daily notes management for Wee Orchestrator.

Provides append_daily_note() for writing timestamped entries to an
agent's daily notes file.
"""

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from memory.inject import resolve_memory_dir


def append_daily_note(
    content: str,
    agent_path: Optional[str] = None,
) -> Path:
    """Append a timestamped entry to today's daily notes file.

    Creates the daily directory and file if they don't exist.
    Returns the path to the daily notes file.
    """
    memory_dir = resolve_memory_dir(agent_path)
    daily_dir = memory_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    today_file = daily_dir / f"{date.today().isoformat()}.md"
    timestamp = datetime.now().strftime("%H:%M")

    entry = f"\n## {timestamp} \u2014 Daily Note\n\n{content.strip()}\n"

    with open(today_file, "a", encoding="utf-8") as f:
        f.write(entry)

    return today_file
