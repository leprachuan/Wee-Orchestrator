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
        """Render control info, reading live state from WeeTUI app"""
        try:
            app = self.app
            agent = getattr(app, "current_agent", "orchestrator")
            runtime = getattr(app, "current_runtime", "copilot")
            model = getattr(app, "current_model", "claude-haiku-4.5")
        except Exception:
            agent, runtime, model = "orchestrator", "copilot", "claude-haiku-4.5"

        info = f"""
[bold]Current Settings[/bold]

Agent: [cyan]{agent}[/cyan]
Runtime: [magenta]{runtime}[/magenta]
Model: [green]{model}[/green]
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
        self.current_agent = "orchestrator"
        self.current_runtime = "copilot"
        self.current_model = "claude-haiku-4.5"

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
            return

        self.api_client = WeeAPIClient(
            base_url=config.api_url,
            auth_token=config.auth_token,
            user_id=config.user_identity,
            channel=config.auth_channel,
            verify_ssl=config.verify_ssl,
        )
        self.run_worker(self.initialize_api())
        self.run_worker(self.refresh_data_loop())

    async def on_unmount(self) -> None:
        """Cleanup on unmount"""
        if self.api_client:
            await self.api_client.close()

    async def initialize_api(self) -> None:
        """Establish persistent API connection"""
        try:
            await self.api_client.connect()
            logging.info("API client connected")
        except Exception as e:
            logging.error(f"Failed to connect API client: {e}")
            self.notify(f"❌ API Error: {e}", severity="error", timeout=10)

    async def refresh_data_loop(self) -> None:
        """Refresh data periodically"""
        while True:
            try:
                await asyncio.sleep(config.update_interval)
                if self.api_client and self.api_client.client:
                    await self.refresh_sessions()
                    await self.refresh_tasks()
                    await self.refresh_service_status()
            except Exception as e:
                logging.error(f"Refresh error: {e}")

    async def refresh_sessions(self) -> None:
        """Refresh session list from API"""
        try:
            sessions = await self.api_client.get_sessions()
            panel = self.query_one("#sessions", SessionListPanel)
            await panel.update_sessions(sessions)
        except Exception as e:
            logging.error(f"Session refresh error: {e}")

    async def refresh_tasks(self) -> None:
        """Refresh task queue from API"""
        try:
            tasks = await self.api_client.get_background_tasks()
            panel = self.query_one("#tasks", TaskQueuePanel)
            await panel.update_tasks(tasks)
        except Exception as e:
            logging.error(f"Task refresh error: {e}")

    async def refresh_service_status(self) -> None:
        """Refresh service status from API"""
        try:
            status = await self.api_client.get_service_status()
            panel = self.query_one("#status_panel", ServiceStatusPanel)
            await panel.update_status(status)
        except Exception as e:
            logging.error(f"Service status error: {e}")

    def action_new_session(self) -> None:
        """Create a new session"""
        self.run_worker(self._new_session_async())

    async def _new_session_async(self) -> None:
        """Async session creation"""
        try:
            if not self.api_client:
                self.notify("❌ API client not initialized", severity="error")
                return

            session_id = await self.api_client.create_session(
                runtime=self.current_runtime,
                model=self.current_model,
                agent=self.current_agent,
            )
            if not session_id:
                self.notify("❌ Session creation returned no ID", severity="error")
                return
            self.current_session_id = session_id
            self.notify(f"✅ New session: {session_id[:8]}...")
            await self.refresh_sessions()
        except Exception as e:
            logging.error(f"Session creation error: {e}")
            self.notify(f"❌ Failed to create session: {e}", severity="error")

    def action_send_prompt(self) -> None:
        """Send prompt as a background task"""
        input_widget = self.query_one("#input", InputField)
        prompt = input_widget.value.strip()

        if not prompt:
            return

        input_widget.value = ""
        self.run_worker(self._send_prompt_async(prompt))

    async def _send_prompt_async(self, prompt: str) -> None:
        """Dispatch prompt as a background task or handle commands"""
        try:
            if not self.api_client:
                self.notify("❌ API client not initialized", severity="error")
                return

            # Check if this is a command (starts with /)
            if prompt.startswith("/"):
                parts = prompt.split(None, 1)
                command = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                chat_panel = self.query_one("#chat", ChatPanel)
                
                if command == "/agent":
                    if arg:
                        self.current_agent = arg
                        await chat_panel.add_message("system", f"✅ Agent set to: {arg}")
                        await self.refresh_display()
                    else:
                        await chat_panel.add_message("system", f"❌ Usage: /agent <name>")
                elif command == "/model":
                    if arg:
                        self.current_model = arg
                        await chat_panel.add_message("system", f"✅ Model set to: {arg}")
                    else:
                        await chat_panel.add_message("system", f"❌ Usage: /model <name>")
                elif command == "/runtime":
                    if arg:
                        self.current_runtime = arg
                        await chat_panel.add_message("system", f"✅ Runtime set to: {arg}")
                    else:
                        await chat_panel.add_message("system", f"❌ Usage: /runtime <name>")
                elif command == "/timeout":
                    if arg:
                        try:
                            timeout_sec = int(arg)
                            self.current_timeout = timeout_sec
                            await chat_panel.add_message("system", f"✅ Timeout set to: {timeout_sec}s")
                        except ValueError:
                            await chat_panel.add_message("system", f"❌ Timeout must be a number")
                    else:
                        await chat_panel.add_message("system", f"❌ Usage: /timeout <seconds>")
                else:
                    await chat_panel.add_message("system", f"❌ Unknown command: {command}")
                return

            # Not a command, dispatch as background task
            logging.info(f"Sending prompt (len={len(prompt)})")
            task_id = await self.api_client.create_background_task(
                prompt=prompt,
                agent=self.current_agent,
                runtime=self.current_runtime,
                model=self.current_model,
            )
            chat_panel = self.query_one("#chat", ChatPanel)
            await chat_panel.add_message("user", prompt)
            await chat_panel.add_message("assistant", f"✅ Task dispatched: {task_id}")
        except Exception as e:
            logging.error(f"Prompt send error: {e}")
            self.notify(f"❌ Error sending prompt: {e}", severity="error")


def main():
    """Main entry point"""
    app = WeeTUI()
    app.run()


if __name__ == "__main__":
    main()
