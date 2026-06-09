"""Chat panel component"""

from rich.align import Align
from rich.text import Text
from textual.widgets import RichLog

PLACEHOLDER = "[dim]No messages yet — type a prompt below and press Ctrl+S[/dim]"


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
