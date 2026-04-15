"""Regression tests for wee-cli QA round fixes (Issue #158).

Covers M-1 (permission wired through), M-2 (output format works),
M-3 (response variable used), N-1 (imports), N-2 (f-string), N-3 (line length).
"""
import ast
import importlib
import io
import json
import sys
import textwrap
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_CLI_PATH = Path(__file__).parent.parent / "wee_cli.py"
_RUNTIME_PATH = Path(__file__).parent.parent / "wee_runtime.py"


def _load_source(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


# ---------------------------------------------------------------------------
# N-1: Unused imports removed
# ---------------------------------------------------------------------------
_REMOVED_IMPORTS = {"signal", "tempfile", "time"}
_REMOVED_FROM_IMPORTS = {"PROVIDER_PRESETS", "TOOL_TIMEOUT"}


def test_n1_stdlib_imports_removed():
    """signal, tempfile, time must not be imported in wee_cli.py."""
    tree = _load_source(_CLI_PATH)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split(".")[0] for alias in node.names}
            assert not names & _REMOVED_IMPORTS, (
                f"Unused stdlib imports still present: {names & _REMOVED_IMPORTS}"
            )


def test_n1_wee_runtime_imports_cleaned():
    """PROVIDER_PRESETS and TOOL_TIMEOUT must not be imported from wee_runtime."""
    tree = _load_source(_CLI_PATH)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "wee_runtime":
            names = {alias.name for alias in node.names}
            assert not names & _REMOVED_FROM_IMPORTS, (
                f"Dead imports from wee_runtime still present: {names & _REMOVED_FROM_IMPORTS}"
            )


def test_n1_syntax_import_removed():
    """rich.syntax.Syntax must not be imported."""
    source = _CLI_PATH.read_text()
    assert "from rich.syntax import Syntax" not in source
    assert "Syntax" not in source or "syntax" not in source.lower()


# ---------------------------------------------------------------------------
# N-2: f-string without placeholder
# ---------------------------------------------------------------------------
def test_n2_fstring_placeholder_fixed():
    """The /help message must not use an f-string without placeholders."""
    source = _CLI_PATH.read_text()
    assert 'f"Type /help for commands, exit to quit' not in source, (
        "F841: f-string with no placeholders still present"
    )


# ---------------------------------------------------------------------------
# N-3: Line lengths ≤ 88 chars
# ---------------------------------------------------------------------------
def test_n3_no_lines_over_88():
    """All lines in wee_cli.py must be ≤ 88 characters."""
    lines = _CLI_PATH.read_text().splitlines()
    violations = [
        (i + 1, len(line), line)
        for i, line in enumerate(lines)
        if len(line) > 88
    ]
    assert not violations, (
        "Lines exceeding 88 chars:\n"
        + "\n".join(f"  L{ln}: {length} chars" for ln, length, _ in violations)
    )


# ---------------------------------------------------------------------------
# NIT-1: --api-key help text warns about env var
# ---------------------------------------------------------------------------
def test_nit1_api_key_help_warns_env():
    """--api-key help text must mention WEE_API_KEY env var as safer alternative."""
    # Import lazily to avoid side-effects at collection time
    import importlib.util
    spec = importlib.util.spec_from_file_location("wee_cli", _CLI_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Stub wee_runtime so we don't need a live Ollama
    fake_rt = types.ModuleType("wee_runtime")
    fake_rt.PROVIDER_PRESETS = {}
    fake_rt._ANTI_HALLUCINATION_PROMPT = ""
    fake_rt._WEE_TOOLS = []
    fake_rt.MAX_TOOL_ROUNDS = 1
    fake_rt.TOOL_TIMEOUT = 10
    fake_rt.execute_tool = lambda *a, **kw: ""
    fake_rt.resolve_model_and_endpoint = lambda *a, **kw: ("m", "http://x", "k")
    sys.modules["wee_runtime"] = fake_rt
    try:
        spec.loader.exec_module(mod)
        parser = mod.build_parser()
        # Grab the --api-key action's help text
        api_key_action = next(
            a for a in parser._actions if "--api-key" in (a.option_strings or [])
        )
        help_text = api_key_action.help or ""
        assert "WEE_API_KEY" in help_text, (
            "--api-key help must mention WEE_API_KEY environment variable"
        )
    finally:
        sys.modules.pop("wee_runtime", None)


# ---------------------------------------------------------------------------
# M-1: permission wired through run_single_shot → chat_stream → execute_tool
# ---------------------------------------------------------------------------
def _make_wee_cli_module():
    """Load wee_cli with a stubbed wee_runtime."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("wee_cli_test", _CLI_PATH)
    mod = importlib.util.module_from_spec(spec)
    fake_rt = types.ModuleType("wee_runtime")
    fake_rt._ANTI_HALLUCINATION_PROMPT = ""
    fake_rt._WEE_TOOLS = []
    fake_rt.MAX_TOOL_ROUNDS = 3
    fake_rt.execute_tool = MagicMock(return_value="tool output")
    fake_rt.resolve_model_and_endpoint = lambda *a, **kw: ("model", "http://base", "key")
    sys.modules["wee_runtime"] = fake_rt
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop("wee_runtime", None)
    return mod, fake_rt


def _make_streaming_client(text="Hello"):
    """Build a mock OpenAI client that streams a single text chunk."""
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = MagicMock()
    chunk.choices[0].delta.content = text
    chunk.choices[0].delta.tool_calls = None
    chunk.usage = None
    client = MagicMock()
    client.chat.completions.create.return_value = iter([chunk])
    return client


def test_m1_run_single_shot_accepts_permission():
    """run_single_shot() must accept a permission parameter."""
    mod, fake_rt = _make_wee_cli_module()
    client = _make_streaming_client()
    with patch.object(mod, "_make_client", return_value=client):
        # Should not raise TypeError even with explicit permission kwarg
        mod.run_single_shot(
            prompt="hi",
            model="m",
            api_base="http://x",
            api_key="k",
            tools_enabled=False,
            temperature=None,
            timeout=10.0,
            system_prompt="",
            output_format="text",
            permission="auto",
        )


def test_m1_permission_passed_to_execute_tool():
    """chat_stream() must pass permission kwarg to execute_tool()."""
    mod, fake_rt = _make_wee_cli_module()

    # Build a client that returns a tool call first, then a text response
    tool_chunk = MagicMock()
    tool_chunk.choices = [MagicMock()]
    tool_chunk.choices[0].delta = MagicMock()
    tool_chunk.choices[0].delta.content = None

    tc_delta = MagicMock()
    tc_delta.index = 0
    tc_delta.id = "tc_1"
    tc_delta.function = MagicMock()
    tc_delta.function.name = "bash"
    tc_delta.function.arguments = '{"command": "echo hi"}'
    tool_chunk.choices[0].delta.tool_calls = [tc_delta]
    tool_chunk.usage = None

    text_chunk = MagicMock()
    text_chunk.choices = [MagicMock()]
    text_chunk.choices[0].delta = MagicMock()
    text_chunk.choices[0].delta.content = "Done"
    text_chunk.choices[0].delta.tool_calls = None
    text_chunk.usage = None

    client = MagicMock()
    client.chat.completions.create.side_effect = [
        iter([tool_chunk]),
        iter([text_chunk]),
    ]

    messages = [{"role": "user", "content": "test"}]
    mod.chat_stream(
        client=client,
        model="m",
        messages=messages,
        tools_enabled=True,
        permission="elevated",
    )

    # execute_tool must have been called with permission="elevated"
    fake_rt.execute_tool.assert_called()
    call_kwargs = fake_rt.execute_tool.call_args
    assert call_kwargs.kwargs.get("permission") == "elevated" or (
        len(call_kwargs.args) >= 3 and call_kwargs.args[2] == "elevated"
    ), f"execute_tool not called with permission='elevated'. Call: {call_kwargs}"


def test_m1_restricted_permission_blocks_tools():
    """execute_tool() must return an error when permission='restricted'."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("wee_runtime_test", _RUNTIME_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.execute_tool("bash", {"command": "echo hi"}, permission="restricted")
    assert "restricted" in result.lower() or "blocked" in result.lower(), (
        f"Expected restriction error, got: {result!r}"
    )


def test_m1_auto_permission_allows_tools():
    """execute_tool() must execute normally when permission='auto'."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("wee_runtime_test2", _RUNTIME_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.execute_tool("bash", {"command": "echo hello"}, permission="auto")
    assert "hello" in result, f"Expected 'hello' in output, got: {result!r}"


# ---------------------------------------------------------------------------
# M-2: output format — JSON and markdown modes
# ---------------------------------------------------------------------------
def test_m2_json_output_emits_json():
    """run_single_shot() with output_format='json' must emit valid JSON to stdout."""
    mod, fake_rt = _make_wee_cli_module()
    client = _make_streaming_client("the answer is 42")

    captured = io.StringIO()
    with patch.object(mod, "_make_client", return_value=client):
        with patch("sys.stdout", captured):
            mod.run_single_shot(
                prompt="what is the answer",
                model="m",
                api_base="http://x",
                api_key="k",
                tools_enabled=False,
                temperature=None,
                timeout=10.0,
                system_prompt="",
                output_format="json",
                permission="auto",
            )

    output = captured.getvalue().strip()
    assert output, "No output produced for json format"
    data = json.loads(output)
    assert "response" in data, f"JSON output missing 'response' key: {data}"
    assert "the answer is 42" in data["response"]


def test_m2_text_output_streams_directly():
    """run_single_shot() with output_format='text' streams tokens to stdout."""
    mod, fake_rt = _make_wee_cli_module()
    client = _make_streaming_client("streaming text")

    captured = io.StringIO()
    with patch.object(mod, "_make_client", return_value=client):
        with patch("sys.stdout", captured):
            mod.run_single_shot(
                prompt="say hi",
                model="m",
                api_base="http://x",
                api_key="k",
                tools_enabled=False,
                temperature=None,
                timeout=10.0,
                system_prompt="",
                output_format="text",
                permission="auto",
            )

    output = captured.getvalue()
    assert "streaming text" in output


def test_m2_json_output_does_not_double_print():
    """JSON mode must not print raw text before the JSON envelope."""
    mod, fake_rt = _make_wee_cli_module()
    client = _make_streaming_client("secret content")

    captured = io.StringIO()
    with patch.object(mod, "_make_client", return_value=client):
        with patch("sys.stdout", captured):
            mod.run_single_shot(
                prompt="test",
                model="m",
                api_base="http://x",
                api_key="k",
                tools_enabled=False,
                temperature=None,
                timeout=10.0,
                system_prompt="",
                output_format="json",
                permission="auto",
            )

    output = captured.getvalue().strip()
    # Output must be valid JSON (not raw text + JSON)
    # If there were a double-print, json.loads would raise
    data = json.loads(output)
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# M-3: response variable is used (not dead code)
# ---------------------------------------------------------------------------
def test_m3_response_variable_not_dead_code():
    """run_single_shot() must not have response as a dead variable."""
    source = _CLI_PATH.read_text()
    # The old dead code pattern: assign response then do nothing with it
    assert "if output_format == \"json\":\n            # Re-emit as JSON" not in source, (
        "Dead code comment still present — M-3 not fixed"
    )
    # The response variable must be used in a call to _print_markdown
    assert "_print_markdown(response" in source, (
        "response variable must be passed to _print_markdown() — M-3 fix missing"
    )
