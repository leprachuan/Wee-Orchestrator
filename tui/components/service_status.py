"""Service status component"""

from rich.table import Table
from textual.reactive import reactive
from textual.widgets import Static

STATUS_ICONS = {
    "active":   ("[bold #A3BE8C]●[/bold #A3BE8C]", "active"),
    "running":  ("[bold #A3BE8C]●[/bold #A3BE8C]", "running"),
    "inactive": ("[bold #EBCB8B]○[/bold #EBCB8B]", "inactive"),
    "failed":   ("[bold #BF616A]✗[/bold #BF616A]", "failed"),
}


class ServiceStatusPanel(Static):
    """Panel for service status display"""

    services: reactive[dict] = reactive({})

    def on_mount(self) -> None:
        self.border_title = "Services"

    def render(self):
        """Render service status"""
        table = Table(show_header=True, header_style="bold", expand=True)
        table.add_column("Name", style="cyan")
        table.add_column("Status", style="yellow")
        table.add_column("Uptime", style="green")

        if not self.services:
            table.add_row("No services", "", "")
        else:
            for name, status_info in self.services.items():
                status = status_info.get("status", "unknown")
                icon, label = STATUS_ICONS.get(
                    status,
                    ("[bold #4C566A]○[/bold #4C566A]", status),
                )
                uptime = status_info.get("uptime_seconds", 0)
                uptime_str = self._format_uptime(uptime) if uptime else "N/A"
                table.add_row(name, f"{icon} {label}", uptime_str)

        return table

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

    async def update_status(self, services) -> None:
        """Update the displayed service statuses."""
        if isinstance(services, list):
            self.services = {s.get("name", str(i)): s for i, s in enumerate(services)}
        elif isinstance(services, dict):
            self.services = services
