"""Task queue component"""

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

# Progress bar drawn with block characters; 12 chars wide
_BAR_WIDTH = 12


def _progress_bar(pct: float) -> Text:
    """Return a styled progress bar Text for a 0-100 percentage."""
    filled = int(pct / 100 * _BAR_WIDTH)
    empty = _BAR_WIDTH - filled
    bar = Text()
    bar.append("█" * filled, style="#A3BE8C")   # Nord green for filled
    bar.append("░" * empty, style="#4C566A")     # Nord muted for empty
    bar.append(f" {int(pct)}%", style="white")
    return bar


class TaskQueuePanel(Static):
    """Panel for background tasks queue"""

    tasks: reactive[list] = reactive([])

    def render(self):
        """Render task queue"""
        table = Table(title="Background Tasks", show_header=True, header_style="bold")
        table.add_column("ID", style="cyan", width=12)
        table.add_column("Agent", style="green")
        table.add_column("Status", style="yellow")
        table.add_column("Progress", style="white", no_wrap=True)

        if not self.tasks:
            table.add_row("No tasks", "", "", "")
        else:
            for task in self.tasks[-10:]:
                pct = float(task.get("progress", 0))
                table.add_row(
                    task.get("task_id", "")[:12],
                    task.get("agent", ""),
                    task.get("status", "queued"),
                    _progress_bar(pct),
                )

        return Panel(table, title="[bold]Tasks[/bold]", expand=False, height=10)

    async def update_tasks(self, tasks: list) -> None:
        """Update the displayed task list."""
        self.tasks = tasks
