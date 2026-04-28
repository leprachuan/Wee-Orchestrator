#!/usr/bin/env python3
"""Regression test for issue #266: rabbitmq_ssl: None breaks .get() fallback.

After the BaseConnector refactor (issue #30), model_dump() in config_schemas.py
included rabbitmq_ssl: null in the serialized config when the field was not set
in webex_config.json. This caused dict.get("rabbitmq_ssl", port == 5671) to
return None instead of the port-derived default, leading to plaintext connections
on TLS-only port 5671 and StreamLostError.

Fix: base_connector.py uses model_dump(exclude_none=True) so absent Optional
fields are omitted from the dict, preserving the .get() fallback semantics.
"""

import json
import sys

import pytest

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


# ---------------------------------------------------------------------------
# Test 1: model_dump(exclude_none=True) omits rabbitmq_ssl when not set
# ---------------------------------------------------------------------------


def test_issue_266_rabbitmq_ssl_absent_when_not_configured(tmp_path):
    """rabbitmq_ssl must be absent from the config dict when not set in the file.

    Before the fix, model_dump() emitted rabbitmq_ssl: None. After the fix,
    the key is absent, so .get("rabbitmq_ssl", port == 5671) uses the default.
    """
    config_file = tmp_path / "webex_config.json"
    config_file.write_text(
        json.dumps(
            {
                "token": "test-token",
                "rabbitmq_host": "192.168.0.85",
                "rabbitmq_port": 5672,
                "rabbitmq_user": "admin",
                "rabbitmq_password": "secret",
                "rabbitmq_queue": "webex",
                "rabbitmq_vhost": "/",
                # rabbitmq_ssl intentionally omitted
            }
        )
    )

    from base_connector import BaseConfig

    class WebexBaseConfig(BaseConfig):
        def _default_config(self):
            return {}

    cfg = WebexBaseConfig(str(config_file))

    # Key must be absent so .get() fallback works
    assert "rabbitmq_ssl" not in cfg.config, (
        "rabbitmq_ssl must not appear in config dict when not set in webex_config.json; "
        "its presence (even as None) breaks .get('rabbitmq_ssl', port == 5671)"
    )


# ---------------------------------------------------------------------------
# Test 2: .get() fallback returns True when port is 5671 and key is absent
# ---------------------------------------------------------------------------


def test_issue_266_ssl_fallback_on_port_5671(tmp_path):
    """When rabbitmq_ssl is not set and port is 5671, SSL must be enabled.

    Simulates the exact expression used in webex_connector.connect_rabbitmq():
        use_ssl = self.config.config.get("rabbitmq_ssl", port == 5671)
    """
    config_file = tmp_path / "webex_config.json"
    config_file.write_text(
        json.dumps(
            {
                "token": "test-token",
                "rabbitmq_host": "192.168.0.85",
                "rabbitmq_port": 5671,  # TLS port
                "rabbitmq_user": "admin",
                "rabbitmq_password": "secret",
                "rabbitmq_queue": "webex",
                "rabbitmq_vhost": "/",
                # rabbitmq_ssl intentionally omitted
            }
        )
    )

    from base_connector import BaseConfig

    class WebexBaseConfig(BaseConfig):
        def _default_config(self):
            return {}

    cfg = WebexBaseConfig(str(config_file))

    port = cfg.config["rabbitmq_port"]
    use_ssl = cfg.config.get("rabbitmq_ssl", port == 5671)

    assert use_ssl is True, (
        f"SSL must be enabled on port 5671 when rabbitmq_ssl is not set; "
        f"got use_ssl={use_ssl!r} (port={port})"
    )


# ---------------------------------------------------------------------------
# Test 3: .get() fallback returns False when port is 5672 and key is absent
# ---------------------------------------------------------------------------


def test_issue_266_no_ssl_fallback_on_port_5672(tmp_path):
    """When rabbitmq_ssl is not set and port is 5672, SSL must be disabled."""
    config_file = tmp_path / "webex_config.json"
    config_file.write_text(
        json.dumps(
            {
                "token": "test-token",
                "rabbitmq_host": "192.168.0.85",
                "rabbitmq_port": 5672,  # plaintext port
                "rabbitmq_user": "admin",
                "rabbitmq_password": "secret",
                "rabbitmq_queue": "webex",
                "rabbitmq_vhost": "/",
                # rabbitmq_ssl intentionally omitted
            }
        )
    )

    from base_connector import BaseConfig

    class WebexBaseConfig(BaseConfig):
        def _default_config(self):
            return {}

    cfg = WebexBaseConfig(str(config_file))

    port = cfg.config["rabbitmq_port"]
    use_ssl = cfg.config.get("rabbitmq_ssl", port == 5671)

    assert use_ssl is False, (
        f"SSL must be disabled on port 5672 when rabbitmq_ssl is not set; "
        f"got use_ssl={use_ssl!r} (port={port})"
    )


# ---------------------------------------------------------------------------
# Test 4: explicit rabbitmq_ssl=true overrides the port-derived default
# ---------------------------------------------------------------------------


def test_issue_266_explicit_ssl_true_overrides_port_default(tmp_path):
    """Explicit rabbitmq_ssl=true must be honoured even on plaintext port 5672."""
    config_file = tmp_path / "webex_config.json"
    config_file.write_text(
        json.dumps(
            {
                "token": "test-token",
                "rabbitmq_host": "192.168.0.85",
                "rabbitmq_port": 5672,
                "rabbitmq_user": "admin",
                "rabbitmq_password": "secret",
                "rabbitmq_queue": "webex",
                "rabbitmq_vhost": "/",
                "rabbitmq_ssl": True,  # explicit override
            }
        )
    )

    from base_connector import BaseConfig

    class WebexBaseConfig(BaseConfig):
        def _default_config(self):
            return {}

    cfg = WebexBaseConfig(str(config_file))

    port = cfg.config["rabbitmq_port"]
    use_ssl = cfg.config.get("rabbitmq_ssl", port == 5671)

    assert (
        use_ssl is True
    ), f"Explicit rabbitmq_ssl=true must be preserved; got use_ssl={use_ssl!r}"


# ---------------------------------------------------------------------------
# Test 5: explicit rabbitmq_ssl=false overrides port 5671 default
# ---------------------------------------------------------------------------


def test_issue_266_explicit_ssl_false_overrides_port_5671(tmp_path):
    """Explicit rabbitmq_ssl=false must be honoured even on TLS port 5671."""
    config_file = tmp_path / "webex_config.json"
    config_file.write_text(
        json.dumps(
            {
                "token": "test-token",
                "rabbitmq_host": "192.168.0.85",
                "rabbitmq_port": 5671,
                "rabbitmq_user": "admin",
                "rabbitmq_password": "secret",
                "rabbitmq_queue": "webex",
                "rabbitmq_vhost": "/",
                "rabbitmq_ssl": False,  # explicit disable despite TLS port
            }
        )
    )

    from base_connector import BaseConfig

    class WebexBaseConfig(BaseConfig):
        def _default_config(self):
            return {}

    cfg = WebexBaseConfig(str(config_file))

    port = cfg.config["rabbitmq_port"]
    use_ssl = cfg.config.get("rabbitmq_ssl", port == 5671)

    assert use_ssl is False, (
        f"Explicit rabbitmq_ssl=false must be preserved even on port 5671; "
        f"got use_ssl={use_ssl!r}"
    )


# ---------------------------------------------------------------------------
# Test 6: other Optional fields with non-None schema defaults are preserved
# ---------------------------------------------------------------------------


def test_issue_266_required_fields_not_stripped_by_exclude_none(tmp_path):
    """exclude_none=True must not strip fields with non-None values.

    Verifies that fields like rabbitmq_host, rabbitmq_port (which have
    real values) are preserved in the config dict.
    """
    config_file = tmp_path / "webex_config.json"
    config_file.write_text(
        json.dumps(
            {
                "token": "test-token",
                "rabbitmq_host": "mybroker.example.com",
                "rabbitmq_port": 5671,
                "rabbitmq_user": "admin",
                "rabbitmq_password": "secret",
                "rabbitmq_queue": "webex-prod",
                "rabbitmq_vhost": "/prod",
            }
        )
    )

    from base_connector import BaseConfig

    class WebexBaseConfig(BaseConfig):
        def _default_config(self):
            return {}

    cfg = WebexBaseConfig(str(config_file))

    assert cfg.config["rabbitmq_host"] == "mybroker.example.com"
    assert cfg.config["rabbitmq_port"] == 5671
    assert cfg.config["rabbitmq_queue"] == "webex-prod"
    assert cfg.config["rabbitmq_vhost"] == "/prod"
