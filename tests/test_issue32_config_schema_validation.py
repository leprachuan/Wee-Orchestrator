"""
Regression tests for Issue #32 — Config schema validation.

Verifies that agents.json, telegram_config.json, and webex_config.json are
validated via Pydantic on load, unknown keys produce warnings, and required
field violations raise ValidationError.
"""

import json
import os
import sys
import warnings
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import ValidationError  # noqa: E402

from config_schemas import (  # noqa: E402
    CONNECTOR_VALIDATORS,
    AgentsConfig,
    TelegramConfigSchema,
    WebEXConfigSchema,
    validate_agents_config,
    validate_telegram_config,
    validate_webex_config,
)

# ===========================================================================
# validate_agents_config
# ===========================================================================


class TestAgentsConfigSchema:
    """Tests for agents.json schema validation."""

    def _valid_config(self) -> dict:
        return {
            "agents": [
                {
                    "name": "orchestrator",
                    "description": "Main agent",
                    "path": "/opt/",
                }
            ]
        }

    def test_valid_config_passes(self):
        cfg = validate_agents_config(self._valid_config())
        assert isinstance(cfg, AgentsConfig)
        assert len(cfg.agents) == 1
        assert cfg.agents[0].name == "orchestrator"

    def test_missing_agents_key_raises(self):
        with pytest.raises(ValidationError):
            validate_agents_config({})

    def test_agents_must_be_list(self):
        with pytest.raises(ValidationError):
            validate_agents_config({"agents": "bad"})

    def test_missing_agent_name_raises(self):
        with pytest.raises(ValidationError):
            validate_agents_config({"agents": [{"path": "/opt/"}]})

    def test_unknown_top_level_key_warns(self):
        data = self._valid_config()
        data["typo_field"] = "oops"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_agents_config(data)
        messages = [str(w.message) for w in caught]
        assert any(
            "typo_field" in m for m in messages
        ), f"Expected warning about 'typo_field', got: {messages}"

    def test_unknown_agent_key_warns(self):
        data = self._valid_config()
        data["agents"][0]["typo_key"] = "value"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_agents_config(data)
        messages = [str(w.message) for w in caught]
        assert any(
            "typo_key" in m for m in messages
        ), f"Expected warning about 'typo_key', got: {messages}"

    def test_dispatch_config_unknown_key_warns(self):
        data = self._valid_config()
        data["agents"][0]["dispatch_config"] = {
            "runtime": "copilot",
            "typo_dispatch_key": "val",
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_agents_config(data)
        messages = [str(w.message) for w in caught]
        assert any(
            "typo_dispatch_key" in m for m in messages
        ), f"Expected warning about 'typo_dispatch_key', got: {messages}"

    def test_known_fields_no_spurious_warnings(self):
        data = {
            "agents": [
                {
                    "name": "wee-dev",
                    "description": "Dev agent",
                    "path": "/opt/wee-dev/",
                    "dispatch_config": {
                        "runtime": "copilot",
                        "model": "claude-sonnet-4.6",
                        "permission_mode": "elevated",
                        "yolo": True,
                        "timeout": 3600,
                    },
                }
            ]
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_agents_config(data)
        unknown_warnings = [
            w for w in caught if "unknown key" in str(w.message).lower()
        ]
        assert (
            not unknown_warnings
        ), f"Unexpected unknown-key warnings: {unknown_warnings}"

    def test_unknown_bots_channel_warns(self):
        """Typo in channel name under agents[].bots should produce a warning."""
        data = {
            "agents": [
                {
                    "name": "a",
                    "path": "/tmp",
                    "bots": {"telegrm": {"token_secret": "X"}},
                }
            ]
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_agents_config(data)
        messages = [str(w.message) for w in caught]
        assert any(
            "telegrm" in m for m in messages
        ), f"Expected warning about 'telegrm', got: {messages}"

    def test_unknown_telegram_bot_config_key_warns(self):
        """Typo in key inside bots.telegram should produce a warning."""
        data = {
            "agents": [
                {
                    "name": "a",
                    "path": "/tmp",
                    "bots": {"telegram": {"tokn_secret": "X"}},
                }
            ]
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_agents_config(data)
        messages = [str(w.message) for w in caught]
        assert any(
            "tokn_secret" in m for m in messages
        ), f"Expected warning about 'tokn_secret', got: {messages}"

    def test_unknown_webex_bot_config_key_warns(self):
        """Typo in key inside bots.webex should produce a warning."""
        data = {
            "agents": [
                {
                    "name": "a",
                    "path": "/tmp",
                    "bots": {"webex": {"allowd_users": ["foster"]}},
                }
            ]
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_agents_config(data)
        messages = [str(w.message) for w in caught]
        assert any(
            "allowd_users" in m for m in messages
        ), f"Expected warning about 'allowd_users', got: {messages}"

    def test_dispatch_config_fallback_fields_no_warnings(self):
        """fallback_runtime and fallback_model are known fields."""
        data = {
            "agents": [
                {
                    "name": "wee-dev",
                    "path": "/opt/wee-dev/",
                    "dispatch_config": {
                        "runtime": "copilot",
                        "fallback_runtime": "claude-sdk",
                        "fallback_model": "claude-haiku-4.5",
                    },
                }
            ]
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_agents_config(data)
        unknown_warnings = [
            w for w in caught if "unknown key" in str(w.message).lower()
        ]
        assert (
            not unknown_warnings
        ), f"Unexpected unknown-key warnings: {unknown_warnings}"


# ===========================================================================
# validate_telegram_config
# ===========================================================================


class TestTelegramConfigSchema:
    """Tests for telegram_config.json schema validation."""

    def _valid_config(self) -> dict:
        return {
            "token": "bot123:FAKE",
            "allowed_users": [8193231291],
            "user_pairings": {},
            "enable_auto_pair": False,
            "default_agent": "orchestrator",
            "default_model": "gpt-5-mini",
        }

    def test_valid_config_passes(self):
        cfg = validate_telegram_config(self._valid_config())
        assert isinstance(cfg, TelegramConfigSchema)
        assert cfg.token == "bot123:FAKE"
        assert cfg.default_agent == "orchestrator"

    def test_empty_config_uses_defaults(self):
        cfg = validate_telegram_config({})
        assert cfg.token == ""
        assert cfg.allowed_users == []
        assert cfg.enable_auto_pair is False

    def test_unknown_key_warns(self):
        data = self._valid_config()
        data["tok3n"] = "typo"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_telegram_config(data)
        messages = [str(w.message) for w in caught]
        assert any(
            "tok3n" in m for m in messages
        ), f"Expected warning about 'tok3n', got: {messages}"

    def test_no_spurious_warnings_for_valid_config(self):
        data = self._valid_config()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_telegram_config(data)
        unknown_warnings = [
            w for w in caught if "unknown key" in str(w.message).lower()
        ]
        assert (
            not unknown_warnings
        ), f"Unexpected unknown-key warnings: {unknown_warnings}"

    def test_returns_as_dict_via_model_dump(self):
        cfg = validate_telegram_config(self._valid_config())
        d = cfg.model_dump()
        assert isinstance(d, dict)
        assert "token" in d


# ===========================================================================
# validate_webex_config
# ===========================================================================


class TestWebEXConfigSchema:
    """Tests for webex_config.json schema validation."""

    def _valid_config(self) -> dict:
        return {
            "token": "bot_abc",
            "rabbitmq_host": "192.168.0.85",
            "rabbitmq_port": 5672,
            "rabbitmq_user": "admin",
            "rabbitmq_password": "secret",
            "rabbitmq_queue": "webex",
            "rabbitmq_vhost": "/",
            "allowed_users": [],
            "user_pairings": {},
            "enable_auto_pair": False,
            "default_agent": "orchestrator",
            "default_model": "gpt-5-mini",
        }

    def test_valid_config_passes(self):
        cfg = validate_webex_config(self._valid_config())
        assert isinstance(cfg, WebEXConfigSchema)
        assert cfg.rabbitmq_host == "192.168.0.85"
        assert cfg.rabbitmq_port == 5672

    def test_empty_config_uses_defaults(self):
        cfg = validate_webex_config({})
        assert cfg.rabbitmq_host == "192.168.0.85"
        assert cfg.rabbitmq_port == 5672

    def test_unknown_key_warns(self):
        data = self._valid_config()
        data["rabbitmq_h0st"] = "typo"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_webex_config(data)
        messages = [str(w.message) for w in caught]
        assert any(
            "rabbitmq_h0st" in m for m in messages
        ), f"Expected warning about 'rabbitmq_h0st', got: {messages}"

    def test_no_spurious_warnings_for_valid_config(self):
        data = self._valid_config()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_webex_config(data)
        unknown_warnings = [
            w for w in caught if "unknown key" in str(w.message).lower()
        ]
        assert (
            not unknown_warnings
        ), f"Unexpected unknown-key warnings: {unknown_warnings}"

    def test_returns_as_dict_via_model_dump(self):
        cfg = validate_webex_config(self._valid_config())
        d = cfg.model_dump()
        assert isinstance(d, dict)
        assert "token" in d
        assert "rabbitmq_host" in d


# ===========================================================================
# CONNECTOR_VALIDATORS registry
# ===========================================================================


class TestConnectorValidatorsRegistry:
    """Verify CONNECTOR_VALIDATORS maps the right keys to the right validators."""

    def test_telegram_validator_registered(self):
        assert "telegram_config.json" in CONNECTOR_VALIDATORS
        assert CONNECTOR_VALIDATORS["telegram_config.json"] is validate_telegram_config

    def test_webex_validator_registered(self):
        assert "webex_config.json" in CONNECTOR_VALIDATORS
        assert CONNECTOR_VALIDATORS["webex_config.json"] is validate_webex_config

    def test_telegram_validator_warns_on_unknown_key(self):
        data = {"token": "x", "unknwon_key": "oops"}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            CONNECTOR_VALIDATORS["telegram_config.json"](data)
        messages = [str(w.message) for w in caught]
        assert any("unknwon_key" in m for m in messages)

    def test_webex_validator_warns_on_unknown_key(self):
        data = {"token": "x", "rabbitmq_h0st": "oops"}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            CONNECTOR_VALIDATORS["webex_config.json"](data)
        messages = [str(w.message) for w in caught]
        assert any("rabbitmq_h0st" in m for m in messages)


# ===========================================================================
# Integration: BaseConfig._load_config invokes the registry validator
# ===========================================================================


class TestBaseConfigIntegration:
    """Verify BaseConfig._load_config calls schema validation via registry."""

    def test_telegram_config_load_warns_on_unknown_key(self, tmp_path):
        """BaseConfig._load_config should warn for unknown telegram_config.json keys."""
        cfg_data = {"token": "bot123:FAKE", "tok3n": "typo"}
        cfg_file = tmp_path / "telegram_config.json"
        cfg_file.write_text(json.dumps(cfg_data))

        import base_connector as bc

        class FakeTelegramConfig(bc.BaseConfig):
            def _default_config(self):
                return {}

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            FakeTelegramConfig(str(cfg_file))

        messages = [str(w.message) for w in caught]
        assert any(
            "tok3n" in m for m in messages
        ), f"Expected warning about 'tok3n', got: {messages}"

    def test_webex_config_load_warns_on_unknown_key(self, tmp_path):
        """BaseConfig._load_config should warn for unknown webex_config.json keys."""
        cfg_data = {"token": "x", "rabbitmq_h0st": "typo"}
        cfg_file = tmp_path / "webex_config.json"
        cfg_file.write_text(json.dumps(cfg_data))

        import base_connector as bc

        class FakeWebEXConfig(bc.BaseConfig):
            def _default_config(self):
                return {}

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            FakeWebEXConfig(str(cfg_file))

        messages = [str(w.message) for w in caught]
        assert any(
            "rabbitmq_h0st" in m for m in messages
        ), f"Expected warning about 'rabbitmq_h0st', got: {messages}"

    def test_unknown_config_file_no_warning(self, tmp_path):
        """Files not in CONNECTOR_VALIDATORS registry don't trigger schema checks."""
        cfg_data = {"some_key": "value", "another_key": "data"}
        cfg_file = tmp_path / "other_config.json"
        cfg_file.write_text(json.dumps(cfg_data))

        import base_connector as bc

        class FakeOtherConfig(bc.BaseConfig):
            def _default_config(self):
                return {}

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            FakeOtherConfig(str(cfg_file))

        unknown_warnings = [
            w for w in caught if "unknown key" in str(w.message).lower()
        ]
        assert (
            not unknown_warnings
        ), f"Expected no unknown-key warnings, got: {unknown_warnings}"


# ===========================================================================
# Integration: AgentManager._load_agents_config uses validation
# ===========================================================================


class TestAgentManagerIntegration:
    """Verify AgentManager._load_agents_config calls schema validation."""

    def test_unknown_agent_key_emits_warning(self, tmp_path):
        """AgentManager._load_agents_config should warn on unknown keys."""
        config = {
            "agents": [
                {
                    "name": "test-agent",
                    "path": "/opt/test/",
                    "typo_unknown_key": "bad_value",
                }
            ]
        }
        config_file = tmp_path / "agents.json"
        config_file.write_text(json.dumps(config))

        import agent_manager as am

        mgr = am.SessionManager.__new__(am.SessionManager)
        mgr._agents_config_path = config_file
        mgr._agents_json_mtime = 0.0

        import io

        captured = io.StringIO()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with patch("sys.stderr", captured):
                mgr._load_agents_config(str(config_file))

        messages = [str(w.message) for w in caught]
        has_warning = any("typo_unknown_key" in m for m in messages) or (
            "typo_unknown_key" in captured.getvalue()
        )
        assert has_warning, (
            f"Expected warning about unknown key, "
            f"warnings={messages!r}, stderr={captured.getvalue()!r}"
        )

    def test_valid_agents_config_loads_without_unknown_warnings(self, tmp_path):
        """AgentManager._load_agents_config should load cleanly."""
        config = {
            "agents": [
                {
                    "name": "orchestrator",
                    "description": "Main agent",
                    "path": "/opt/",
                }
            ]
        }
        config_file = tmp_path / "agents.json"
        config_file.write_text(json.dumps(config))

        import agent_manager as am

        mgr = am.SessionManager.__new__(am.SessionManager)
        mgr._agents_config_path = config_file
        mgr._agents_json_mtime = 0.0

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = mgr._load_agents_config(str(config_file))

        assert "orchestrator" in result
        unknown_warnings = [
            w for w in caught if "unknown key" in str(w.message).lower()
        ]
        assert (
            not unknown_warnings
        ), f"Expected no unknown-key warnings, got: {unknown_warnings}"


# ===========================================================================
# Regression: _load_agents_config rejects structurally invalid agents.json
# ===========================================================================


class TestAgentManagerInvalidSchemaRejected:
    """Regression: _load_agents_config must not load agents when schema validation fails.

    Issue #32 Round 5 — max_concurrent="oops" was previously loaded silently.
    """

    def test_invalid_max_concurrent_returns_empty_agents(self, tmp_path):
        """_load_agents_config returns {} when max_concurrent has wrong type."""
        config = {
            "agents": [
                {
                    "name": "bad-agent",
                    "path": "/opt/bad/",
                    "max_concurrent": "oops",  # must be int
                }
            ]
        }
        config_file = tmp_path / "agents.json"
        config_file.write_text(json.dumps(config))

        import agent_manager as am

        mgr = am.SessionManager.__new__(am.SessionManager)
        mgr._agents_config_path = config_file
        mgr._agents_json_mtime = 0.0

        result = mgr._load_agents_config(str(config_file))

        assert result == {}, (
            f"Expected empty dict when schema validation fails, got: {result!r}. "
            "Invalid agents.json must not be loaded into the live agent roster."
        )

    def test_invalid_schema_does_not_load_any_agents(self, tmp_path):
        """_load_agents_config rejects the entire config, not just the bad entry."""
        config = {
            "agents": [
                {
                    "name": "good-agent",
                    "path": "/opt/good/",
                },
                {
                    "name": "bad-agent",
                    "path": "/opt/bad/",
                    "max_concurrent": "oops",
                },
            ]
        }
        config_file = tmp_path / "agents.json"
        config_file.write_text(json.dumps(config))

        import agent_manager as am

        mgr = am.SessionManager.__new__(am.SessionManager)
        mgr._agents_config_path = config_file
        mgr._agents_json_mtime = 0.0

        result = mgr._load_agents_config(str(config_file))

        assert result == {}, (
            f"Expected empty dict for any schema failure, got: {result!r}. "
            "A single invalid entry must prevent loading the entire file."
        )

    def test_valid_config_still_loads_normally(self, tmp_path):
        """_load_agents_config loads valid config without regression."""
        config = {
            "agents": [
                {
                    "name": "good-agent",
                    "path": "/opt/good/",
                    "max_concurrent": 2,
                }
            ]
        }
        config_file = tmp_path / "agents.json"
        config_file.write_text(json.dumps(config))

        import agent_manager as am

        mgr = am.SessionManager.__new__(am.SessionManager)
        mgr._agents_config_path = config_file
        mgr._agents_json_mtime = 0.0

        result = mgr._load_agents_config(str(config_file))

        assert "good-agent" in result, f"Valid config should load cleanly, got: {result!r}"
        assert result["good-agent"]["max_concurrent"] == 2


# ===========================================================================
# Regression: BaseConfig._load_config surfaces connector schema failures
# ===========================================================================


class TestBaseConfigSchemaValidationSurfaced:
    """Regression: BaseConfig._load_config must not swallow ValidationError.

    Issue #32 Round 5 — allowed_users="not-a-list" was previously silently
    defaulted instead of surfacing the schema failure.
    """

    def test_telegram_invalid_allowed_users_raises(self, tmp_path):
        """_load_config raises ValidationError when allowed_users is not a list."""
        cfg_data = {"token": "bot123:FAKE", "allowed_users": "not-a-list"}
        cfg_file = tmp_path / "telegram_config.json"
        cfg_file.write_text(json.dumps(cfg_data))

        import base_connector as bc

        class FakeTelegramConfig(bc.BaseConfig):
            def _default_config(self):
                return {"defaulted": True}

        with pytest.raises(ValidationError):
            FakeTelegramConfig(str(cfg_file))

    def test_webex_invalid_allowed_users_raises(self, tmp_path):
        """_load_config raises ValidationError when webex allowed_users is not a list."""
        cfg_data = {"token": "webex-token", "allowed_users": "not-a-list"}
        cfg_file = tmp_path / "webex_config.json"
        cfg_file.write_text(json.dumps(cfg_data))

        import base_connector as bc

        class FakeWebEXConfig(bc.BaseConfig):
            def _default_config(self):
                return {"defaulted": True}

        with pytest.raises(ValidationError):
            FakeWebEXConfig(str(cfg_file))

    def test_invalid_connector_config_does_not_return_defaults(self, tmp_path):
        """_load_config raises rather than silently returning default config."""
        cfg_data = {"token": "bot123:FAKE", "allowed_users": 12345}  # must be list
        cfg_file = tmp_path / "telegram_config.json"
        cfg_file.write_text(json.dumps(cfg_data))

        import base_connector as bc

        class FakeTelegramConfig(bc.BaseConfig):
            def _default_config(self):
                return {"defaulted": True}

        caught_exc = None
        result = None
        try:
            instance = FakeTelegramConfig(str(cfg_file))
            result = instance.config
        except Exception as e:
            caught_exc = e

        assert caught_exc is not None, (
            f"Expected an exception to propagate, but got config: {result!r}. "
            "Schema validation failure must not be silently swallowed."
        )
        assert isinstance(caught_exc, ValidationError), (
            f"Expected ValidationError, got {type(caught_exc).__name__}: {caught_exc}"
        )

    def test_json_parse_error_still_returns_defaults(self, tmp_path):
        """I/O errors and JSON parse errors still fall back to defaults (unchanged)."""
        cfg_file = tmp_path / "telegram_config.json"
        cfg_file.write_text("{{invalid json")

        import base_connector as bc

        class FakeTelegramConfig(bc.BaseConfig):
            def _default_config(self):
                return {"defaulted": True}

        instance = FakeTelegramConfig(str(cfg_file))
        assert instance.config == {"defaulted": True}, (
            "JSON parse errors should still fall back to _default_config()"
        )
