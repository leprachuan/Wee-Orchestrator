#!/usr/bin/env python3
"""Regression tests for issue #25: graceful connector shutdown."""

import json
import signal
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests


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


def test_issue_25_telegram_shutdown_interrupts_poll_loop(telegram_connector):
    get_updates_started = threading.Event()

    def fake_get(url, params=None, timeout=None, **_kwargs):
        if url.endswith("/getUpdates"):
            get_updates_started.set()
            while not telegram_connector.shutdown_event.is_set():
                time.sleep(0.01)
            raise requests.exceptions.ReadTimeout("shutdown")
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"ok": True, "result": {"id": 123456}}
        return response

    with patch("telegram_connector.requests.get", side_effect=fake_get):
        worker = threading.Thread(target=telegram_connector.run, daemon=True)
        worker.start()

        assert get_updates_started.wait(timeout=1)
        telegram_connector._handle_shutdown_signal(signal.SIGTERM, None)

        worker.join(timeout=1)
        assert worker.is_alive() is False


def test_issue_25_telegram_shutdown_waits_without_default_timeout(
    telegram_connector,
):
    update = {
        "message": {
            "from": {"id": 42, "username": "tester", "is_bot": False},
            "chat": {"id": 1001},
            "text": "hello",
        }
    }
    started = threading.Event()
    release = threading.Event()
    wait_started = threading.Event()
    wait_finished = threading.Event()

    def blocking_query(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=2)
        return ("done", None)

    def wait_for_shutdown_drain():
        wait_started.set()
        assert telegram_connector._wait_for_active_requests("telegram test") is True
        wait_finished.set()

    telegram_connector.running = True

    with (
        patch.object(telegram_connector, "send_typing"),
        patch.object(
            telegram_connector,
            "_query_agent_with_status",
            side_effect=blocking_query,
        ),
        patch.object(telegram_connector, "send_response"),
        patch.object(telegram_connector, "send_message"),
    ):
        worker = threading.Thread(
            target=telegram_connector.handle_message, args=(update,), daemon=True
        )
        worker.start()
        assert started.wait(timeout=1)

        telegram_connector._handle_shutdown_signal(signal.SIGTERM, None)
        assert telegram_connector.shutdown_timeout is None

        waiter = threading.Thread(target=wait_for_shutdown_drain, daemon=True)
        waiter.start()
        assert wait_started.wait(timeout=1)
        time.sleep(0.1)
        assert wait_finished.is_set() is False

        release.set()
        worker.join(timeout=1)
        waiter.join(timeout=1)
        assert waiter.is_alive() is False
        assert wait_finished.is_set() is True


def test_issue_25_telegram_unauthorized_media_rejected_before_download(
    telegram_connector,
):
    telegram_connector.config.config["allowed_users"] = [999]
    update = {
        "message": {
            "from": {"id": 42, "username": "tester", "is_bot": False},
            "chat": {"id": 1001},
            "document": {"file_id": "doc-1"},
            "caption": "please review",
        }
    }

    with (
        patch.object(telegram_connector, "download_file") as download_file_mock,
        patch.object(telegram_connector, "send_message") as send_message_mock,
    ):
        telegram_connector.handle_message(update)

    download_file_mock.assert_not_called()
    send_message_mock.assert_called_once_with(
        1001, "❌ You are not authorized to use this bot."
    )


def test_issue_25_telegram_missing_sender_is_ignored_cleanly(telegram_connector):
    update = {"message": {"chat": {"id": 1001}, "text": "hello"}}

    with patch.object(telegram_connector, "send_message") as send_message_mock:
        telegram_connector.handle_message(update)

    send_message_mock.assert_not_called()


def test_issue_25_webex_ack_happens_after_processing(webex_connector):
    message = {
        "personId": "person-1",
        "personEmail": "user@example.com",
        "roomId": "room-1",
        "text": "hello",
        "files": [],
    }
    callback_holder = {}
    channel = MagicMock()
    method = MagicMock(delivery_tag="tag-1")
    webex_connector.rabbitmq_channel = channel
    webex_connector.rabbitmq_connection = MagicMock(is_closed=False)
    webex_connector.running = True

    def fake_basic_consume(*, queue, on_message_callback):
        callback_holder["callback"] = on_message_callback

    def fake_start_consuming():
        callback_holder["callback"](
            channel, method, None, json.dumps(message).encode()
        )
        webex_connector.running = False

    channel.basic_consume.side_effect = fake_basic_consume
    channel.start_consuming.side_effect = fake_start_consuming

    with (
        patch.object(webex_connector, "connect_rabbitmq", return_value=True),
        patch.object(webex_connector, "start_cleanup_background_task"),
        patch.object(webex_connector, "disconnect_rabbitmq"),
        patch.object(webex_connector, "handle_message", return_value=True) as handle_mock,
    ):
        webex_connector.listen_to_queue()

    handle_mock.assert_called_once()
    channel.basic_ack.assert_called_once_with(delivery_tag="tag-1")
    channel.basic_nack.assert_not_called()


def test_issue_25_webex_shutdown_requeues_undispatched_message(webex_connector):
    message = {
        "personId": "person-1",
        "personEmail": "user@example.com",
        "roomId": "room-1",
        "text": "hello",
        "files": [],
    }
    callback_holder = {}
    channel = MagicMock()
    method = MagicMock(delivery_tag="tag-2")
    webex_connector.rabbitmq_channel = channel
    webex_connector.rabbitmq_connection = MagicMock(is_closed=False)
    webex_connector.running = True

    def fake_basic_consume(*, queue, on_message_callback):
        callback_holder["callback"] = on_message_callback

    def fake_start_consuming():
        callback_holder["callback"](
            channel, method, None, json.dumps(message).encode()
        )
        webex_connector.running = False

    channel.basic_consume.side_effect = fake_basic_consume
    channel.start_consuming.side_effect = fake_start_consuming

    with (
        patch.object(webex_connector, "connect_rabbitmq", return_value=True),
        patch.object(webex_connector, "start_cleanup_background_task"),
        patch.object(webex_connector, "disconnect_rabbitmq"),
        patch.object(webex_connector, "handle_message", return_value=False),
    ):
        webex_connector.listen_to_queue()

    channel.basic_ack.assert_not_called()
    channel.basic_nack.assert_called_once_with(delivery_tag="tag-2", requeue=True)


def test_issue_25_webex_shutdown_interrupts_reconnect_backoff(webex_connector):
    connect_started = threading.Event()

    def fake_connect():
        connect_started.set()
        return False

    with (
        patch.object(webex_connector, "connect_rabbitmq", side_effect=fake_connect),
        patch.object(webex_connector, "disconnect_rabbitmq"),
        patch.object(webex_connector, "start_cleanup_background_task"),
    ):
        worker = threading.Thread(target=webex_connector.listen_to_queue, daemon=True)
        worker.start()

        assert connect_started.wait(timeout=1)
        webex_connector._handle_shutdown_signal(signal.SIGTERM, None)

        worker.join(timeout=1)
        assert worker.is_alive() is False


def test_issue_25_webex_shutdown_interrupts_connection_establishment(
    webex_connector,
):
    connect_started = threading.Event()
    release_connect = threading.Event()

    def slow_blocking_connection(_parameters):
        connect_started.set()
        assert release_connect.wait(timeout=2)
        raise RuntimeError("connect aborted for test")

    with (
        patch(
            "webex_connector.pika.BlockingConnection",
            side_effect=slow_blocking_connection,
        ),
        patch.object(webex_connector, "disconnect_rabbitmq"),
        patch.object(webex_connector, "start_cleanup_background_task"),
    ):
        worker = threading.Thread(target=webex_connector.listen_to_queue, daemon=True)
        worker.start()

        assert connect_started.wait(timeout=1)
        webex_connector._handle_shutdown_signal(signal.SIGTERM, None)

        worker.join(timeout=1)
        assert worker.is_alive() is False

        release_connect.set()


def test_issue_25_webex_shutdown_waits_without_default_timeout(webex_connector):
    message = {
        "personId": "person-1",
        "personEmail": "user@example.com",
        "roomId": "room-1",
        "text": "hello",
        "files": [],
    }
    started = threading.Event()
    release = threading.Event()
    wait_started = threading.Event()
    wait_finished = threading.Event()

    def blocking_query(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=2)
        return ("done", None)

    def wait_for_shutdown_drain():
        wait_started.set()
        assert webex_connector._wait_for_active_requests("webex test") is True
        wait_finished.set()

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
        ),
        patch.object(webex_connector, "send_response"),
        patch.object(webex_connector, "send_message"),
    ):
        worker = threading.Thread(
            target=webex_connector.handle_message, args=(message,), daemon=True
        )
        worker.start()
        assert started.wait(timeout=1)

        webex_connector._handle_shutdown_signal(signal.SIGTERM, None)
        assert webex_connector.shutdown_timeout is None

        waiter = threading.Thread(target=wait_for_shutdown_drain, daemon=True)
        waiter.start()
        assert wait_started.wait(timeout=1)
        time.sleep(0.1)
        assert wait_finished.is_set() is False

        release.set()
        worker.join(timeout=1)
        waiter.join(timeout=1)
        assert waiter.is_alive() is False
        assert wait_finished.is_set() is True


def test_issue_25_webex_audio_transcription_failure_is_acked(webex_connector):
    message = {
        "personId": "person-1",
        "personEmail": "user@example.com",
        "roomId": "room-1",
        "text": "",
        "files": ["https://files.example/audio.wav"],
    }
    callback_holder = {}
    channel = MagicMock()
    method = MagicMock(delivery_tag="tag-audio")
    webex_connector.rabbitmq_channel = channel
    webex_connector.rabbitmq_connection = MagicMock(is_closed=False)
    webex_connector.running = True

    def fake_basic_consume(*, queue, on_message_callback):
        callback_holder["callback"] = on_message_callback

    def fake_start_consuming():
        callback_holder["callback"](
            channel, method, None, json.dumps(message).encode()
        )
        webex_connector.running = False

    channel.basic_consume.side_effect = fake_basic_consume
    channel.start_consuming.side_effect = fake_start_consuming

    with (
        patch.object(webex_connector, "connect_rabbitmq", return_value=True),
        patch.object(webex_connector, "start_cleanup_background_task"),
        patch.object(webex_connector, "disconnect_rabbitmq"),
        patch.object(
            webex_connector,
            "download_file",
            return_value=("/tmp/audio.wav", "audio.wav"),
        ),
        patch("webex_connector.audio_transcriber.is_audio_file", return_value=True),
        patch(
            "webex_connector.audio_transcriber.transcribe",
            return_value=(None, "mock-backend"),
        ),
        patch.object(webex_connector, "send_message") as send_message_mock,
    ):
        webex_connector.listen_to_queue()

    send_message_mock.assert_called_once_with(
        "room-1",
        "⚠️ Could not transcribe audio file. Please send as text instead.",
    )
    channel.basic_ack.assert_called_once_with(delivery_tag="tag-audio")
    channel.basic_nack.assert_not_called()
