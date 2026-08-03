"""Regression coverage for Issue #428: shared dev Telegram pairing config."""

import json

import agent_manager
import telegram_connector


def _write_config(path):
    path.write_text(
        json.dumps(
            {
                "token": "",
                "allowed_users": [],
                "user_pairings": {
                    "8193231291": {
                        "user_id": 8193231291,
                        "username": "vtflip",
                    }
                },
                "enable_auto_pair": False,
                "default_agent": "orchestrator",
                "default_model": "gpt-5-mini",
                "pinned_users": {},
                "yolo_allowed_users": [],
            }
        )
    )


def test_api_resolves_username_from_shared_config(tmp_path, monkeypatch):
    config = tmp_path / "telegram-dev.json"
    _write_config(config)
    monkeypatch.setenv("TELEGRAM_CONFIG_PATH", str(config))

    assert agent_manager._resolve_telegram_identity("vtflip") == "8193231291"
    assert agent_manager._resolve_telegram_identity("@VTFLIP") == "8193231291"
    assert agent_manager._get_telegram_username("8193231291") == "vtflip"


def test_listener_uses_same_environment_config(tmp_path, monkeypatch):
    config = tmp_path / "telegram-dev.json"
    _write_config(config)
    monkeypatch.setenv("TELEGRAM_CONFIG_PATH", str(config))

    assert telegram_connector.default_telegram_config_path() == str(config)
    loaded = telegram_connector.TelegramConfig()
    assert loaded.get_user_session(8193231291)["username"] == "vtflip"
