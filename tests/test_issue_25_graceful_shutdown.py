#!/usr/bin/env python3
"""Regression tests for issue #25: graceful connector shutdown."""

import json
import signal
import threading
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
            }
        )
    )

    from webex_connector import WebEXConnector

    return WebEXConnector("fake_token", config_file=str(config_path))


def test_issue_25_telegram_sigterm_waits_for_active_request(telegram_connector):
    update = {
        "message": {
            "from": {"id": 42, "username": "tester", "is_bot": False},
            "chat": {"id": 1001},
            "text": "hello",
        }
    }
    started = threading.Event()
    release = threading.Event()

    def blocking_query(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=1)
        return ("done", None)

    telegram_connector.running = True

    with (
        patch.object(telegram_connector, "send_typing"),
        patch.object(
            telegram_connector,
            "_query_agent_with_status",
            side_effect=blocking_query,
        ) as query_mock,
        patch.object(telegram_connector, "send_response") as send_response_mock,
        patch.object(telegram_connector, "send_message") as send_message_mock,
    ):
        worker = threading.Thread(
            target=telegram_connector.handle_message, args=(update,), daemon=True
        )
        worker.start()
        assert started.wait(timeout=1)

        telegram_connector._handle_shutdown_signal(signal.SIGTERM, None)

        assert telegram_connector.shutdown_event.is_set()
        assert telegram_connector.running is False
        assert (
            telegram_connector._wait_for_active_requests(
                "telegram test", timeout=0.01
            )
            is False
        )

        telegram_connector.handle_message(update)
        assert query_mock.call_count == 1

        release.set()
        worker.join(timeout=1)

        assert (
            telegram_connector._wait_for_active_requests(
                "telegram test", timeout=0.1
            )
            is True
        )
        send_response_mock.assert_called_once_with(1001, "done", None)
        send_message_mock.assert_not_called()


def test_issue_25_webex_sigterm_stops_consuming_and_waits_for_active_request(
    webex_connector,
):
    message = {
        "personId": "person-1",
        "personEmail": "user@example.com",
        "roomId": "room-1",
        "text": "hello",
        "files": [],
    }
    started = threading.Event()
    release = threading.Event()

    def blocking_query(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=1)
        return ("done", None)

    webex_connector.running = True
    webex_connector.rabbitmq_channel = MagicMock()
    webex_connector.rabbitmq_channel.is_open = True
    webex_connector.rabbitmq_connection = MagicMock()
    webex_connector.rabbitmq_connection.is_closed = False
    webex_connector.rabbitmq_connection.add_callback_threadsafe.side_effect = (
        lambda callback: callback()
    )

    with (
        patch.object(
            webex_connector,
            "_query_agent_with_status",
            side_effect=blocking_query,
        ) as query_mock,
        patch.object(webex_connector, "send_response") as send_response_mock,
        patch.object(webex_connector, "send_message") as send_message_mock,
    ):
        worker = threading.Thread(
            target=webex_connector.handle_message, args=(message,), daemon=True
        )
        worker.start()
        assert started.wait(timeout=1)

        webex_connector._handle_shutdown_signal(signal.SIGTERM, None)

        assert webex_connector.shutdown_event.is_set()
        assert webex_connector.running is False
        webex_connector.rabbitmq_connection.add_callback_threadsafe.assert_called_once()
        webex_connector.rabbitmq_channel.stop_consuming.assert_called_once()
        assert (
            webex_connector._wait_for_active_requests("webex test", timeout=0.01)
            is False
        )

        webex_connector.handle_message(message)
        assert query_mock.call_count == 1

        release.set()
        worker.join(timeout=1)

        assert (
            webex_connector._wait_for_active_requests("webex test", timeout=0.1)
            is True
        )
        send_response_mock.assert_called_once_with("room-1", "done", None)
        send_message_mock.assert_not_called()
