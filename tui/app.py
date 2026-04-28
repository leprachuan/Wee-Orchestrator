"""Main Wee TUI Application using Textual"""
from __future__ import annotations
import os
import asyncio
import logging
from datetime import datetime
from typing import Optional

from textual.app import ComposeResult, App
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    Header, Footer, Static, Input, Button, Label, ListItem, ListView, 
    DataTable, ScrollableContainer, RichLog
)
from textual.binding import Binding
from textual.reactive import reactive
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

from tui.api.client import WeeAPIClient
from tui.theme import get_agent_color, get_status_color, STYLES

logger = logging.getLogger(__name__)

class SessionListPanel(Static):
    """Left panel showing available sessions"""
    
    sessions: reactive[list] = reactive([])
    selected_session: reactive[Optional[str]] = reactive(None)
    
    def render(self):
        """Render the session list"""
        table = Table(title="Sessions", show_header=True, header_style="bold")
        table.add_column("ID", style="cyan")
        table.add_column("Runtime", style="magenta")
        table.add_column("Agent", style="green")
        table.add_column("Status", style="yellow")
        table.add_column("Msgs", style="white")
        
        if not self.sessions:
            table.add_row("No sessions", "", "", "", "")
        else:
            for session in self.sessions:
                status_color = get_status_color(session.get("status", "idle"))
                agent_color = get_agent_color(session.get("agent", "orchestrator"))
                table.add_row(
                    session.get("id", "")[:12],
                    session.get("runtime", ""),
                    session.get("agent", ""),
                    f"[{status_color}]{session.get('status', 'idle')}[/{status_color}]",
                    str(session.get("message_count", 0)),
                )
        
        return Panel(table, title="[bold]Sessions[/bold]", expand=False, height=15)


class ChatPanel(Static):
    """Center panel for chat history and streaming"""
    
    messages: reactive[list] = reactive([])
    
    def render(self):
        """Render chat messages"""
        if not self.messages:
            return Panel("No messages yet. Select a session or start a new one.", title="Chat")
        
        log_content = ""
        for msg in self.messages[-30:]:  # Show last 30 messages
            role = msg.get("role", "user").upper()
            content = msg.get("content", "")[:500]  # Truncate long messages
            timestamp = msg.get("timestamp", "")
            log_content += f"\n[bold]{role}[/bold] ({timestamp})\n{content}\n"
        
        return Panel(log_content, title="Chat History", expand=True, overflow="auto")


class ControlPanel(Static):
    """Right panel for controls (agent, model, runtime, timeout)"""
    
    current_agent: reactive[str] = reactive("orchestrator")
    current_runtime: reactive[str] = reactive("copilot")
    current_model: reactive[str] = reactive("claude-haiku-4.5")
    timeout_sec: reactive[int] = reactive(300)
    
    def render(self):
        """Render control panel"""
        info = f"""
[bold]Settings[/bold]

Agent: [cyan]{self.current_agent}[/cyan]
Runtime: [magenta]{self.current_runtime}[/magenta]
Model: [green]{self.current_model}[/green]
Timeout: [yellow]{self.timeout_sec}s[/yellow]

[bold]Shortcuts[/bold]
Tab     - Focus next
Shift+Tab - Focus prev
Ctrl+N  - New session
Ctrl+S  - Send prompt
Ctrl+Q  - Quit

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
        status = f"[green]{self.api_status}[/green] | Tasks: {self.task_count}"
        return Text(status, style="white")


class InputField(Input):
    """Input field for prompts and commands"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prompt = "Enter prompt or command: "


class WeeTUI(App):
    """Main Wee TUI Application"""
    
    TITLE = "Wee TUI - Terminal UI for Wee Orchestrator"
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
        self.setup_logging()
        
    def setup_logging(self):
        """Setup logging"""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler("/tmp/wee_tui.log"),
            ]
        )
    
    def compose(self) -> ComposeResult:
        """Compose the UI layout"""
        yield Header()
        
        with Horizontal():
            yield SessionListPanel(id="sessions", expand=False, width=30)
            
            with Vertical(expand=True):
                yield ChatPanel(id="chat", expand=True)
                yield InputField(id="input", name="prompt")
            
            yield ControlPanel(id="controls", expand=False)
        
        yield StatusBar(id="status")
        yield Footer()
    
    def on_mount(self) -> None:
        """Initialize on mount"""
        asyncio.create_task(self.initialize_api())
        asyncio.create_task(self.refresh_data_loop())
    
    async def initialize_api(self) -> None:
        """Initialize API client"""
        try:
            api_url = os.getenv("WEE_API_URL", "https://127.0.0.1:8001")
            auth_token = os.getenv("WEE_AUTH_TOKEN", "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU")
            user_id = os.getenv("WEE_USER_ID", "8193231291")
            
            self.api_client = WeeAPIClient(api_url, auth_token, user_id)
            
            # Test connection
            async with self.api_client:
                health = await self.api_client.get_health()
                logger.info(f"API connected: {health}")
                self.query_one("#status", StatusBar).api_status = "✓ Connected"
        except Exception as e:
            logger.error(f"API initialization failed: {e}")
            self.query_one("#status", StatusBar).api_status = "✗ Offline"
    
    async def refresh_data_loop(self) -> None:
        """Refresh data periodically"""
        while True:
            try:
                await self.refresh_sessions()
                await self.refresh_tasks()
            except Exception as e:
                logger.error(f"Refresh error: {e}")
            
            await asyncio.sleep(2.0)  # Refresh every 2 seconds
    
    async def refresh_sessions(self) -> None:
        """Refresh session list"""
        if not self.api_client:
            return
        
        try:
            async with self.api_client:
                sessions = await self.api_client.get_sessions()
                panel = self.query_one("#sessions", SessionListPanel)
                panel.sessions = sessions
        except Exception as e:
            logger.error(f"Failed to refresh sessions: {e}")
    
    async def refresh_tasks(self) -> None:
        """Refresh background tasks"""
        if not self.api_client:
            return
        
        try:
            async with self.api_client:
                tasks = await self.api_client.get_background_tasks()
                self.query_one("#status", StatusBar).task_count = len(tasks)
        except Exception as e:
            logger.error(f"Failed to refresh tasks: {e}")
    
    def action_new_session(self) -> None:
        """Create a new session"""
        logger.info("New session action")
    
    def action_send_prompt(self) -> None:
        """Send a prompt"""
        input_field = self.query_one("#input", InputField)
        prompt = input_field.value
        logger.info(f"Sending prompt: {prompt}")
        input_field.value = ""
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission"""
        self.action_send_prompt()


def main():
    """Run the TUI"""
    app = WeeTUI()
    app.run()


if __name__ == "__main__":
    main()
