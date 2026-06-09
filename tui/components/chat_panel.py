"""Chat panel component"""

from typing import Any, Dict, List

from rich.align import Align
from rich.rule import Rule
from rich.text import Text
from textual.widgets import RichLog

PLACEHOLDER = "[dim]No messages yet — type a prompt below and press Ctrl+S[/dim]"

STATUS_STYLES = {
    "queued": "#EBCB8B",
    "running": "#88C0D0",
    "completed": "#A3BE8C",
    "failed": "#BF616A",
}


class ChatPanel(RichLog):
    """Scrollable chat message display panel"""

    DEFAULT_CSS = """
    ChatPanel {
        height: 1fr;
        border: solid $primary;
        overflow-y: scroll;
    }
    """

    ROLE_STYLES = {
        "user": "bold cyan",
        "assistant": "bold #A3BE8C",
        "system": "bold #EBCB8B",
        "error": "bold #BF616A",
    }

    def on_mount(self) -> None:
        """Show placeholder on first mount"""
        self.write(Align.center(Text.from_markup(PLACEHOLDER), vertical="middle"))

    async def add_message(self, role: str, content: str) -> None:
        """Add a message to the chat panel."""
        if self.line_count == 1:
            # Clear placeholder on first real message
            self.clear()
        style = self.ROLE_STYLES.get(role, "white")
        prefix = Text(f"[{role.upper()}] ", style=style)
        message = Text.assemble(prefix, content)
        self.write(message)

    async def load_transcript(self, messages: List[Dict[str, Any]]) -> None:
        """Clear and populate the panel with a historical transcript."""
        self.clear()
        if not messages:
            self.write(Align.center(Text.from_markup("[dim]No messages in this session[/dim]"), vertical="middle"))
            return
        for msg in messages:
            role = msg.get("role", "system")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Handle content blocks (e.g. Claude API format)
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        parts.append(block)
                content = "\n".join(parts)
            style = self.ROLE_STYLES.get(role, "white")
            prefix = Text(f"[{role.upper()}] ", style=style)
            self.write(Text.assemble(prefix, str(content)))
        self.scroll_end(animate=False)

    async def show_task_inspector(self, task: Dict[str, Any]) -> None:
        """Render task metadata + output transcript for the inspector view."""
        self.clear()

        status = task.get("status", "unknown")
        status_style = STATUS_STYLES.get(status, "white")
        task_id = task.get("task_id", "unknown")

        # Header
        self.write(Rule(f"[bold]Task Inspector[/bold]", style="#88C0D0"))
        self.write(Text.assemble(
            Text("  Task ID:  ", style="bold white"),
            Text(task_id, style="cyan"),
        ))
        self.write(Text.assemble(
            Text("  Agent:    ", style="bold white"),
            Text(task.get("agent", "—"), style="green"),
        ))
        self.write(Text.assemble(
            Text("  Runtime:  ", style="bold white"),
            Text(task.get("runtime", "—"), style="magenta"),
        ))
        self.write(Text.assemble(
            Text("  Model:    ", style="bold white"),
            Text(task.get("model", "—"), style="#B48EAD"),
        ))
        self.write(Text.assemble(
            Text("  Status:   ", style="bold white"),
            Text(status, style=f"bold {status_style}"),
        ))

        # Timestamps
        for label, key in [("Created", "created_at"), ("Started", "started_at"), ("Completed", "completed_at")]:
            val = task.get(key)
            if val:
                self.write(Text.assemble(
                    Text(f"  {label}:".ljust(12), style="bold white"),
                    Text(str(val), style="dim"),
                ))

        self.write(Rule(style="#4C566A"))

        # Output transcript
        output = task.get("output") or task.get("result") or task.get("transcript") or ""
        if output:
            self.write(Text("Output:", style="bold #EBCB8B"))
            self.write(str(output))
        elif status in ("queued",):
            self.write(Text.from_markup("[dim]Task is queued — no output yet[/dim]"))
        elif status == "running":
            self.write(Text.from_markup("[dim #88C0D0]Task is running — output will appear here...[/dim #88C0D0]"))
        else:
            self.write(Text.from_markup("[dim]No output recorded[/dim]"))

        if status == "running":
            self.write(Rule(style="#4C566A"))
            self.write(Text.from_markup("[dim]Auto-refreshing every 2.5 s — press [bold]Escape[/bold] to exit[/dim]"))

        self.scroll_end(animate=False)
