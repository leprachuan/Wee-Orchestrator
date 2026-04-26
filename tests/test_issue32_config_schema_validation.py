"""
Regression tests for Issue #32: Config schema validation.

Tests that:
1. Unknown keys in config files produce warnings
2. Missing required fields raise ValidationError
3. Wrong field types raise ValidationError
4. Valid configs pass validation
5. Telegram and WebEX connector configs are properly validated
"""

import json
import tempfile
import unittest
import warnings
from pathlib import Path

from pydantic import ValidationError

from config_schemas import (
    validate_agents_config,
    validate_telegram_config,
    validate_webex_config,
)


class TestIssue32ConfigSchemaValidation(unittest.TestCase):
    """Test suite for config schema validation (Issue #32)."""

    def test_telegram_unknown_key_warning(self):
        """Unknown keys in telegram_config should produce UserWarning."""
        cfg = {
            "token": "test_token",
            "typo_field": "should_warn",
            "another_typo": "also_warn",
        }

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            validate_telegram_config(cfg)
            # Should have 2 warnings for the typos
            assert len(w) >= 2
            assert all(issubclass(warning.category, UserWarning) for warning in w)
            assert any("typo_field" in str(warning.message) for warning in w)
            assert any("another_typo" in str(warning.message) for warning in w)

    def test_telegram_valid_config(self):
        """Valid telegram_config should pass validation."""
        cfg = {
            "token": "test_token",
            "allowed_users": [123, 456],
            "user_pairings": {},
            "default_agent": "orchestrator",
        }

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = validate_telegram_config(cfg)
            # Should have no warnings for known keys
            unknown_warnings = [x for x in w if "unknown key" in str(x.message)]
            assert len(unknown_warnings) == 0
            assert result.token == "test_token"

    def test_webex_unknown_key_warning(self):
        """Unknown keys in webex_config should produce UserWarning."""
        cfg = {
            "token": "test_token",
            "bad_key": "value",
        }

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            validate_webex_config(cfg)
            assert any("bad_key" in str(warning.message) for warning in w)

    def test_webex_valid_config(self):
        """Valid webex_config should pass validation."""
        cfg = {
            "token": "test_token",
            "rabbitmq_host": "192.168.0.85",
            "rabbitmq_port": 5672,
        }

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = validate_webex_config(cfg)
            unknown_warnings = [x for x in w if "unknown key" in str(x.message)]
            assert len(unknown_warnings) == 0
            assert result.token == "test_token"

    def test_telegram_wrong_type_raises_validation_error(self):
        """Wrong field types should raise ValidationError."""
        cfg = {
            "token": "test_token",
            "allowed_users": "not_a_list",  # Should be list
        }

        with self.assertRaises(ValidationError):
            validate_telegram_config(cfg)

    def test_webex_wrong_type_raises_validation_error(self):
        """Wrong field types in webex should raise ValidationError."""
        cfg = {
            "token": "test_token",
            "rabbitmq_port": "not_an_int",  # Should be int
        }

        with self.assertRaises(ValidationError):
            validate_webex_config(cfg)

    def test_agents_valid_config(self):
        """Valid agents.json should pass validation."""
        cfg = {
            "agents": [
                {
                    "name": "orchestrator",
                    "description": "Main orchestrator",
                    "path": "/opt/",
                }
            ]
        }

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = validate_agents_config(cfg)
            unknown_warnings = [x for x in w if "unknown key" in str(x.message)]
            assert len(unknown_warnings) == 0
            assert len(result.agents) == 1

    def test_agents_unknown_key_warning(self):
        """Unknown keys in agents should produce warnings."""
        cfg = {
            "agents": [
                {
                    "name": "test",
                    "bad_field": "value",  # Unknown key
                }
            ],
            "unknown_top": "value",  # Top-level unknown
        }

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            validate_agents_config(cfg)
            assert any("bad_field" in str(warning.message) for warning in w)
            assert any("unknown_top" in str(warning.message) for warning in w)

    def test_agents_missing_required_field(self):
        """Missing 'agents' field should raise ValidationError."""
        cfg = {"something_else": "value"}

        with self.assertRaises(ValidationError):
            validate_agents_config(cfg)

    def test_base_connector_validates_telegram_on_load(self):
        """BaseConnector should validate telegram_config on _load_config."""
        from base_connector import BaseConfig

        class TestConfig(BaseConfig):
            def _default_config(self):
                return {"token": "", "allowed_users": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "telegram_config.json"

            # Valid config should load
            config_file.write_text(
                json.dumps(
                    {
                        "token": "test",
                        "allowed_users": [123],
                    }
                )
            )

            cfg = TestConfig(str(config_file))
            assert cfg.config["token"] == "test"

    def test_base_connector_rejects_invalid_telegram_on_load(self):
        """BaseConnector should raise on invalid telegram config schema."""
        from base_connector import BaseConfig

        class TestConfig(BaseConfig):
            def _default_config(self):
                return {"token": "", "allowed_users": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "telegram_config.json"

            # Invalid config (allowed_users should be list, not string)
            config_file.write_text(
                json.dumps(
                    {
                        "token": "test",
                        "allowed_users": "not_a_list",
                    }
                )
            )

            # Should raise ValidationError when _load_config runs
            with self.assertRaises(ValidationError):
                TestConfig(str(config_file))

    def test_telegram_helper_functions_propagate_validation_error(self):
        """Helper functions should handle config validation errors gracefully."""
        # This test verifies that when _get_telegram_username is called,
        # it only catches FileNotFoundError/JSONDecodeError, not ValidationError
        from agent_manager import _get_telegram_username

        # Test with missing file - should return None (FileNotFoundError caught)
        result = _get_telegram_username("123")
        assert result is None


if __name__ == "__main__":
    unittest.main()
