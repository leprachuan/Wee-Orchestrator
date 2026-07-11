"""Regression test for issue #386: Local API loads bundled remote agent configuration.

API mode (`--api`) bypasses argparse entirely (see `start_api_server`), so a
`--config` flag was silently ignored and the server fell back to reading the
repository's bundled `agents.json` (exposing shared/remote agent
definitions) whenever `AGENT_CONFIG_FILE` was not set. This verifies `--config`
is honored in API mode, giving callers (e.g. the macOS app's Local API
launcher) a CLI-based way to force an isolated, empty agent configuration.
"""

from agent_manager import _api_config_file_from_argv


def test_api_config_flag_is_parsed_from_argv():
    """--config <path> is extracted even though --api bypasses argparse."""
    argv = ["--api", "--config", "/tmp/local-agents.json"]

    assert _api_config_file_from_argv(argv) == "/tmp/local-agents.json"


def test_api_config_flag_short_form_is_parsed_from_argv():
    argv = ["--api", "-c", "/tmp/local-agents.json"]

    assert _api_config_file_from_argv(argv) == "/tmp/local-agents.json"


def test_api_config_flag_equals_form_is_parsed_from_argv():
    argv = ["--api", "--config=/tmp/local-agents.json"]

    assert _api_config_file_from_argv(argv) == "/tmp/local-agents.json"


def test_api_config_flag_absent_returns_none():
    """Existing deployments that only pass --api keep falling back to env vars."""
    argv = ["--api"]

    assert _api_config_file_from_argv(argv) is None
