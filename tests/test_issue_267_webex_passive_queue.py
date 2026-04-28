"""Regression test for Issue #267: webex_connector.py queue_declare() without passive flag.

On hosted AMQP brokers where the bot user only has CONSUME permission, calling
queue_declare() without passive=True raises ACCESS_REFUSED and kills the connection.

The fix adds a rabbitmq_queue_passive config flag. When True, queue_declare() is
called with passive=True (assert-only, no create attempt). Default is False to
preserve existing behavior.
"""

import json
import os
import sys
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_modules():
    """Inject lightweight stubs for heavy imports so webex_connector can load."""
    for mod in ("agent_manager", "audio_transcriber"):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()


_stub_modules()


def _make_config(queue_passive=None, tmp_path=None):
    """Write a minimal webex_config.json and return a WebEXConfig object."""
    from webex_connector import WebEXConfig

    cfg_data = {
        "token": "test-token",
        "rabbitmq_host": "localhost",
        "rabbitmq_port": 5672,
        "rabbitmq_user": "guest",
        "rabbitmq_password": "guest",
        "rabbitmq_queue": "webex",
        "rabbitmq_vhost": "/",
    }
    if queue_passive is not None:
        cfg_data["rabbitmq_queue_passive"] = queue_passive

    if tmp_path is None:
        tmp_dir = tempfile.mkdtemp()
        config_path = os.path.join(tmp_dir, "webex_config.json")
    else:
        config_path = os.path.join(str(tmp_path), "webex_config.json")

    with open(config_path, "w") as f:
        json.dump(cfg_data, f)

    return WebEXConfig(config_path)


def _make_connector(queue_passive=None):
    """Return a WebEXConnector with mocked config for queue_declare tests."""
    from webex_connector import WebEXConnector, WebEXConfig

    connector = WebEXConnector.__new__(WebEXConnector)
    connector.config = MagicMock(spec=WebEXConfig)
    cfg = {
        "rabbitmq_host": "localhost",
        "rabbitmq_host_ip": None,
        "rabbitmq_port": 5672,
        "rabbitmq_user": "guest",
        "rabbitmq_password": "guest",
        "rabbitmq_vhost": "/",
        "rabbitmq_queue": "webex",
        "rabbitmq_ssl": False,
        "rabbitmq_ssl_verify": True,
    }
    if queue_passive is not None:
        cfg["rabbitmq_queue_passive"] = queue_passive
    connector.config.config = cfg
    connector.shutdown_event = MagicMock()
    connector.shutdown_event.is_set.return_value = False
    connector.rabbitmq_connection = None
    connector.rabbitmq_channel = None
    return connector


def _run_connect(connector, mock_channel):
    """Invoke connect_rabbitmq() with a fully mocked pika stack.

    connect_rabbitmq() spawns a daemon thread that calls pika.BlockingConnection
    and then signals a threading.Event. We patch BlockingConnection so it returns
    a mock connection immediately.
    """
    import pika as real_pika  # noqa: F401 — we patch at module level below

    mock_connection = MagicMock()
    mock_connection.is_closed = False
    mock_connection.channel.return_value = mock_channel

    with patch("webex_connector.pika.BlockingConnection", return_value=mock_connection), \
         patch("webex_connector.pika.ConnectionParameters"), \
         patch("webex_connector.pika.PlainCredentials"), \
         patch("webex_connector.pika.SSLOptions"):
        connector.shutdown_event.wait.return_value = False
        result = connector.connect_rabbitmq()

    return result, mock_channel


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestIssue267PassiveQueueDeclare:
    """Verify queue_declare() uses passive flag according to config."""

    def test_default_config_uses_active_declare(self):
        """Without rabbitmq_queue_passive, queue_declare is called with durable=True."""
        connector = _make_connector()  # no passive key → .get() returns False
        mock_channel = MagicMock()

        result, ch = _run_connect(connector, mock_channel)

        assert result is True
        ch.queue_declare.assert_called_once_with(queue="webex", durable=True)

    def test_passive_false_uses_active_declare(self):
        """rabbitmq_queue_passive=False → queue_declare with durable=True."""
        connector = _make_connector(queue_passive=False)
        mock_channel = MagicMock()

        result, ch = _run_connect(connector, mock_channel)

        assert result is True
        ch.queue_declare.assert_called_once_with(queue="webex", durable=True)

    def test_passive_true_uses_passive_declare(self):
        """rabbitmq_queue_passive=True → queue_declare with passive=True only.

        This is the core fix for Issue #267: hosted brokers with CONSUME-only
        permissions reject any queue_declare that includes durable= or other
        config args. passive=True performs a read-only assertion.
        """
        connector = _make_connector(queue_passive=True)
        mock_channel = MagicMock()

        result, ch = _run_connect(connector, mock_channel)

        assert result is True
        ch.queue_declare.assert_called_once_with(queue="webex", passive=True)
        # Critically: durable must NOT be passed alongside passive
        _, kwargs = ch.queue_declare.call_args
        assert "durable" not in kwargs, (
            "durable must not be passed with passive=True — "
            "would raise ACCESS_REFUSED on a CONSUME-only broker"
        )

    def test_access_refused_without_passive_fails(self):
        """Active declare raises ACCESS_REFUSED → connect_rabbitmq returns False.

        This reproduces the pre-fix failure path: without passive=True, a
        CONSUME-only broker returns 403 ACCESS_REFUSED on queue_declare.
        """
        import pika.exceptions

        connector = _make_connector(queue_passive=False)
        mock_channel = MagicMock()
        mock_channel.queue_declare.side_effect = pika.exceptions.ChannelClosedByBroker(
            403,
            "ACCESS_REFUSED - access to queue 'webex' in vhost '/' refused for user 'bot'",
        )

        result, _ = _run_connect(connector, mock_channel)
        assert result is False, (
            "Connection must fail when queue_declare raises ACCESS_REFUSED"
        )

    def test_passive_declare_succeeds_on_consume_only_broker(self):
        """passive=True + no ACCESS_REFUSED → connect succeeds on CONSUME-only broker."""
        connector = _make_connector(queue_passive=True)
        mock_channel = MagicMock()
        mock_channel.queue_declare.return_value = MagicMock()  # passive declare OK

        result, ch = _run_connect(connector, mock_channel)

        assert result is True
        ch.queue_declare.assert_called_once_with(queue="webex", passive=True)


class TestIssue267DefaultConfigKey:
    """Verify rabbitmq_queue_passive is present in _default_config with safe default."""

    def test_default_config_has_passive_key_false(self, tmp_path):
        """_default_config must include rabbitmq_queue_passive defaulting to False.

        Default must be False to preserve existing behavior for operators who
        haven't set the flag explicitly.
        """
        from webex_connector import WebEXConfig

        config_file = tmp_path / "webex_config.json"
        config_file.write_text(json.dumps({}))  # empty — triggers _default_config

        cfg = WebEXConfig(str(config_file))

        assert "rabbitmq_queue_passive" in cfg.config, (
            "rabbitmq_queue_passive must be present in default config"
        )
        assert cfg.config["rabbitmq_queue_passive"] is False, (
            "Default must be False to preserve existing (non-passive) behavior"
        )

    def test_passive_key_persisted_from_file(self, tmp_path):
        """rabbitmq_queue_passive=true in config file is read and respected."""
        from webex_connector import WebEXConfig

        config_file = tmp_path / "webex_config.json"
        config_file.write_text(
            json.dumps(
                {
                    "token": "t",
                    "rabbitmq_queue_passive": True,
                }
            )
        )

        cfg = WebEXConfig(str(config_file))
        assert cfg.config.get("rabbitmq_queue_passive") is True
