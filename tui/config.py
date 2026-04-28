"""Configuration for wee-tui"""
import os
from dataclasses import dataclass

@dataclass
class TUIConfig:
    """TUI Configuration"""
    api_url: str = os.getenv("WEE_API_URL", "https://127.0.0.1:8001")
    verify_ssl: bool = os.getenv("WEE_VERIFY_SSL", "false").lower() == "true"
    auth_token: str = os.getenv("WEE_AUTH_TOKEN", "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU")
    user_identity: str = os.getenv("WEE_USER_ID", "8193231291")
    auth_channel: str = os.getenv("WEE_CHANNEL", "tui")
    
    # TUI-specific settings
    update_interval: float = 1.0  # Update rate in seconds
    ws_reconnect_delay: float = 2.0  # WebSocket reconnect delay
    max_history_lines: int = 10000  # Max lines in chat history
    theme: str = os.getenv("WEE_TUI_THEME", "default")

config = TUIConfig()
