"""Task queue component"""

from textual.widgets import DataTable


class TaskQueuePanel(DataTable):
    """Interactive panel for background tasks queue"""

    def on_mount(self) -> None:
        self.border_title = "Tasks"
        self.add_columns("Task ID", "Agent")
        self.cursor_type = "row"
        self.show_cursor = True

    async def update_tasks(self, tasks: list) -> None:
        """Update the displayed task list."""
        # Preserve current cursor row if possible
        current_key = None
        if self.cursor_row < self.row_count:
            try:
                row_keys = list(self._row_locations.keys())
                if row_keys:
                    current_key = row_keys[self.cursor_row]
            except Exception:
                pass

        max_rows = max(0, self.size.height - 3)
        self.clear()

        if not tasks:
            self.add_row("(no tasks)", "", key="__empty__")
            return

        for task in tasks[:max_rows]:
            tid = task.get("task_id", "")
            self.add_row(
                tid[:12],
                task.get("agent", ""),
                key=tid or None,
            )

        # Restore cursor
        if current_key:
            try:
                self.move_cursor(row=self.get_row_index(current_key))
            except Exception:
                pass
