"""TUI Theme and styling — Nord-inspired dark palette"""

from rich.style import Style

# Nord palette
NORD = {
    "accent":        "#88C0D0",  # ice blue — focus/highlights
    "success":       "#A3BE8C",  # forest green — running/active
    "warn":          "#EBCB8B",  # sandy yellow — pending/idle
    "error":         "#BF616A",  # muted red — failed/error
    "border":        "#4C566A",  # muted gray-blue — panel borders
    "bg_subtle":     "#3B4252",  # panel backgrounds
    "text_muted":    "#D8DEE9",  # light gray
}

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

# Status colors — Nord palette
STATUS_COLORS = {
    "running":   NORD["success"],
    "active":    NORD["success"],
    "idle":      NORD["warn"],
    "queued":    NORD["warn"],
    "completed": NORD["accent"],
    "error":     NORD["error"],
    "failed":    NORD["error"],
    "stopped":   NORD["error"],
}

# Styles
STYLES = {
    "header":         Style(bold=True, color="bright_white", bgcolor="blue"),
    "status_bar":     Style(color=NORD["text_muted"], bgcolor=NORD["bg_subtle"]),
    "session_active": Style(color=NORD["success"], bold=True),
    "session_idle":   Style(color=NORD["warn"]),
    "input_focus":    Style(bgcolor=NORD["bg_subtle"], color="bright_white"),
    "error":          Style(color=NORD["error"], bold=True),
    "success":        Style(color=NORD["success"], bold=True),
}


def get_agent_color(agent_name: str) -> str:
    """Get color for an agent"""
    return AGENT_COLORS.get(agent_name, "white")


def get_status_color(status: str) -> str:
    """Get color for a status"""
    return STATUS_COLORS.get(status, "white")
