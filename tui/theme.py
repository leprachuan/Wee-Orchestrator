"""TUI Theme and styling"""

from rich.style import Style

# Agent color mapping (matching WebUI)
AGENT_COLORS = {
    "orchestrator": "green",
    "wee-dev": "blue",
    "email-triage": "magenta",
    "family-knowledge": "red",
    "research": "white",
    "devops": "bright_blue",
    "smarthome": "bright_cyan",
}

# Status colors
STATUS_COLORS = {
    "running": "green",
    "idle": "yellow",
    "completed": "blue",
    "error": "red",
    "queued": "white",
    "stopped": "red",
}

# Styles
STYLES = {
    "header": Style(bold=True, color="bright_white", bgcolor="blue"),
    "status_bar": Style(color="white", bgcolor="dark_slate_gray3"),
    "session_active": Style(color="green", bold=True),
    "session_idle": Style(color="yellow"),
    "input_focus": Style(bgcolor="dark_slate_gray1", color="bright_white"),
    "error": Style(color="red", bold=True),
    "success": Style(color="green", bold=True),
}


def get_agent_color(agent_name: str) -> str:
    """Get color for an agent"""
    return AGENT_COLORS.get(agent_name, "white")


def get_status_color(status: str) -> str:
    """Get color for a status"""
    return STATUS_COLORS.get(status, "white")
