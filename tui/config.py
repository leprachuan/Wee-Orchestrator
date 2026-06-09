"""Configuration for wee-tui"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()  # loads .env from cwd, no-op if not found


@dataclass
class TUIConfig:
    """TUI Configuration"""

    api_url: str = os.getenv("WEE_API_URL", "https://127.0.0.1:8001")
    verify_ssl: bool = os.getenv("WEE_VERIFY_SSL", "false").lower() == "true"
    auth_token: str = os.getenv("WEE_AUTH_TOKEN", "")
    user_identity: str = os.getenv("WEE_USER_ID", "")
    auth_channel: str = os.getenv("WEE_CHANNEL", "tui")

    # TUI-specific settings
    update_interval: float = 5.0  # Update rate in seconds
    ws_reconnect_delay: float = 2.0  # WebSocket reconnect delay
    max_history_lines: int = 10000  # Max lines in chat history
    theme: str = os.getenv("WEE_TUI_THEME", "default")

    def validate(self) -> None:
        """Validate required config"""
        if not self.auth_token:
            raise ValueError("WEE_AUTH_TOKEN env var required for API authentication")
        if not self.user_identity:
            raise ValueError("WEE_USER_ID env var required for user identification")


# Create singleton config instance
config = TUIConfig()
