"""Regression coverage for Issue #427: polished Wee interactive terminal UI."""

from io import StringIO
from unittest.mock import MagicMock, patch

import wee_cli
from rich.console import Console


def _capture_console():
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=True,
        color_system="truecolor",
        width=100,
        highlight=False,
    )
    return console, output


def test_provider_labels():
    assert wee_cli._provider_label("openrouter/openai/gpt-5.6-luna") == "OpenRouter"
    assert wee_cli._provider_label("ollama/qwen3.5:4b") == "Ollama"
    assert wee_cli._provider_label("lmstudio/qwen") == "LM Studio"


def test_banner_contains_agentic_status():
    console, output = _capture_console()
    with (
        patch.object(wee_cli, "_console", console),
        patch.object(wee_cli, "_interactive_ui_enabled", return_value=True),
    ):
        wee_cli._render_interactive_banner(
            "openrouter/openai/gpt-5.6-luna", "auto", True, "default"
        )

    rendered = output.getvalue()
    assert "Wee" in rendered
    assert "OpenRouter" in rendered
    assert "openrouter/openai/gpt-5.6-luna" in rendered
    assert "tools" in rendered
    assert "\x1b[" in rendered


def test_prompt_area_is_colored_and_status_aware():
    console, output = _capture_console()
    console.input = MagicMock(return_value="hello")
    tracker = wee_cli.TokenTracker(context_window=1000)
    tracker.last_prompt_tokens = 250
    with (
        patch.object(wee_cli, "_console", console),
        patch.object(wee_cli, "_interactive_ui_enabled", return_value=True),
    ):
        answer = wee_cli._read_interactive_prompt(
            "openrouter/openai/gpt-5.6-luna", "auto", True, tracker
        )

    assert answer == "hello"
    rendered = output.getvalue()
    assert "You" in rendered
    assert "tools on" in rendered
    assert "25% context" in rendered


def test_assistant_response_renders_markdown_panel():
    console, output = _capture_console()
    with (
        patch.object(wee_cli, "_console", console),
        patch.object(wee_cli, "_interactive_ui_enabled", return_value=True),
    ):
        wee_cli._render_assistant_response(
            "## Result\n\n- one\n- two\n\n```python\nprint('ok')\n```"
        )

    rendered = output.getvalue()
    assert "Wee" in rendered
    assert "Result" in rendered
    assert "print" in rendered


def test_plain_ui_escape_hatch():
    fake_stream = MagicMock()
    fake_stream.isatty.return_value = True
    with (
        patch.object(wee_cli, "_rich_available", True),
        patch.object(wee_cli.sys, "stdin", fake_stream),
        patch.object(wee_cli.sys, "stderr", fake_stream),
        patch.dict("os.environ", {"WEE_PLAIN_UI": "1"}),
    ):
        assert wee_cli._interactive_ui_enabled() is False
