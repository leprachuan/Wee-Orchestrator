#!/usr/bin/env python3
"""Regression tests for issue #30: BaseConnector extraction.

Verifies that:
1. BaseConfig shared methods work correctly
2. BaseConnector shared infrastructure methods work correctly
3. TelegramConfig/WebEXConfig correctly inherit from BaseConfig
4. TelegramConnector/WebEXConnector correctly inherit from BaseConnector
5. Platform-specific overrides (_safe_file_dirs, _max_file_bytes, etc.) are correct
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure dev directory is in path
sys.path.insert(0, "/opt/n8n-copilot-shim-dev")
from base_connector import BaseConnector

# ── BaseConfig tests ─────────────────────────────────────────────────────────


@pytest.fixture
def base_config_class(tmp_path):
    """Return a concrete BaseConfig subclass pointing at tmp_path."""
    from base_connector import BaseConfig

    class ConcreteConfig(BaseConfig):
        def _default_config(self):
            return {
                "allowed_users": [],
                "user_pairings": {},
                "pinned_users": {},
                "yolo_allowed_users": [],
            }

    config_path = tmp_path / "test_config.json"
    return ConcreteConfig(str(config_path))


def test_base_config_default_on_missing_file(tmp_path):
    """BaseConfig creates default config when file is absent."""
    from base_connector import BaseConfig

    class ConcreteConfig(BaseConfig):
        def _default_config(self):
            return {
                "allowed_users": [],
                "user_pairings": {},
                "pinned_users": {},
                "yolo_allowed_users": [],
            }

    cfg = ConcreteConfig(str(tmp_path / "missing.json"))
    assert cfg.config == cfg._default_config()


def test_base_config_save_and_reload(tmp_path):
    """BaseConfig saves to file and reloads correctly."""
    from base_connector import BaseConfig

    class ConcreteConfig(BaseConfig):
        def _default_config(self):
            return {
                "allowed_users": [],
                "user_pairings": {},
                "pinned_users": {},
                "yolo_allowed_users": [],
            }

    cfg_path = str(tmp_path / "config.json")
    cfg = ConcreteConfig(cfg_path)
    cfg.config["allowed_users"] = [100]
    cfg.save()

    cfg2 = ConcreteConfig(cfg_path)
    assert cfg2.config["allowed_users"] == [100]


def test_base_config_is_user_allowed_empty(base_config_class):
    """Empty allowed_users means everyone is allowed."""
    cfg = base_config_class
    assert cfg.is_user_allowed(999) is True


def test_base_config_is_user_allowed_restricted(base_config_class):
    """Only listed users are allowed when list is non-empty."""
    cfg = base_config_class
    cfg.config["allowed_users"] = [1, 2]
    assert cfg.is_user_allowed(1) is True
    assert cfg.is_user_allowed(3) is False


def test_base_config_user_session_roundtrip(base_config_class):
    """set_user_session and get_user_session round-trip correctly."""
    cfg = base_config_class
    session = {"agent": "orchestrator", "timeout": 300}
    cfg.set_user_session(42, session)
    assert cfg.get_user_session(42) == session
    # int user_id stored under str key
    assert "42" in cfg.config["user_pairings"]


def test_base_config_user_session_roundtrip_str_id(base_config_class):
    """String user IDs (WebEX style) are also handled correctly."""
    cfg = base_config_class
    session = {"agent": "orchestrator", "timeout": 300}
    cfg.set_user_session("webex_abc123", session)
    assert cfg.get_user_session("webex_abc123") == session


def test_base_config_allow_deny_user(base_config_class):
    """allow_user and deny_user update the allowed_users list."""
    cfg = base_config_class
    # Allow two users so deny_user leaves a non-empty list (restricted mode)
    cfg.allow_user(5)
    cfg.allow_user(10)
    assert cfg.is_user_allowed(5) is True
    assert cfg.is_user_allowed(10) is True
    cfg.deny_user(5)
    # After denial: list is [10] (non-empty, restricted mode) → 5 not allowed
    assert cfg.is_user_allowed(5) is False
    assert cfg.is_user_allowed(10) is True


def test_base_config_timeout_clamp(base_config_class):
    """set_user_timeout clamps values to 30–3600."""
    cfg = base_config_class
    cfg.set_user_session(1, {"timeout": 300})
    cfg.set_user_timeout(1, 5)
    assert cfg.get_user_timeout(1) == 30
    cfg.set_user_timeout(1, 99999)
    assert cfg.get_user_timeout(1) == 3600


def test_base_config_pinned_user(base_config_class):
    """is_user_pinned / get_pinned_* return correct values."""
    cfg = base_config_class
    assert cfg.is_user_pinned(7) is False
    cfg.config["pinned_users"]["7"] = {
        "agent": "email_triage",
        "runtime": "copilot",
        "model": "haiku",
    }
    assert cfg.is_user_pinned(7) is True
    assert cfg.get_pinned_agent(7) == "email_triage"
    assert cfg.get_pinned_runtime(7) == "copilot"
    assert cfg.get_pinned_model(7) == "haiku"


def test_base_config_yolo_allowed_empty_means_all(base_config_class):
    """Empty yolo_allowed_users means all users are permitted."""
    cfg = base_config_class
    assert cfg.is_yolo_allowed(999) is True


def test_base_config_yolo_allowed_restricted(base_config_class):
    """When yolo_allowed_users is set, only listed users can enable yolo."""
    cfg = base_config_class
    cfg.config["yolo_allowed_users"] = [1]
    assert cfg.is_yolo_allowed(1) is True
    assert cfg.is_yolo_allowed(2) is False


# ── BaseConnector infrastructure tests ──────────────────────────────────────


@pytest.fixture
def concrete_connector():
    """Minimal concrete BaseConnector subclass for testing shared methods."""

    class TestConfig:
        def is_user_pinned(self, uid):
            return False

    class ConcreteConnector(BaseConnector):
        connector_name = "Test connector"
        channel_name = "test"

        def __init__(self):
            self.config = TestConfig()
            self._init_shared_state()

        @property
        def _copilot_api_url(self):
            return "http://localhost:9999"

        def _make_session_id(self, user_id):
            return f"test_{user_id}"

        def _get_user_identity(self, user_id):
            return str(user_id)

        def _send_channel_status(self, channel_id, text):
            return None

        def _edit_channel_status(self, channel_id, msg_id, text):
            pass

        def _send_channel_typing(self, channel_id):
            pass

    return ConcreteConnector()


def test_base_connector_init_shared_state(concrete_connector):
    """_init_shared_state sets up correct initial state."""
    c = concrete_connector
    assert c.session_managers == {}
    assert c.running is False
    assert c.shutdown_event is not None
    assert not c.shutdown_event.is_set()
    assert c._active_requests == 0
    assert c._active_requests_drained.is_set()


def test_base_connector_request_shutdown(concrete_connector):
    """_request_shutdown sets shutdown_event and clears running."""
    c = concrete_connector
    c.running = True
    c._request_shutdown("test")
    assert c.shutdown_event.is_set()
    assert c.running is False


def test_base_connector_begin_finish_active_request(concrete_connector):
    """_begin/_finish_active_request track in-flight request count."""
    c = concrete_connector
    assert c._begin_active_request() is True
    assert c._active_requests == 1
    assert not c._active_requests_drained.is_set()
    c._finish_active_request()
    assert c._active_requests == 0
    assert c._active_requests_drained.is_set()


def test_base_connector_begin_returns_false_after_shutdown(concrete_connector):
    """_begin_active_request returns False after shutdown is requested."""
    c = concrete_connector
    c._request_shutdown("test")
    assert c._begin_active_request() is False


def test_base_connector_wait_for_active_requests_no_requests(concrete_connector):
    """_wait_for_active_requests returns True immediately when no active requests."""
    c = concrete_connector
    assert c._wait_for_active_requests() is True


def test_base_connector_evict_session_manager(concrete_connector):
    """_evict_session_manager removes the cached manager."""
    c = concrete_connector
    c.session_managers["sid1"] = MagicMock()
    c._evict_session_manager("sid1")
    assert "sid1" not in c.session_managers


# ── _is_safe_file_path tests ─────────────────────────────────────────────────


def test_is_safe_file_path_nonexistent(concrete_connector, tmp_path):
    """Non-existent file fails the safety check."""
    assert concrete_connector._is_safe_file_path(str(tmp_path / "no_such.txt")) is False


def test_is_safe_file_path_outside_allowed_dir(concrete_connector, tmp_path):
    """File outside allowed dirs fails."""
    f = tmp_path / "secret.txt"
    f.write_text("data")
    assert concrete_connector._is_safe_file_path(str(f)) is False


def test_is_safe_file_path_allowed_dir(concrete_connector, tmp_path, monkeypatch):
    """File inside an allowed dir passes."""
    f = tmp_path / "image.png"
    f.write_bytes(b"PNG")

    # Monkeypatch _safe_file_dirs to include tmp_path
    monkeypatch.setattr(
        type(concrete_connector),
        "_safe_file_dirs",
        property(lambda self: [tmp_path.resolve()]),
    )
    assert concrete_connector._is_safe_file_path(str(f)) is True


def test_is_safe_file_path_too_large(concrete_connector, tmp_path, monkeypatch):
    """File exceeding size limit fails."""
    f = tmp_path / "bigfile.bin"
    f.write_bytes(b"x" * 10)

    monkeypatch.setattr(
        type(concrete_connector),
        "_safe_file_dirs",
        property(lambda self: [tmp_path.resolve()]),
    )
    monkeypatch.setattr(
        type(concrete_connector), "_max_file_bytes", property(lambda self: 5)
    )
    assert concrete_connector._is_safe_file_path(str(f)) is False


# ── extract_image_urls tests ─────────────────────────────────────────────────


def test_extract_image_urls_markdown(concrete_connector):
    """Markdown image syntax is extracted with caption."""
    text = "Here is ![a cat](https://example.com/cat.png) and text."
    images, remaining = concrete_connector.extract_image_urls(text)
    assert len(images) == 1
    assert images[0][0] == "https://example.com/cat.png"
    assert images[0][1] == "a cat"
    assert "![a cat]" not in remaining


def test_extract_image_urls_bare_url(concrete_connector):
    """Bare image URLs are extracted without caption."""
    text = "See https://example.com/photo.jpg for details."
    images, remaining = concrete_connector.extract_image_urls(text)
    assert len(images) == 1
    assert images[0][0] == "https://example.com/photo.jpg"
    assert images[0][1] == ""


def test_extract_image_urls_no_duplicates(concrete_connector):
    """Duplicate URLs are deduplicated."""
    url = "https://example.com/cat.png"
    text = f"![alt1]({url})\n![alt2]({url})"
    images, _ = concrete_connector.extract_image_urls(text)
    assert len(images) == 1


def test_extract_image_urls_no_images(concrete_connector):
    """Text without images returns empty list and unchanged text."""
    text = "Plain text with no images."
    images, remaining = concrete_connector.extract_image_urls(text)
    assert images == []
    assert remaining == text


# ── extract_file_paths tests ─────────────────────────────────────────────────


def test_extract_file_paths_basic(concrete_connector, tmp_path, monkeypatch):
    """[FILE:...] marker is parsed and file returned if path is safe."""
    f = tmp_path / "report.pdf"
    f.write_bytes(b"PDF")

    monkeypatch.setattr(
        type(concrete_connector),
        "_safe_file_dirs",
        property(lambda self: [tmp_path.resolve()]),
    )

    text = f"Here is the report [FILE:{f}:Report PDF]."
    files, remaining = concrete_connector.extract_file_paths(text)
    assert len(files) == 1
    assert files[0][0] == str(f)
    assert files[0][1] == "Report PDF"
    assert "[FILE:" not in remaining


def test_extract_file_paths_unsafe_rejected(concrete_connector):
    """Unsafe paths are rejected (marker stays in text)."""
    text = "[FILE:/etc/passwd:sensitive]"
    files, remaining = concrete_connector.extract_file_paths(text)
    assert files == []


# ── _execute_command tests ────────────────────────────────────────────────────


def test_execute_command_returns_response(concrete_connector):
    """_execute_command returns the command response via direct mode."""
    mock_mgr = MagicMock()
    mock_mgr.execute.return_value = "Command executed successfully"
    concrete_connector.session_managers["test_42"] = mock_mgr
    concrete_connector.use_api = False

    result = concrete_connector._execute_command("/status", "test_42", timeout=5)
    assert result == "Command executed successfully"


# ── Platform-specific override tests ─────────────────────────────────────────


def test_telegram_safe_file_dirs(tmp_path):
    """TelegramConnector._safe_file_dirs includes telegram_downloads."""
    with patch("telegram_connector.requests") as mock_requests:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"id": 12345, "is_bot": True},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        config_path = tmp_path / "telegram_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "token": "fake",
                    "allowed_users": [],
                    "user_pairings": {},
                    "enable_auto_pair": False,
                    "default_agent": "orchestrator",
                    "default_model": "gpt-5-mini",
                }
            )
        )

        from telegram_connector import TelegramConnector

        conn = TelegramConnector("fake_token", config_file=str(config_path))

    dirs = conn._safe_file_dirs
    dir_strs = [str(d) for d in dirs]
    assert any("telegram_downloads" in d for d in dir_strs)
    assert conn._max_file_bytes == 50 * 1024 * 1024


def test_webex_safe_file_dirs(tmp_path):
    """WebEXConnector._safe_file_dirs includes webex_downloads and limit is 100MB."""
    config_path = tmp_path / "webex_config.json"
    config_path.write_text(
        json.dumps(
            {
                "token": "fake",
                "allowed_users": [],
                "user_pairings": {},
                "enable_auto_pair": False,
                "default_agent": "orchestrator",
                "default_model": "gpt-5-mini",
                "rabbitmq_host": "localhost",
                "rabbitmq_port": 5672,
                "rabbitmq_user": "admin",
                "rabbitmq_password": "",
                "rabbitmq_queue": "test",
                "rabbitmq_vhost": "/",
            }
        )
    )

    from webex_connector import WebEXConnector

    conn = WebEXConnector("fake_token", config_file=str(config_path))

    dirs = conn._safe_file_dirs
    dir_strs = [str(d) for d in dirs]
    assert any("webex_downloads" in d for d in dir_strs)
    assert conn._max_file_bytes == 100 * 1024 * 1024


def test_telegram_make_session_id_with_bot_id(tmp_path):
    """TelegramConnector._make_session_id includes bot_id when available."""
    with patch("telegram_connector.requests") as mock_requests:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"id": 99, "is_bot": True},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        config_path = tmp_path / "telegram_config.json"
        config_path.write_text(
            json.dumps({"token": "fake", "allowed_users": [], "user_pairings": {}})
        )

        from telegram_connector import TelegramConnector

        conn = TelegramConnector("fake_token", config_file=str(config_path))

    assert conn._make_session_id(42) == "telegram_99_42"


def test_telegram_make_session_id_without_bot_id(tmp_path):
    """TelegramConnector._make_session_id falls back when bot_id is None."""
    with patch("telegram_connector.requests") as mock_requests:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False}
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        config_path = tmp_path / "telegram_config.json"
        config_path.write_text(
            json.dumps({"token": "fake", "allowed_users": [], "user_pairings": {}})
        )

        from telegram_connector import TelegramConnector

        conn = TelegramConnector("fake_token", config_file=str(config_path))

    assert conn._make_session_id(42) == "telegram_42"


def test_webex_make_session_id(tmp_path):
    """WebEXConnector._make_session_id prefixes webex_."""
    config_path = tmp_path / "webex_config.json"
    config_path.write_text(
        json.dumps(
            {
                "token": "fake",
                "allowed_users": [],
                "user_pairings": {},
                "rabbitmq_host": "localhost",
                "rabbitmq_port": 5672,
                "rabbitmq_user": "u",
                "rabbitmq_password": "",
                "rabbitmq_queue": "q",
                "rabbitmq_vhost": "/",
            }
        )
    )
    from webex_connector import WebEXConnector

    conn = WebEXConnector("fake_token", config_file=str(config_path))
    assert conn._make_session_id("person123") == "webex_person123"


def test_telegram_config_inherits_base(tmp_path):
    """TelegramConfig inherits from BaseConfig."""
    from base_connector import BaseConfig
    from telegram_connector import TelegramConfig

    config_path = tmp_path / "t.json"
    cfg = TelegramConfig(str(config_path))
    assert isinstance(cfg, BaseConfig)


def test_webex_config_inherits_base(tmp_path):
    """WebEXConfig inherits from BaseConfig."""
    from base_connector import BaseConfig
    from webex_connector import WebEXConfig

    config_path = tmp_path / "w.json"
    cfg = WebEXConfig(str(config_path))
    assert isinstance(cfg, BaseConfig)


def test_telegram_connector_inherits_base(tmp_path):
    """TelegramConnector inherits from BaseConnector."""

    with patch("telegram_connector.requests") as mock_requests:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"id": 1, "is_bot": True},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        config_path = tmp_path / "t.json"
        config_path.write_text(
            json.dumps({"token": "fake", "allowed_users": [], "user_pairings": {}})
        )

        from telegram_connector import TelegramConnector

        conn = TelegramConnector("fake_token", config_file=str(config_path))

    assert isinstance(conn, BaseConnector)


def test_webex_connector_inherits_base(tmp_path):
    """WebEXConnector inherits from BaseConnector."""

    config_path = tmp_path / "w.json"
    config_path.write_text(
        json.dumps(
            {
                "token": "fake",
                "allowed_users": [],
                "user_pairings": {},
                "rabbitmq_host": "localhost",
                "rabbitmq_port": 5672,
                "rabbitmq_user": "u",
                "rabbitmq_password": "",
                "rabbitmq_queue": "q",
                "rabbitmq_vhost": "/",
            }
        )
    )
    from webex_connector import WebEXConnector

    conn = WebEXConnector("fake_token", config_file=str(config_path))
    assert isinstance(conn, BaseConnector)


# ── API-mode execution tests ─────────────────────────────────────────────────


@pytest.fixture
def api_connector():
    """Concrete BaseConnector subclass that implements _execute_via_api."""

    class TestConfig:
        def is_user_pinned(self, uid):
            return False

    class ApiConnector(BaseConnector):
        connector_name = "API Test connector"
        channel_name = "test"

        def __init__(self):
            self.config = TestConfig()
            self._init_shared_state()
            self._api_calls = []
            self._status_calls = []
            self._typing_calls = []

        @property
        def _copilot_api_url(self):
            return "http://localhost:9999"

        def _make_session_id(self, user_id):
            return f"api_test_{user_id}"

        def _get_user_identity(self, user_id):
            return str(user_id)

        def _execute_via_api(self, query, session_id, identity, channel):
            self._api_calls.append((query, session_id, identity, channel))
            return "API response"

        def _send_channel_status(self, channel_id, text):
            self._status_calls.append(text)
            return "msg_123"

        def _edit_channel_status(self, channel_id, msg_id, text):
            pass

        def _send_channel_typing(self, channel_id):
            self._typing_calls.append(channel_id)

    return ApiConnector()


def test_execute_via_api_raises_not_implemented():
    """BaseConnector._execute_via_api raises NotImplementedError."""

    class MinimalConnector(BaseConnector):
        connector_name = "minimal"
        channel_name = "min"

        def __init__(self):
            class Cfg:
                def is_user_pinned(self, u):
                    return False

            self.config = Cfg()
            self._init_shared_state()

        @property
        def _copilot_api_url(self):
            return "http://localhost"

        def _make_session_id(self, u):
            return f"min_{u}"

        def _get_user_identity(self, u):
            return str(u)

        def _send_channel_status(self, c, t):
            return None

        def _edit_channel_status(self, c, m, t):
            pass

        def _send_channel_typing(self, c):
            pass

    conn = MinimalConnector()
    with pytest.raises(NotImplementedError):
        conn._execute_via_api("q", "s", "i", "c")


def test_execute_command_api_mode(api_connector):
    """_execute_command delegates to _execute_via_api when use_api is True."""
    api_connector.use_api = True
    result = api_connector._execute_command("/status", "api_test_1", timeout=10)
    assert result == "API response"
    assert len(api_connector._api_calls) == 1
    query, session_id, identity, channel = api_connector._api_calls[0]
    assert query == "/status"
    assert session_id == "api_test_1"
    assert channel == "test"


# ── _poll_live_status tests ──────────────────────────────────────────────────


def test_poll_live_status_returns_status(api_connector):
    """_poll_live_status returns the status text on a successful 200 response."""
    with patch("base_connector.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "Thinking..."}
        mock_get.return_value = mock_resp
        status = api_connector._poll_live_status("session_abc")
    assert status == "Thinking..."


def test_poll_live_status_returns_none_on_non_200(api_connector):
    """_poll_live_status returns None for non-200 HTTP responses."""
    with patch("base_connector.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        status = api_connector._poll_live_status("session_abc")
    assert status is None


def test_poll_live_status_returns_none_on_exception(api_connector):
    """_poll_live_status returns None when the request raises an exception."""
    with patch("base_connector.requests.get", side_effect=Exception("timeout")):
        status = api_connector._poll_live_status("session_abc")
    assert status is None


def test_poll_live_status_returns_none_when_key_absent(api_connector):
    """_poll_live_status returns None when the response JSON has no 'status' key."""
    with patch("base_connector.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp
        status = api_connector._poll_live_status("session_abc")
    assert status is None


# ── _query_agent API mode tests ──────────────────────────────────────────────


def test_query_agent_api_mode(api_connector):
    """_query_agent routes through _execute_via_api when use_api is True."""
    api_connector.use_api = True
    result = api_connector._query_agent(
        "What is the weather?", "fosterbot", "gpt-5-mini", user_id=42
    )
    assert result == "API response"
    assert len(api_connector._api_calls) == 1
    query, session_id, identity, channel = api_connector._api_calls[0]
    assert query == "What is the weather?"
    assert session_id == "api_test_42"
    assert channel == "test"


# ── _query_agent_with_status tests ──────────────────────────────────────────


def test_query_agent_with_status_fast_response(api_connector):
    """_query_agent_with_status returns (response, status_msg_id) on fast completion."""
    api_connector.use_api = True
    with patch.object(api_connector, "_query_agent", return_value="Fast result"):
        response, status_msg_id = api_connector._query_agent_with_status(
            "fast query", "fosterbot", "gpt-5-mini", user_id=42, channel_id="ch_1"
        )
    assert response == "Fast result"


def test_query_agent_with_status_returns_no_status_on_fast_response(api_connector):
    """No status message is sent when the query completes before the 30s threshold."""
    api_connector.use_api = True
    with patch.object(api_connector, "_query_agent", return_value="Quick"):
        _, status_msg_id = api_connector._query_agent_with_status(
            "quick", "fosterbot", "gpt-5-mini", user_id=1, channel_id="ch_2"
        )
    # Fast query completes before 30s; no status message should have been sent
    assert status_msg_id is None
    assert len(api_connector._status_calls) == 0


# ── TelegramConnector delegation tests ─────────────────────────────────────────


def test_telegram_connector_delegates_execute_command_to_base():
    """TelegramConnector._execute_command is inherited from BaseConnector."""
    from telegram_connector import TelegramConnector

    # Verify the method exists and is from BaseConnector
    assert hasattr(TelegramConnector, "_execute_command")
    assert "_execute_command" in BaseConnector.__dict__

    # Verify TelegramConnector does NOT override it (it should be inherited)
    assert "_execute_command" not in TelegramConnector.__dict__

    # Create instance and verify method is callable
    connector = TelegramConnector.__new__(TelegramConnector)
    assert callable(getattr(connector, "_execute_command"))


def test_telegram_connector_delegates_query_agent_to_base():
    """TelegramConnector._query_agent is inherited from BaseConnector."""
    from telegram_connector import TelegramConnector

    # Verify the method exists and is from BaseConnector
    assert hasattr(TelegramConnector, "_query_agent")
    assert "_query_agent" in BaseConnector.__dict__

    # Verify TelegramConnector does NOT override it
    assert "_query_agent" not in TelegramConnector.__dict__

    connector = TelegramConnector.__new__(TelegramConnector)
    assert callable(getattr(connector, "_query_agent"))


def test_telegram_connector_delegates_query_agent_with_status_to_base():
    """TelegramConnector._query_agent_with_status is inherited from BaseConnector."""
    from telegram_connector import TelegramConnector

    # Verify the method exists and is from BaseConnector
    assert hasattr(TelegramConnector, "_query_agent_with_status")
    assert "_query_agent_with_status" in BaseConnector.__dict__

    # Verify TelegramConnector does NOT override it
    assert "_query_agent_with_status" not in TelegramConnector.__dict__

    connector = TelegramConnector.__new__(TelegramConnector)
    assert callable(getattr(connector, "_query_agent_with_status"))


# ── WebEXConnector delegation tests ────────────────────────────────────────────


def test_webex_connector_delegates_execute_command_to_base():
    """WebEXConnector._execute_command is inherited from BaseConnector."""
    from webex_connector import WebEXConnector

    # Verify the method exists and is from BaseConnector
    assert hasattr(WebEXConnector, "_execute_command")
    assert "_execute_command" in BaseConnector.__dict__

    # Verify WebEXConnector does NOT override it (it should be inherited)
    assert "_execute_command" not in WebEXConnector.__dict__

    # Create instance and verify method is callable
    connector = WebEXConnector.__new__(WebEXConnector)
    assert callable(getattr(connector, "_execute_command"))


def test_webex_connector_delegates_query_agent_to_base():
    """WebEXConnector._query_agent is inherited from BaseConnector."""
    from webex_connector import WebEXConnector

    # Verify the method exists and is from BaseConnector
    assert hasattr(WebEXConnector, "_query_agent")
    assert "_query_agent" in BaseConnector.__dict__

    # Verify WebEXConnector does NOT override it
    assert "_query_agent" not in WebEXConnector.__dict__

    connector = WebEXConnector.__new__(WebEXConnector)
    assert callable(getattr(connector, "_query_agent"))


def test_webex_connector_delegates_query_agent_with_status_to_base():
    """WebEXConnector._query_agent_with_status is inherited from BaseConnector."""
    from webex_connector import WebEXConnector

    # Verify the method exists and is from BaseConnector
    assert hasattr(WebEXConnector, "_query_agent_with_status")
    assert "_query_agent_with_status" in BaseConnector.__dict__

    # Verify WebEXConnector does NOT override it
    assert "_query_agent_with_status" not in WebEXConnector.__dict__

    connector = WebEXConnector.__new__(WebEXConnector)
    assert callable(getattr(connector, "_query_agent_with_status"))


# -- Regression: Telegram API-mode session lifecycle (issue #30) ---------------


def test_telegram_execute_via_api_passes_session_id_on_create():
    """Regression: _execute_via_api must pass session_id when creating the session.

    Previously, telegram_connector._execute_via_api called POST /api/v1/sessions/create
    with json={} so the server minted a random UUID.  The subsequent POST
    /api/v1/sessions/<session_id>/execute returned 404 because the deterministic
    session_id was never registered.
    """
    import requests as req_mod
    from unittest.mock import MagicMock, patch, call

    with patch.dict(
        "sys.modules",
        {"telegram": MagicMock(), "telegram.ext": MagicMock()},
    ):
        from telegram_connector import TelegramConnector

        connector = TelegramConnector.__new__(TelegramConnector)
        connector.api_url_copilot = "https://127.0.0.1:8000"
        connector.api_shared_key = "testkey"
        connector.config = MagicMock()
        connector.config.get_user_timeout.return_value = 600

        session_id = "telegram_12345"

        create_resp = MagicMock()
        create_resp.status_code = 200
        create_resp.json.return_value = {"session_id": session_id}

        execute_resp = MagicMock()
        execute_resp.status_code = 200
        execute_resp.json.return_value = {"response": "hello from bot"}

        with patch.object(req_mod, "post", side_effect=[create_resp, execute_resp]) as mock_post:
            result = connector._execute_via_api(
                query="hello",
                session_id=session_id,
                user_identity="telegram_12345",
                channel="telegram",
            )

        create_call = mock_post.call_args_list[0]
        assert create_call == call(
            f"{connector.api_url_copilot}/api/v1/sessions/create",
            headers={
                "Authorization": "Bearer shared_testkey",
                "Content-Type": "application/json",
                "X-User-Identity": "telegram_12345",
                "X-Auth-Channel": "telegram",
            },
            json={"session_id": session_id},
            timeout=10,
        ), f"Create call was missing session_id: {create_call}"

        execute_call = mock_post.call_args_list[1]
        assert f"/sessions/{session_id}/execute" in execute_call[0][0], (
            f"Execute URL did not reference expected session_id: {execute_call}"
        )

        assert result == "hello from bot"


def test_telegram_execute_via_api_session_id_not_empty_on_create():
    """Ensure the session_id forwarded to create is non-empty (regression guard)."""
    import requests as req_mod
    from unittest.mock import MagicMock, patch

    with patch.dict(
        "sys.modules",
        {"telegram": MagicMock(), "telegram.ext": MagicMock()},
    ):
        from telegram_connector import TelegramConnector

        connector = TelegramConnector.__new__(TelegramConnector)
        connector.api_url_copilot = "https://127.0.0.1:8000"
        connector.api_shared_key = "testkey"
        connector.config = MagicMock()
        connector.config.get_user_timeout.return_value = 600

        session_id = "telegram_99999"

        create_resp = MagicMock()
        create_resp.status_code = 200
        create_resp.json.return_value = {"session_id": session_id}

        execute_resp = MagicMock()
        execute_resp.status_code = 200
        execute_resp.json.return_value = {"response": "ok"}

        captured = {}

        def capturing_post(url, **kwargs):
            if "/sessions/create" in url:
                captured["create_json"] = kwargs.get("json", {})
                return create_resp
            return execute_resp

        with patch.object(req_mod, "post", side_effect=capturing_post):
            connector._execute_via_api(
                query="ping",
                session_id=session_id,
                user_identity="telegram_99999",
                channel="telegram",
            )

        payload = captured.get("create_json", {})
        assert "session_id" in payload, "session_id key missing from create payload"
        assert payload["session_id"], "session_id in create payload must be non-empty"
        assert payload["session_id"] == session_id, (
            f"session_id mismatch: expected {session_id}, got {payload['session_id']}"
        )
