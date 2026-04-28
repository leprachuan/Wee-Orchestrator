"""Main TUI Application"""

import asyncio
import logging
from typing import Optional

from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Static

from tui.api.client import WeeAPIClient
from tui.components.chat_panel import ChatPanel
from tui.components.service_status import ServiceStatusPanel
from tui.components.session_list import SessionListPanel
from tui.components.task_queue import TaskQueuePanel
from tui.config import config


class ControlPanel(Static):
    """Right-side control panel"""

    def render(self):
        """Render control info"""
        info = """
[bold]Current Settings[/bold]

Agent: [cyan]orchestrator[/cyan]
Runtime: [magenta]copilot[/magenta]
Model: [green]haiku[/green]
Timeout: [yellow]60s[/yellow]

[bold]Keyboard Shortcuts[/bold]
Tab - Focus next
Shift+Tab - Focus prev
Ctrl+N - New session
Ctrl+S - Send prompt
Ctrl+Q - Quit

[bold]Commands[/bold]
/agent <name>
/model <name>
/runtime <name>
/timeout <sec>
"""
        return Panel(info, title="[bold]Controls[/bold]", expand=False, width=35)


class StatusBar(Static):
    """Bottom status bar"""

    api_status: reactive[str] = reactive("✓ Connected")
    task_count: reactive[int] = reactive(0)

    def render(self):
        """Render status bar"""
        status = (
            f"[green]{self.api_status}[/green] | "
            f"Tasks: {self.task_count} | "
            f"Wee TUI Ready"
        )
        return Text(status, style="white")


class InputField(Input):
    """Input field for prompts and commands"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prompt = "Enter prompt or command: "


class WeeTUI(App):
    """Main Wee TUI Application"""

    TITLE = "Wee TUI - Terminal UI for Wee Orchestrator"
    CSS = """
    Screen {
        layout: vertical;
    }

    #status_bar {
        height: 1;
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+n", "new_session", "New Session", show=False),
        Binding("ctrl+s", "send_prompt", "Send", show=False),
        Binding("tab", "focus_next", "Next", show=False),
        Binding("shift+tab", "focus_previous", "Prev", show=False),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.api_client: Optional[WeeAPIClient] = None
        self.current_session_id: Optional[str] = None
        self.setup_logging()

    @staticmethod
    def setup_logging():
        """Setup logging"""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler("/tmp/wee_tui.log"),
            ],
            force=True,
        )

    def compose(self) -> ComposeResult:
        """Compose the UI layout"""
        yield Header()

        with Horizontal(id="main"):
            yield SessionListPanel(id="sessions", expand=False, width=30)

            with Vertical(expand=True, id="center"):
                yield ServiceStatusPanel(id="status_panel", expand=False)
                yield ChatPanel(id="chat", expand=True)
                yield TaskQueuePanel(id="tasks", expand=False)
                yield InputField(id="input", name="prompt")

            yield ControlPanel(id="controls", expand=False)

        yield StatusBar(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize on mount"""
        try:
            config.validate()
        except ValueError as e:
            self.exit(f"Config error: {e}")

        self.run_worker(self.initialize_api())
        self.run_worker(self.refresh_data_loop())

    async def initialize_api(self) -> None:
        """Initialize API client"""
        try:
            self.api_client = WeeAPIClient(config)
            logging.info("API client initialized")
        except Exception as e:
            logging.error(f"Failed to initialize API client: {e}")
            self.notify(f"❌ API Error: {e}", severity="error", timeout=10)

    async def refresh_data_loop(self) -> None:
        """Refresh data periodically"""
        while True:
            try:
                await asyncio.sleep(config.update_interval)
                if self.api_client:
                    await self.refresh_sessions()
                    await self.refresh_tasks()
                    await self.refresh_service_status()
            except Exception as e:
                logging.error(f"Refresh error: {e}")

    async def refresh_sessions(self) -> None:
        """Refresh session list from API"""
        try:
            if not self.api_client:
                return

            async with self.api_client as client:
                sessions = await client.get_sessions()

            # Update session panel
            panel = self.query_one("#sessions", SessionListPanel)
            await panel.update_sessions(sessions)
        except Exception as e:
            logging.error(f"Session refresh error: {e}")

    async def refresh_tasks(self) -> None:
        """Refresh task queue from API"""
        try:
            if not self.api_client:
                return

            async with self.api_client as client:
                tasks = await client.get_background_tasks()

            # Update task panel
            panel = self.query_one("#tasks", TaskQueuePanel)
            await panel.update_tasks(tasks)
        except Exception as e:
            logging.error(f"Task refresh error: {e}")

    async def refresh_service_status(self) -> None:
        """Refresh service status from API"""
        try:
            if not self.api_client:
                return

            async with self.api_client as client:
                status = await client.get_service_status()

            # Update status panel
            panel = self.query_one("#status_panel", ServiceStatusPanel)
            await panel.update_status(status)
        except Exception as e:
            logging.error(f"Service status error: {e}")

    def action_new_session(self) -> None:
        """Create a new session"""
        try:
            asyncio.create_task(self._new_session_async())
        except Exception as e:
            self.notify(f"❌ Error creating session: {e}", severity="error")

    async def _new_session_async(self) -> None:
        """Async session creation"""
        try:
            if not self.api_client:
                self.notify("❌ API client not initialized", severity="error")
                return

            async with self.api_client as client:
                session = await client.create_session()

            self.current_session_id = session.get("id")
            self.notify(f"✅ New session: {self.current_session_id}")
            await self.refresh_sessions()
        except Exception as e:
            logging.error(f"Session creation error: {e}")
            self.notify(f"❌ Failed to create session: {e}", severity="error")

    def action_send_prompt(self) -> None:
        """Send prompt to current session"""
        input_widget = self.query_one("#input", InputField)
        prompt = input_widget.value.strip()

        if not prompt:
            return

        input_widget.value = ""

        if not self.current_session_id:
            self.notify("❌ No active session", severity="error")
            return

        asyncio.create_task(self._send_prompt_async(prompt))

    async def _send_prompt_async(self, prompt: str) -> None:
        """Async prompt sending"""
        try:
            if not self.api_client:
                self.notify("❌ API client not initialized", severity="error")
                return

            async with self.api_client as client:
                response = await client.send_prompt(self.current_session_id, prompt)

            # Update chat panel
            chat_panel = self.query_one("#chat", ChatPanel)
            await chat_panel.add_message("user", prompt)
            await chat_panel.add_message("assistant", response.get("response", ""))

        except Exception as e:
            logging.error(f"Prompt send error: {e}")
            self.notify(f"❌ Error sending prompt: {e}", severity="error")

    def action_focus_next(self) -> None:
        """Focus next widget"""
        self.screen.focus_next()

    def action_focus_previous(self) -> None:
        """Focus previous widget"""
        self.screen.focus_previous()


def main():
    """Main entry point"""
    app = WeeTUI()
    app.run()


if __name__ == "__main__":
    main()
