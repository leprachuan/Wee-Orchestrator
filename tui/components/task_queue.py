"""Task queue component"""
from textual.widgets import Static
from textual.reactive import reactive
from rich.panel import Panel
from rich.table import Table


class TaskQueuePanel(Static):
    """Panel for background tasks queue"""
    
    tasks: reactive[list] = reactive([])
    
    def render(self):
        """Render task queue"""
        table = Table(title="Background Tasks", show_header=True, header_style="bold")
        table.add_column("ID", style="cyan", width=12)
        table.add_column("Agent", style="green")
        table.add_column("Status", style="yellow")
        table.add_column("Progress", style="white")
        
        if not self.tasks:
            table.add_row("No tasks", "", "", "")
        else:
            for task in self.tasks[-10:]:  # Show last 10 tasks
                progress_bar = "█" * int(task.get("progress", 0) // 10) + "░" * (10 - int(task.get("progress", 0) // 10))
                table.add_row(
                    task.get("id", "")[:12],
                    task.get("agent", ""),
                    task.get("status", "queued"),
                    f"{progress_bar} {int(task.get('progress', 0))}%",
                )
        
        return Panel(table, title="[bold]Tasks[/bold]", expand=False, height=10)
