"""Session list component"""

from typing import Any, Dict, List

from rich.panel import Panel
from rich.table import Table
from textual.reactive import reactive
from textual.widgets import Static


class SessionListPanel(Static):
    """Scrollable session list panel"""

    sessions: reactive[list] = reactive([])

    def render(self):
        """Render session list"""
        table = Table(show_header=True, header_style="bold", expand=True)
        table.add_column("ID", style="cyan", width=10, no_wrap=True)
        table.add_column("Agent", style="green", width=12)
        table.add_column("Status", style="yellow")

        if not self.sessions:
            table.add_row("—", "No sessions", "")
        else:
            for session in self.sessions[-20:]:
                sid = session.get("session_id", session.get("id", ""))
                short_id = sid[:8] if sid else "—"
                agent = session.get("agent", "—")
                status = session.get("status", "unknown")
                status_icon = (
                    "[green]●[/green]" if status in ("active", "running")
                    else "[yellow]○[/yellow]" if status == "idle"
                    else "[red]✗[/red]"
                )
                table.add_row(short_id, agent, f"{status_icon} {status}")

        return Panel(table, title="[bold]Sessions[/bold]", expand=False, width=30)

    async def update_sessions(self, sessions: List[Dict[str, Any]]) -> None:
        """Update the displayed session list.

        Args:
            sessions: List of session dicts from the API
        """
        self.sessions = sessions
