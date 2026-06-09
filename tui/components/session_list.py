"""Session list component"""

from typing import Any, Dict, List

from textual.widgets import DataTable


class SessionListPanel(DataTable):
    """Selectable session list panel using DataTable for keyboard/mouse navigation"""

    def on_mount(self) -> None:
        self.border_title = "Sessions"
        self.cursor_type = "row"
        self.add_columns("ID", "Agent", "Status")

    async def update_sessions(self, sessions: List[Dict[str, Any]]) -> None:
        """Rebuild the session rows; preserve cursor position by session_id if possible."""
        try:
            current_key = self.get_row_at(self.cursor_row)[0] if self.row_count else None
        except Exception:
            current_key = None

        self.clear()

        if not sessions:
            self.add_row("—", "No sessions", "", key="__empty__")
            return

        max_rows = max(0, self.size.height - 3)
        new_row_key = None
        for session in sessions[:max_rows]:
            sid = session.get("session_id", session.get("id", ""))
            short_id = sid[:8] if sid else "—"
            agent = session.get("agent", "—")
            status = session.get("status", "unknown")
            self.add_row(short_id, agent, status, key=sid or "__unknown__")
            if short_id == current_key:
                new_row_key = sid

        # Restore cursor to the previously selected row after rebuild
        if new_row_key:
            try:
                self.move_cursor(row=self.get_row_index(new_row_key))
            except Exception:
                pass
