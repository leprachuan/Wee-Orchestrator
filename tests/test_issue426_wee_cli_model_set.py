"""Regression coverage for Issue #426: explicit `/model set` syntax."""

from io import StringIO
from unittest.mock import patch

from wee_cli import _normalize_model_identifier, run_interactive


def _run_with_inputs(inputs):
    with (
        patch("wee_cli._init_readline"),
        patch("wee_cli._save_readline"),
        patch("wee_cli._make_client"),
        patch("wee_cli.chat_stream", return_value="unused"),
        patch("builtins.input", side_effect=inputs),
    ):
        return run_interactive(
            model="qwen3.5:4b",
            api_base="http://localhost:11434/v1",
            api_key="ollama",
            tools_enabled=False,
            temperature=None,
            timeout=60,
            system_prompt="",
            output_format="text",
            permission="restricted",
        )


def test_model_set_strips_command_keyword():
    with patch(
        "wee_cli.resolve_model_and_endpoint",
        return_value=(
            "openai/gpt-5.6-luna",
            "https://openrouter.ai/api/v1",
            "test-key",
        ),
    ) as resolve:
        model = _run_with_inputs(
            ["/model set openrouter/openai/gpt-5.6-luna", "exit"]
        )

    resolve.assert_called_once_with("openrouter/openai/gpt-5.6-luna")
    assert model == "openrouter/openai/gpt-5.6-luna"


def test_model_set_without_model_reports_usage():
    with patch("wee_cli.resolve_model_and_endpoint") as resolve:
        with patch("sys.stderr", new_callable=StringIO) as stderr:
            _run_with_inputs(["/model set", "exit"])

    resolve.assert_not_called()
    assert "Usage: /model set PROVIDER/MODEL" in stderr.getvalue()


def test_legacy_persisted_model_is_repaired():
    assert _normalize_model_identifier(
        "set openrouter/openai/gpt-5.6-luna"
    ) == "openrouter/openai/gpt-5.6-luna"


def test_normal_model_is_unchanged():
    assert _normalize_model_identifier(
        "openrouter/openai/gpt-5.6-luna"
    ) == "openrouter/openai/gpt-5.6-luna"


def test_legacy_persisted_current_alias_is_ignored():
    assert _normalize_model_identifier("current") == ""


def test_model_current_does_not_switch_models():
    with patch("wee_cli.resolve_model_and_endpoint") as resolve:
        with patch("sys.stderr", new_callable=StringIO) as stderr:
            model = _run_with_inputs(["/model current", "exit"])

    resolve.assert_not_called()
    assert model == "qwen3.5:4b"
    assert "Current model: qwen3.5:4b" in stderr.getvalue()


def test_model_status_aliases_are_read_only():
    for command in ("/model", "/model show", "/model get"):
        with patch("wee_cli.resolve_model_and_endpoint") as resolve:
            _run_with_inputs([command, "exit"])
        resolve.assert_not_called()
