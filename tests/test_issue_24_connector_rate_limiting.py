#!/usr/bin/env python3
"""Regression tests for issue #24: connector per-user rate limiting."""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def telegram_connector(tmp_path):
    config_path = tmp_path / "telegram_config.json"
    config_path.write_text(
        json.dumps(
            {
                "token": "fake_token",
                "allowed_users": [42],
                "user_pairings": {},
                "enable_auto_pair": False,
                "default_agent": "orchestrator",
                "default_model": "gpt-5-mini",
                "user_rate_limit_max_requests": 1,
                "user_rate_limit_window_seconds": 60,
            }
        )
    )

    with patch("telegram_connector.requests") as mock_requests:
        get_me_response = MagicMock()
        get_me_response.json.return_value = {
            "ok": True,
            "result": {"id": 123456, "is_bot": True},
        }
        get_me_response.raise_for_status = MagicMock()
        mock_requests.get.return_value = get_me_response

        from telegram_connector import TelegramConnector

        connector = TelegramConnector("fake_token", config_file=str(config_path))

    return connector


@pytest.fixture
def webex_connector(tmp_path):
    config_path = tmp_path / "webex_config.json"
    config_path.write_text(
        json.dumps(
            {
                "token": "fake_token",
                "rabbitmq_host": "localhost",
                "rabbitmq_port": 5672,
                "rabbitmq_user": "guest",
                "rabbitmq_password": "",
                "rabbitmq_queue": "webex",
                "rabbitmq_vhost": "/",
                "allowed_users": ["person-1"],
                "user_pairings": {},
                "enable_auto_pair": False,
                "default_agent": "orchestrator",
                "default_model": "gpt-5-mini",
                "user_rate_limit_max_requests": 1,
                "user_rate_limit_window_seconds": 60,
            }
        )
    )

    from webex_connector import WebEXConnector

    return WebEXConnector("fake_token", config_file=str(config_path))


def test_issue_24_telegram_regular_messages_are_rate_limited(telegram_connector):
    update = {
        "message": {
            "from": {"id": 42, "username": "tester", "is_bot": False},
            "chat": {"id": 1001},
            "text": "hello",
        }
    }

    with (
        patch.object(telegram_connector, "send_typing"),
        patch.object(
            telegram_connector,
            "_query_agent_with_status",
            return_value=("first response", None),
        ) as query_mock,
        patch.object(telegram_connector, "send_response") as send_response_mock,
        patch.object(telegram_connector, "send_message") as send_message_mock,
    ):
        telegram_connector.handle_message(update)
        telegram_connector.handle_message(update)

    assert query_mock.call_count == 1
    send_response_mock.assert_called_once_with(1001, "first response", None)
    send_message_mock.assert_called_once()
    assert "Rate limit exceeded" in send_message_mock.call_args.args[1]


def test_issue_24_telegram_command_messages_are_rate_limited(telegram_connector):
    update = {
        "message": {
            "from": {"id": 42, "username": "tester", "is_bot": False},
            "chat": {"id": 1001},
            "text": "/help",
        }
    }

    with (
        patch.object(telegram_connector, "send_typing"),
        patch.object(
            telegram_connector, "_execute_command", return_value="command response"
        ) as execute_mock,
        patch.object(telegram_connector, "send_message") as send_message_mock,
    ):
        telegram_connector.handle_message(update)
        telegram_connector.handle_message(update)

    assert execute_mock.call_count == 1
    assert send_message_mock.call_args_list[0].args == (1001, "command response")
    assert "Rate limit exceeded" in send_message_mock.call_args_list[1].args[1]


def test_issue_24_webex_regular_messages_are_rate_limited(webex_connector):
    message = {
        "personId": "person-1",
        "personEmail": "user@example.com",
        "roomId": "room-1",
        "text": "hello",
        "files": [],
    }

    with (
        patch.object(
            webex_connector,
            "_query_agent_with_status",
            return_value=("first response", None),
        ) as query_mock,
        patch.object(webex_connector, "send_response") as send_response_mock,
        patch.object(webex_connector, "send_message") as send_message_mock,
    ):
        webex_connector.handle_message(message)
        webex_connector.handle_message(message)

    assert query_mock.call_count == 1
    send_response_mock.assert_called_once_with("room-1", "first response", None)
    send_message_mock.assert_called_once()
    assert "Rate limit exceeded" in send_message_mock.call_args.args[1]


def test_issue_24_webex_command_messages_are_rate_limited(webex_connector):
    message = {
        "personId": "person-1",
        "personEmail": "user@example.com",
        "roomId": "room-1",
        "text": "/help",
        "files": [],
    }

    with (
        patch.object(webex_connector, "_execute_command", return_value="command ok")
        as execute_mock,
        patch.object(webex_connector, "send_message") as send_message_mock,
    ):
        webex_connector.handle_message(message)
        webex_connector.handle_message(message)

    assert execute_mock.call_count == 1
    assert send_message_mock.call_args_list[0].args == ("room-1", "command ok")
    assert "Rate limit exceeded" in send_message_mock.call_args_list[1].args[1]


def test_issue_24_telegram_voice_messages_are_blocked_before_download(
    telegram_connector,
):
    update = {
        "message": {
            "from": {"id": 42, "username": "tester", "is_bot": False},
            "chat": {"id": 1001},
            "voice": {"file_id": "voice-1", "duration": 3},
        }
    }

    with (
        patch.object(
            telegram_connector, "download_file", return_value="/tmp/voice.ogg"
        ) as download_mock,
        patch("telegram_connector.audio_transcriber.transcribe") as transcribe_mock,
        patch.object(
            telegram_connector,
            "_query_agent_with_status",
            return_value=("voice response", None),
        ) as query_mock,
        patch.object(telegram_connector, "send_typing"),
        patch.object(telegram_connector, "send_response") as send_response_mock,
        patch.object(telegram_connector, "send_message") as send_message_mock,
        patch.object(telegram_connector, "cleanup_files"),
    ):
        transcribe_mock.return_value = ("transcribed audio", "mock")

        telegram_connector.handle_message(update)
        telegram_connector.handle_message(update)

    assert download_mock.call_count == 1
    assert transcribe_mock.call_count == 1
    assert query_mock.call_count == 1
    send_response_mock.assert_called_once_with(1001, "voice response", None)
    assert "Rate limit exceeded" in send_message_mock.call_args.args[1]


def test_issue_24_webex_files_are_blocked_before_download(webex_connector):
    message = {
        "personId": "person-1",
        "personEmail": "user@example.com",
        "roomId": "room-1",
        "text": "",
        "files": ["https://files.example/test.png"],
    }

    with (
        patch.object(
            webex_connector,
            "download_file",
            return_value=("/tmp/test.png", "test.png"),
        ) as download_mock,
        patch("webex_connector.audio_transcriber.is_audio_file", return_value=False),
        patch.object(
            webex_connector,
            "_query_agent_with_status",
            return_value=("file response", None),
        ) as query_mock,
        patch.object(webex_connector, "send_response") as send_response_mock,
        patch.object(webex_connector, "send_message") as send_message_mock,
    ):
        webex_connector.handle_message(message)
        webex_connector.handle_message(message)

    assert download_mock.call_count == 1
    assert query_mock.call_count == 1
    send_response_mock.assert_called_once_with("room-1", "file response", None)
    assert "Rate limit exceeded" in send_message_mock.call_args.args[1]
