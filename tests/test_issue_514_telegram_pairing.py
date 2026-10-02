"""Regression coverage for Telegram pairing-token resolution (#514)."""

import sys
from types import SimpleNamespace
from unittest import mock

import agent_manager


def test_pairing_uses_configured_token_without_optional_resolver():
    """A configured Telegram token must work when the legacy resolver is absent."""
    sent = []

    class FakeConnector:
        def __init__(self, token, config_file):
            self.token = token
            self.config_file = config_file

        def send_message(self, identity, message):
            sent.append((self.token, self.config_file, identity, message))

    fake_connector_module = SimpleNamespace(TelegramConnector=FakeConnector)
    with mock.patch.dict(sys.modules, {"telegram_connector": fake_connector_module}), mock.patch.object(
        agent_manager, "_load_telegram_config", return_value={"token": "configured-token"}
    ), mock.patch.object(agent_manager, "_telegram_config_path", return_value="/tmp/telegram.json"):
        delivered = agent_manager._send_pairing_code("telegram", "12345", "123456")

    assert delivered is True
    assert sent == [
        (
            "configured-token",
            "/tmp/telegram.json",
            12345,
            "Your pairing code is: 123456\nIt expires in 5 minutes.",
        )
    ]
