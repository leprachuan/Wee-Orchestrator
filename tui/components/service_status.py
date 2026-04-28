"""Service status component"""

from rich.panel import Panel
from rich.table import Table
from textual.reactive import reactive
from textual.widgets import Static


class ServiceStatusPanel(Static):
    """Panel for service status display"""

    services: reactive[dict] = reactive({})

    def render(self):
        """Render service status"""
        table = Table(title="Services", show_header=True, header_style="bold")
        table.add_column("Name", style="cyan")
        table.add_column("Status", style="yellow")
        table.add_column("Uptime", style="green")

        if not self.services:
            table.add_row("No services", "", "")
        else:
            for name, status_info in self.services.items():
                status = status_info.get("status", "unknown")
                status_color = (
                    "[green]✓[/green]" if status == "running" else "[red]✗[/red]"
                )
                uptime = status_info.get("uptime_seconds", 0)
                uptime_str = self._format_uptime(uptime) if uptime else "N/A"
                table.add_row(name, f"{status_color} {status}", uptime_str)

        return Panel(table, title="[bold]Services[/bold]", expand=False, height=8)

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """Format uptime in human-readable form"""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m"
        elif seconds < 86400:
            return f"{int(seconds // 3600)}h"
        else:
            return f"{int(seconds // 86400)}d"
