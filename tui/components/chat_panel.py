"""Chat panel component"""

from typing import List, Tuple

from rich.panel import Panel
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import RichLog, Static


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
        "assistant": "bold green",
        "system": "bold yellow",
        "error": "bold red",
    }

    async def add_message(self, role: str, content: str) -> None:
        """Add a message to the chat panel.

        Args:
            role: Message role (user, assistant, system, error)
            content: Message text content
        """
        style = self.ROLE_STYLES.get(role, "white")
        prefix = Text(f"[{role.upper()}] ", style=style)
        message = Text.assemble(prefix, content)
        self.write(message)
