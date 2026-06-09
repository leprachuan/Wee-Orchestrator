"""Task queue component"""

from rich.table import Table
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

_BAR_WIDTH = 12


def _progress_bar(pct: float) -> Text:
    """Return a styled Unicode block progress bar Text for a 0-100 percentage."""
    filled = int(pct / 100 * _BAR_WIDTH)
    empty = _BAR_WIDTH - filled
    bar = Text()
    bar.append("█" * filled, style="#A3BE8C")
    bar.append("░" * empty, style="#4C566A")
    bar.append(f" {int(pct)}%", style="white")
    return bar


class TaskQueuePanel(Static):
    """Panel for background tasks queue"""

    tasks: reactive[list] = reactive([])

    def on_mount(self) -> None:
        self.border_title = "Tasks"

    def render(self):
        """Render task queue"""
        table = Table(show_header=True, header_style="bold", expand=True)
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

        return table

    async def update_tasks(self, tasks: list) -> None:
        """Update the displayed task list."""
        self.tasks = tasks
