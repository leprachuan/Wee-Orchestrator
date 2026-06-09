"""Main TUI Application"""

import asyncio
import logging
from typing import Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Header, Input, Static

from tui.api.client import WeeAPIClient
from tui.components.chat_panel import ChatPanel
from tui.components.service_status import ServiceStatusPanel
from tui.components.session_list import SessionListPanel
from tui.components.task_queue import TaskQueuePanel
from tui.config import config


class ControlPanel(Static):
    """Right-side control panel"""

    def on_mount(self) -> None:
        self.border_title = "Controls"

    def render(self):
        """Render control info, reading live state from WeeTUI app"""
        try:
            app = self.app
            agent = getattr(app, "current_agent", "orchestrator")
            runtime = getattr(app, "current_runtime", "copilot")
            model = getattr(app, "current_model", "claude-haiku-4.5")
            session_id = getattr(app, "current_session_id", None)
            session_label = session_id[:8] + "..." if session_id else "(none)"
        except Exception:
            agent, runtime, model, session_label = "orchestrator", "copilot", "claude-haiku-4.5", "(none)"

        return (
            f"[bold #88C0D0]🍀 Wee Orchestrator[/bold #88C0D0]\n\n"
            f"[bold]Current Settings[/bold]\n\n"
            f"Session: [white]{session_label}[/white]\n"
            f"Agent: [cyan]{agent}[/cyan]\n"
            f"Runtime: [magenta]{runtime}[/magenta]\n"
            f"Model: [green]{model}[/green]\n"
            f"Timeout: [yellow]60s[/yellow]\n\n"
            f"[bold]Keyboard Shortcuts[/bold]\n"
            f"Tab - Focus next\n"
            f"Shift+Tab - Focus prev\n"
            f"Ctrl+N - New session\n"
            f"Ctrl+S - Send prompt\n"
            f"Ctrl+Q - Quit\n\n"
            f"[bold]Commands[/bold]\n"
            f"/agent <name>\n"
            f"/model <name>\n"
            f"/runtime <name>\n"
            f"/timeout <sec>\n"
        )


class StatusBar(Static):
    """Bottom status bar"""

    api_status: reactive[str] = reactive("✓ Connected")
    task_count: reactive[int] = reactive(0)

    def render(self):
        """Render status bar"""
        text = Text()
        text.append("✓ Connected", style="bold green")
        text.append(f" | Tasks: {self.task_count} | Wee TUI Ready", style="white")
        return text


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
        height: 100%;
        layout: vertical;
    }

    /* Layout proportions */
    #main         { height: 1fr; }
    #sessions     { width: 30; }
    #center       { width: 1fr; }
    #right        { width: 38; }
    #controls     { height: auto; }
    #status_panel { height: 9; }
    #tasks        { height: 1fr; }
    #chat         { height: 1fr; }

    /* Panel borders — rounded + Nord muted gray-blue */
    SessionListPanel, ServiceStatusPanel, ChatPanel, TaskQueuePanel, ControlPanel {
        border: round #4C566A;
        padding: 0 1;
    }

    /* Highlight focused panel */
    *:focus-within {
        border: round #88C0D0;
    }

    /* Status bar — docked bottom strip */
    #status_bar {
        height: 1;
        dock: bottom;
        background: #3B4252;
        border-top: solid #4C566A;
        padding: 0 1;
        color: $text-muted;
    }

    /* Input field */
    #input {
        border: round #4C566A;
        padding: 0 1;
        background: #3B4252;
    }

    #input:focus {
        border: round #88C0D0;
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
        self._sessions_cache: list = []

    def compose(self) -> ComposeResult:
        """Compose the UI layout"""
        yield Header()

        with Horizontal(id="main"):
            yield SessionListPanel(id="sessions")

            with Vertical(id="center"):
                yield ChatPanel(id="chat")
                yield InputField(id="input", name="prompt")

            with Vertical(id="right"):
                yield ControlPanel(id="controls")
                yield ServiceStatusPanel(id="status_panel")
                yield TaskQueuePanel(id="tasks")

        yield StatusBar(id="status_bar")

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
            self._sessions_cache = sessions
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

    async def refresh_display(self) -> None:
        """Refresh the controls panel display"""
        try:
            self.query_one("#controls", ControlPanel).refresh()
        except Exception:
            pass

    # ── Session selection ──────────────────────────────────────────────────────

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle session row selection from the sessions DataTable."""
        if event.control.id != "sessions":
            return
        session_id = str(event.row_key.value) if event.row_key.value else ""
        if session_id and session_id not in ("__empty__", "__unknown__"):
            self.run_worker(self._load_session(session_id))

    async def _load_session(self, session_id: str) -> None:
        """Switch active session and load its transcript into the chat panel."""
        try:
            if not self.api_client:
                self.notify("❌ API client not initialized", severity="error")
                return

            self.current_session_id = session_id

            # Update agent from cached session data if available
            for s in self._sessions_cache:
                sid = s.get("session_id", s.get("id", ""))
                if sid == session_id:
                    if s.get("agent"):
                        self.current_agent = s["agent"]
                    if s.get("runtime"):
                        self.current_runtime = s["runtime"]
                    if s.get("model"):
                        self.current_model = s["model"]
                    break

            await self.refresh_display()

            # Load transcript
            messages = await self.api_client.get_session_messages(session_id)
            chat = self.query_one("#chat", ChatPanel)
            await chat.load_transcript(messages)

            self.notify(f"📂 Loaded session {session_id[:8]}...", timeout=3)
        except Exception as e:
            logging.error(f"Load session error: {e}")
            self.notify(f"❌ Failed to load session: {e}", severity="error")

    # ── Actions ────────────────────────────────────────────────────────────────

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
            await self.refresh_display()
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
                        await chat_panel.add_message("system", "❌ Usage: /agent <name>")
                elif command == "/model":
                    if arg:
                        self.current_model = arg
                        await chat_panel.add_message("system", f"✅ Model set to: {arg}")
                        await self.refresh_display()
                    else:
                        await chat_panel.add_message("system", "❌ Usage: /model <name>")
                elif command == "/runtime":
                    if arg:
                        self.current_runtime = arg
                        await chat_panel.add_message("system", f"✅ Runtime set to: {arg}")
                        await self.refresh_display()
                    else:
                        await chat_panel.add_message("system", "❌ Usage: /runtime <name>")
                elif command == "/timeout":
                    if arg:
                        try:
                            timeout_sec = int(arg)
                            self.current_timeout = timeout_sec
                            await chat_panel.add_message("system", f"✅ Timeout set to: {timeout_sec}s")
                        except ValueError:
                            await chat_panel.add_message("system", "❌ Timeout must be a number")
                    else:
                        await chat_panel.add_message("system", "❌ Usage: /timeout <seconds>")
                else:
                    await chat_panel.add_message("system", f"❌ Unknown command: {command}")
                return

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
