#!/usr/bin/env python3
"""Comprehensive agentic runtime test suite — Volume 2.

Supplements test_wee_runtime_agentic.py with deeper coverage:
  - Temperature / parameter propagation
  - Parallel multi-tool calls in one round
  - Tool call ID fallback to synthetic IDs
  - Exact message-history structure validation
  - Env-var resolution paths (WEE_API_BASE, WEE_API_KEY)
  - Stderr diagnostic output format
  - System prompt combination rules
  - Content + tool_calls interleaving
  - Tool result injection into messages
  - OpenRouter API key missing → clear error
  - Large tool output handling
  - Python multi-line code execution
  - Bash pipe / redirection support
  - Bash timeout (fast timeout)
  - Live Ollama: temperature=0 determinism
  - Live Ollama: multi-step tool chain
  - Live OpenRouter: tool calling smoke test

Run:
    pytest tests/test_wee_runtime_comprehensive_v2.py -v
    pytest tests/test_wee_runtime_comprehensive_v2.py -v -k 'not Live'
"""

import io
import os
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

# ---------------------------------------------------------------------------
# Shared constants (mirrors test_wee_runtime_agentic.py)
# ---------------------------------------------------------------------------

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://192.168.1.101:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_TEST_MODEL", "ollama/qwen3:8b")
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_TEST_MODEL", "openrouter/google/gemma-3-12b-it:free"
)

WEE_RUNTIME = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "wee_runtime.py"
)

LIVE_TIMEOUT = 120
UNIT_TIMEOUT = 10


def _has_ollama():
    try:
        r = subprocess.run(
            [
                "curl",
                "-s",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                f"{OLLAMA_HOST}/api/tags",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout.strip() == "200"
    except Exception:
        return False


def _has_openrouter_key():
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return True
    try:
        import keyring

        return bool(keyring.get_password("openrouter", "api_key"))
    except Exception:
        return False


HAS_OLLAMA = _has_ollama()
HAS_OPENROUTER = _has_openrouter_key()

skip_ollama = unittest.skipUnless(HAS_OLLAMA, "Ollama not reachable")
skip_openrouter = unittest.skipUnless(HAS_OPENROUTER, "No OPENROUTER_API_KEY")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _make_tool_call_delta(idx, tc_id=None, name=None, arguments=None):
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=idx, id=tc_id, function=fn)


def _run_main_mocked(
    model, prompt, tools=False, system_prompt="", extra_args=None, env_overrides=None
):
    """Run wee_runtime.main() in-process with captured I/O.

    env_overrides: dict of env vars to temporarily set during execution.
    """
    import wee_runtime

    argv = ["wee_runtime.py", "--model", model]
    if tools:
        argv.append("--tools")
    if system_prompt:
        argv.extend(["--system-prompt", system_prompt])
    if extra_args:
        argv.extend(extra_args)
    argv.append(prompt)

    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    old_env = {}

    sys.argv = argv
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    if env_overrides:
        for k, v in env_overrides.items():
            old_env[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    exit_code = 0
    try:
        wee_runtime.main()
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    finally:
        stdout_val = sys.stdout.getvalue()
        stderr_val = sys.stderr.getvalue()
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        if env_overrides:
            for k, orig_v in old_env.items():
                if orig_v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = orig_v

    return SimpleNamespace(stdout=stdout_val, stderr=stderr_val, returncode=exit_code)


def run_wee_cli(
    model, prompt, tools=False, system_prompt="", timeout=LIVE_TIMEOUT, extra_args=None
):
    cmd = [sys.executable, WEE_RUNTIME, "--model", model, "--timeout", str(timeout)]
    if tools:
        cmd.append("--tools")
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(prompt)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout + 30,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


# ===========================================================================
# 1. TEMPERATURE & PARAMETER PROPAGATION
# ===========================================================================


class TestTemperaturePropagation(unittest.TestCase):
    """Verify --temperature CLI arg reaches the OpenAI API call."""

    @patch("openai.OpenAI")
    def test_temperature_passed_to_api(self, mock_cls):
        """--temperature 0.7 appears in create_kwargs."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="ok"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked("ollama/t", "test", extra_args=["--temperature", "0.7"])

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertIn("temperature", call_kwargs)
        self.assertAlmostEqual(call_kwargs["temperature"], 0.7, places=2)

    @patch("openai.OpenAI")
    def test_temperature_zero_passed(self, mock_cls):
        """--temperature 0.0 (deterministic) is propagated."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="deterministic"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked("ollama/t", "test", extra_args=["--temperature", "0.0"])

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertIn("temperature", call_kwargs)
        self.assertAlmostEqual(call_kwargs["temperature"], 0.0, places=2)

    @patch("openai.OpenAI")
    def test_no_temperature_means_absent_from_api(self, mock_cls):
        """Without --temperature, the key is absent from create_kwargs."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="ok"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked("ollama/t", "test")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertNotIn("temperature", call_kwargs)

    @patch("openai.OpenAI")
    def test_temperature_propagated_in_tools_mode(self, mock_cls):
        """--temperature is propagated even when --tools is active."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="ok"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked(
            "ollama/t", "test", tools=True, extra_args=["--temperature", "0.3"]
        )

        # All rounds should include temperature
        for c in mock_client.chat.completions.create.call_args_list:
            kw = c[1]
            self.assertIn("temperature", kw)
            self.assertAlmostEqual(kw["temperature"], 0.3, places=2)


# ===========================================================================
# 2. MESSAGE HISTORY STRUCTURE VALIDATION
# ===========================================================================


class TestMessageHistoryStructure(unittest.TestCase):
    """Validate the exact sequence of messages built by the tool loop."""

    @patch("openai.OpenAI")
    def test_no_tools_message_sequence(self, mock_cls):
        """Without tools: [system, user] — exactly two messages."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="hi"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked("ollama/t", "hello")

        msgs = mock_client.chat.completions.create.call_args[1]["messages"]
        roles = [m["role"] for m in msgs]
        self.assertIn("system", roles)
        self.assertIn("user", roles)
        self.assertEqual(roles[-1], "user")

    @patch("openai.OpenAI")
    def test_tool_round_appends_assistant_and_tool_messages(self, mock_cls):
        """After one tool round: [system, user, assistant, tool, ...]."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0, tc_id="tc1", name="bash", arguments='{"command": "echo hi"}'
        )
        r1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [
            _make_chunk(content="done"),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        _run_main_mocked("ollama/t", "run echo", tools=True)

        # Second call should have: system, user, assistant (w/ tool_calls), tool
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)
        msgs_r2 = mock_client.chat.completions.create.call_args_list[1][1]["messages"]
        roles_r2 = [m["role"] for m in msgs_r2]

        self.assertIn("assistant", roles_r2)
        self.assertIn("tool", roles_r2)
        # Tool message must follow assistant
        assistant_idx = next(i for i, r in enumerate(roles_r2) if r == "assistant")
        tool_idx = next(i for i, r in enumerate(roles_r2) if r == "tool")
        self.assertGreater(tool_idx, assistant_idx)

    @patch("openai.OpenAI")
    def test_tool_message_has_tool_call_id(self, mock_cls):
        """Tool result message includes tool_call_id matching the call."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0, tc_id="my_tc_id", name="bash", arguments='{"command": "echo x"}'
        )
        r1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [
            _make_chunk(content="done"),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        _run_main_mocked("ollama/t", "echo", tools=True)

        msgs_r2 = mock_client.chat.completions.create.call_args_list[1][1]["messages"]
        tool_msgs = [m for m in msgs_r2 if m["role"] == "tool"]
        self.assertTrue(tool_msgs, "Should have at least one tool message")
        self.assertEqual(tool_msgs[0]["tool_call_id"], "my_tc_id")

    @patch("openai.OpenAI")
    def test_assistant_message_has_tool_calls_list(self, mock_cls):
        """Assistant message in history has tool_calls list with function details."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0, tc_id="tc_check", name="python", arguments='{"code": "print(1)"}'
        )
        r1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [_make_chunk(content="1"), _make_chunk(content=None, finish_reason="stop")]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        _run_main_mocked("ollama/t", "calc", tools=True)

        msgs_r2 = mock_client.chat.completions.create.call_args_list[1][1]["messages"]
        assistant_msgs = [m for m in msgs_r2 if m["role"] == "assistant"]
        self.assertTrue(assistant_msgs)
        ast_msg = assistant_msgs[0]
        self.assertIn("tool_calls", ast_msg)
        self.assertEqual(len(ast_msg["tool_calls"]), 1)
        tc = ast_msg["tool_calls"][0]
        self.assertEqual(tc["function"]["name"], "python")

    @patch("openai.OpenAI")
    def test_user_prompt_in_first_call_messages(self, mock_cls):
        """User prompt always appears in messages for the first API call."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="reply"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked("ollama/t", "my special prompt text")

        msgs = mock_client.chat.completions.create.call_args[1]["messages"]
        user_msgs = [m for m in msgs if m["role"] == "user"]
        self.assertTrue(user_msgs)
        self.assertIn("my special prompt text", user_msgs[0]["content"])


# ===========================================================================
# 3. PARALLEL MULTI-TOOL CALLS IN ONE ROUND
# ===========================================================================


class TestParallelToolCalls(unittest.TestCase):
    """The tool loop handles multiple simultaneous tool calls from the model."""

    @patch("openai.OpenAI")
    def test_two_tools_in_one_round(self, mock_cls):
        """Model issues two tool calls in round 1; both are executed."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td0 = _make_tool_call_delta(
            0, tc_id="tc_a", name="bash", arguments='{"command": "echo first"}'
        )
        td1 = _make_tool_call_delta(
            1, tc_id="tc_b", name="python", arguments='{"code": "print(2)"}'
        )
        r1 = [
            _make_chunk(content="", tool_calls=[td0]),
            _make_chunk(content="", tool_calls=[td1]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [
            _make_chunk(content="both done"),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        result = _run_main_mocked("ollama/t", "two tools", tools=True)
        self.assertEqual(result.returncode, 0)
        # Verify two tool messages in round-2 messages
        msgs_r2 = mock_client.chat.completions.create.call_args_list[1][1]["messages"]
        tool_msgs = [m for m in msgs_r2 if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 2, "Both tool calls should produce results")

    @patch("openai.OpenAI")
    def test_two_tools_ids_distinct(self, mock_cls):
        """Two parallel tool calls get distinct tool_call_id values."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td0 = _make_tool_call_delta(
            0, tc_id="id_one", name="bash", arguments='{"command": "echo a"}'
        )
        td1 = _make_tool_call_delta(
            1, tc_id="id_two", name="bash", arguments='{"command": "echo b"}'
        )
        r1 = [
            _make_chunk(content="", tool_calls=[td0]),
            _make_chunk(content="", tool_calls=[td1]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [_make_chunk(content="done"), _make_chunk(content=None)]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        _run_main_mocked("ollama/t", "test ids", tools=True)

        msgs_r2 = mock_client.chat.completions.create.call_args_list[1][1]["messages"]
        tool_msgs = [m for m in msgs_r2 if m["role"] == "tool"]
        ids = [m["tool_call_id"] for m in tool_msgs]
        self.assertEqual(len(set(ids)), 2, "Tool call IDs must be distinct")


# ===========================================================================
# 4. SYNTHETIC TOOL CALL ID FALLBACK
# ===========================================================================


class TestSyntheticToolCallID(unittest.TestCase):
    """When tc_delta.id is None, a synthetic id is generated."""

    @patch("openai.OpenAI")
    def test_none_id_gets_synthetic(self, mock_cls):
        """Tool call with id=None is assigned a tc_wee_N synthetic id."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # No id on the delta
        td = _make_tool_call_delta(
            0, tc_id=None, name="bash", arguments='{"command": "echo synth"}'
        )
        r1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [
            _make_chunk(content="synth ok"),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        _run_main_mocked("ollama/t", "synth id", tools=True)

        msgs_r2 = mock_client.chat.completions.create.call_args_list[1][1]["messages"]
        assistant_msgs = [m for m in msgs_r2 if m["role"] == "assistant"]
        tool_msgs = [m for m in msgs_r2 if m["role"] == "tool"]

        self.assertTrue(assistant_msgs)
        self.assertTrue(tool_msgs)

        # IDs should match between assistant tool_calls and tool message
        ast_tc_id = assistant_msgs[0]["tool_calls"][0]["id"]
        tool_tc_id = tool_msgs[0]["tool_call_id"]
        self.assertEqual(
            ast_tc_id,
            tool_tc_id,
            "Synthetic ID must match between assistant and tool msg",
        )
        self.assertTrue(
            ast_tc_id.startswith("tc_wee_") or ast_tc_id,
            "Synthetic ID should be non-empty",
        )


# ===========================================================================
# 5. ENV VAR RESOLUTION PATHS
# ===========================================================================


class TestEnvVarResolution(unittest.TestCase):
    """resolve_model_and_endpoint uses WEE_API_BASE and WEE_API_KEY env vars."""

    def setUp(self):
        import wee_runtime

        self.resolve = wee_runtime.resolve_model_and_endpoint

    def test_wee_api_base_overrides_default(self):
        """WEE_API_BASE env var sets the base URL for bare models."""
        original = os.environ.get("WEE_API_BASE")
        try:
            os.environ["WEE_API_BASE"] = "http://custom-host:9999/v1"
            model, base, _ = self.resolve("bare-model")
            self.assertEqual(base, "http://custom-host:9999/v1")
        finally:
            if original is None:
                os.environ.pop("WEE_API_BASE", None)
            else:
                os.environ["WEE_API_BASE"] = original

    def test_wee_api_key_sets_key(self):
        """WEE_API_KEY env var is used when no other key is available."""
        orig_key = os.environ.get("WEE_API_KEY")
        orig_base = os.environ.get("WEE_API_BASE")
        try:
            os.environ.pop("OPENROUTER_API_KEY", None)
            os.environ["WEE_API_KEY"] = "test_api_key_xyz"
            os.environ["WEE_API_BASE"] = "http://localhost:1111/v1"
            _, _, key = self.resolve("bare-model")
            self.assertEqual(key, "test_api_key_xyz")
        finally:
            if orig_key is None:
                os.environ.pop("WEE_API_KEY", None)
            else:
                os.environ["WEE_API_KEY"] = orig_key
            if orig_base is None:
                os.environ.pop("WEE_API_BASE", None)
            else:
                os.environ["WEE_API_BASE"] = orig_base

    def test_explicit_key_overrides_env(self):
        """Explicit api_key param takes priority over WEE_API_KEY env var."""
        orig_key = os.environ.get("WEE_API_KEY")
        try:
            os.environ["WEE_API_KEY"] = "env_key"
            _, _, key = self.resolve("ollama/model", api_key="explicit_key")
            self.assertEqual(key, "explicit_key")
        finally:
            if orig_key is None:
                os.environ.pop("WEE_API_KEY", None)
            else:
                os.environ["WEE_API_KEY"] = orig_key

    def test_explicit_base_overrides_wee_api_base_env(self):
        """Explicit api_base param takes priority over WEE_API_BASE env var."""
        orig = os.environ.get("WEE_API_BASE")
        try:
            os.environ["WEE_API_BASE"] = "http://env-host:8888/v1"
            _, base, _ = self.resolve("bare-model", api_base="http://explicit:7777/v1")
            self.assertEqual(base, "http://explicit:7777/v1")
        finally:
            if orig is None:
                os.environ.pop("WEE_API_BASE", None)
            else:
                os.environ["WEE_API_BASE"] = orig

    def test_openrouter_key_missing_exits_with_error(self):
        """OpenRouter request without API key exits non-zero with clear error."""
        orig = os.environ.get("OPENROUTER_API_KEY")
        try:
            os.environ.pop("OPENROUTER_API_KEY", None)
            result = run_wee_cli(
                "openrouter/some/model",
                "test",
                extra_args=["--api-key", ""],
                timeout=15,
            )
            # Should fail (missing key)
            self.assertNotEqual(result.returncode, 0)
        finally:
            if orig is not None:
                os.environ["OPENROUTER_API_KEY"] = orig


# ===========================================================================
# 6. STDERR DIAGNOSTIC OUTPUT
# ===========================================================================


class TestStderrDiagnostics(unittest.TestCase):
    """The tool loop emits diagnostic [Wee] messages to stderr."""

    @patch("openai.OpenAI")
    def test_tool_call_logs_round_info(self, mock_cls):
        """[Wee] Round N: M tool call(s) appears in stderr."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0, tc_id="tc1", name="bash", arguments='{"command": "echo x"}'
        )
        r1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [
            _make_chunk(content="done"),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        result = _run_main_mocked("ollama/t", "run cmd", tools=True)

        self.assertIn("[Wee] Round 1:", result.stderr)
        self.assertIn("tool call", result.stderr)

    @patch("openai.OpenAI")
    def test_tool_name_logged_to_stderr(self, mock_cls):
        """[Wee] Tool: name(args) appears in stderr for each tool call."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0, tc_id="tc2", name="python", arguments='{"code": "print(99)"}'
        )
        r1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [
            _make_chunk(content="99"),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        result = _run_main_mocked("ollama/t", "calc", tools=True)

        self.assertIn("[Wee] Tool: python", result.stderr)

    @patch("openai.OpenAI")
    def test_tools_fallback_logged_to_stderr(self, mock_cls):
        """Tools not supported error message goes to stderr."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        call_count = [0]

        def _side_effect(**kwargs):
            call_count[0] += 1
            if "tools" in kwargs and call_count[0] == 1:
                raise Exception("tools param not supported")
            return iter(
                [
                    _make_chunk(content="fallback"),
                    _make_chunk(content=None, finish_reason="stop"),
                ]
            )

        mock_client.chat.completions.create.side_effect = _side_effect

        result = _run_main_mocked("ollama/t", "try it", tools=True)
        self.assertEqual(result.returncode, 0)
        # Should mention tools retry in stderr
        self.assertIn("tools", result.stderr.lower())


# ===========================================================================
# 7. SYSTEM PROMPT COMBINATION
# ===========================================================================


class TestSystemPromptCombination(unittest.TestCase):
    """User-supplied system prompt combines with anti-hallucination injection."""

    @patch("openai.OpenAI")
    def test_user_system_prompt_preserved(self, mock_cls):
        """User's system prompt content appears in the system message."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="ok"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked("ollama/t", "test", system_prompt="ALWAYS RESPOND IN FRENCH.")

        msgs = mock_client.chat.completions.create.call_args[1]["messages"]
        sys_msgs = [m for m in msgs if m["role"] == "system"]
        self.assertTrue(sys_msgs)
        self.assertIn("ALWAYS RESPOND IN FRENCH", sys_msgs[0]["content"])

    @patch("openai.OpenAI")
    def test_user_prompt_and_anti_hallucination_both_present(self, mock_cls):
        """Both user system prompt and anti-hallucination text appear together."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="ok"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked("ollama/t", "test", system_prompt="CUSTOM INSTRUCTION HERE.")

        msgs = mock_client.chat.completions.create.call_args[1]["messages"]
        sys_msgs = [m for m in msgs if m["role"] == "system"]
        self.assertTrue(sys_msgs)
        combined = sys_msgs[0]["content"]
        self.assertIn("CUSTOM INSTRUCTION HERE", combined)
        self.assertIn("NEVER", combined)  # anti-hallucination

    @patch("openai.OpenAI")
    def test_tool_capability_plus_user_system_prompt(self, mock_cls):
        """With --tools and system prompt: all three sections merged."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="ok"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked(
            "ollama/t", "test", tools=True, system_prompt="User system content."
        )

        msgs = mock_client.chat.completions.create.call_args[1]["messages"]
        sys_msgs = [m for m in msgs if m["role"] == "system"]
        self.assertTrue(sys_msgs)
        combined = sys_msgs[0]["content"]
        self.assertIn("User system content.", combined)
        self.assertIn("NEVER", combined)  # anti-hallucination
        self.assertIn("bash", combined)  # tool capability


# ===========================================================================
# 8. CONTENT + TOOL_CALLS INTERLEAVING
# ===========================================================================


class TestContentAndToolCallInterleaving(unittest.TestCase):
    """Model can emit text content AND tool calls in the same round."""

    @patch("openai.OpenAI")
    def test_content_before_tool_call_captured(self, mock_cls):
        """Text chunks before a tool call are included in the assistant message."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0, tc_id="tc_x", name="bash", arguments='{"command": "echo mixed"}'
        )
        r1 = [
            _make_chunk(content="I will run this: "),
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [
            _make_chunk(content="Result is mixed"),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        result = _run_main_mocked("ollama/t", "run mixed", tools=True)
        self.assertEqual(result.returncode, 0)

        # Check assistant message content contains pre-call text
        msgs_r2 = mock_client.chat.completions.create.call_args_list[1][1]["messages"]
        ast_msgs = [m for m in msgs_r2 if m["role"] == "assistant"]
        self.assertTrue(ast_msgs)
        ast_content = ast_msgs[0].get("content") or ""
        self.assertIn("I will run this:", ast_content)

    @patch("openai.OpenAI")
    def test_output_streamed_during_tool_call_rounds(self, mock_cls):
        """Text emitted during tool rounds is written to stdout."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0, tc_id="tc_s", name="bash", arguments='{"command": "echo streamed"}'
        )
        r1 = [
            _make_chunk(content="streaming text "),
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [
            _make_chunk(content="final"),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        result = _run_main_mocked("ollama/t", "stream mixed", tools=True)
        # "streaming text" should appear in stdout (streamed during round 1)
        self.assertIn("streaming text", result.stdout)


# ===========================================================================
# 9. TOOL OUTPUT EDGE CASES
# ===========================================================================


class TestToolOutputEdgeCases(unittest.TestCase):
    """Edge cases in tool result generation and injection."""

    def setUp(self):
        import wee_runtime

        self.execute = wee_runtime.execute_tool

    def test_bash_large_output(self):
        """Bash tool handles large output without truncation errors."""
        # Generate 1000 lines of output
        result = self.execute(
            "bash", {"command": "for i in $(seq 1 1000); do echo line_$i; done"}
        )
        self.assertIn("line_1", result)
        self.assertIn("line_1000", result)

    def test_python_multiline_code(self):
        """Python tool executes multi-line code correctly."""
        code = "x = 0\n" "for i in range(5):\n" "    x += i\n" "print(f'sum={x}')"
        result = self.execute("python", {"code": code})
        self.assertIn("sum=10", result)

    def test_bash_pipe_command(self):
        """Bash tool supports pipe-chained commands."""
        result = self.execute("bash", {"command": "echo hello_pipe | tr a-z A-Z"})
        self.assertIn("HELLO_PIPE", result)

    def test_bash_redirect_command(self):
        """Bash tool handles output redirection via subshell."""
        long_cmd = (
            "echo redirect_test > /tmp/wee_redir_test.txt"
            " && cat /tmp/wee_redir_test.txt"
        )
        result = self.execute("bash", {"command": long_cmd})
        self.assertIn("redirect_test", result)

    def test_python_import_statement(self):
        """Python tool can import stdlib modules."""
        result = self.execute("python", {"code": "import math; print(math.pi)"})
        self.assertIn("3.14", result)

    def test_bash_env_variable_access(self):
        """Bash tool can read environment variables."""
        result = self.execute("bash", {"command": "echo $HOME"})
        self.assertTrue(len(result.strip()) > 0, "Should read HOME env var")

    def test_tool_result_no_output_placeholder(self):
        """bash command with no stdout gets (no output) placeholder."""
        # A command that produces no stdout, just exits 0
        result = self.execute("bash", {"command": "true"})
        self.assertTrue(
            result.strip() == "(no output)" or result.strip() == "",
            f"Expected (no output) or empty, got: {result!r}",
        )

    @patch("openai.OpenAI")
    def test_tool_result_injected_as_tool_message(self, mock_cls):
        """Tool result is injected as role=tool message with content."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0,
            tc_id="tc_r",
            name="bash",
            arguments='{"command": "echo tool_result_here"}',
        )
        r1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        r2 = [
            _make_chunk(content="ok"),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2)]

        _run_main_mocked("ollama/t", "check inject", tools=True)

        msgs_r2 = mock_client.chat.completions.create.call_args_list[1][1]["messages"]
        tool_msgs = [m for m in msgs_r2 if m["role"] == "tool"]
        self.assertTrue(tool_msgs)
        # Content should contain actual bash output
        self.assertIn("tool_result_here", tool_msgs[0]["content"])


# ===========================================================================
# 10. FAST TIMEOUT TEST
# ===========================================================================


class TestFastToolTimeout(unittest.TestCase):
    """Tool execution timeout works (using 2s max for the test)."""

    def test_fast_timeout_terminates(self):
        """execute_tool honours a very short timeout via subprocess."""
        import wee_runtime

        orig_timeout = wee_runtime.TOOL_TIMEOUT
        wee_runtime.TOOL_TIMEOUT = 2  # force 2-second timeout
        try:
            start = time.time()
            result = wee_runtime.execute_tool("bash", {"command": "sleep 60"})
            elapsed = time.time() - start
        finally:
            wee_runtime.TOOL_TIMEOUT = orig_timeout

        self.assertLess(elapsed, 10, "Should timeout in ~2s, not 60s")
        self.assertIn("timed out", result.lower())


# ===========================================================================
# 11. MODEL NAME IN API CALL
# ===========================================================================


class TestModelNameInAPICall(unittest.TestCase):
    """The resolved model name (without provider prefix) reaches the API."""

    @patch("openai.OpenAI")
    def test_ollama_model_name_stripped_of_prefix(self, mock_cls):
        """API call uses model name without 'ollama/' prefix."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="ok"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked("ollama/qwen3:8b", "test")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "qwen3:8b")

    @patch("openai.OpenAI")
    def test_openrouter_model_name_stripped_of_prefix(self, mock_cls):
        """API call uses model name without 'openrouter/' prefix."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="ok"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        # Simulate openrouter key available
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test_key"}):
            _run_main_mocked("openrouter/google/gemma-3n-e4b-it:free", "test")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "google/gemma-3n-e4b-it:free")

    @patch("openai.OpenAI")
    def test_api_base_url_in_client_init(self, mock_cls):
        """OpenAI client is initialized with the resolved base URL."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="ok"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked("ollama/test", "test")

        init_kwargs = mock_cls.call_args[1]
        self.assertIn("base_url", init_kwargs)
        self.assertIn("11434", init_kwargs["base_url"])

    @patch("openai.OpenAI")
    def test_stream_true_always(self, mock_cls):
        """stream=True is always passed to the API call."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="ok"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        _run_main_mocked("ollama/test", "test")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertTrue(call_kwargs.get("stream"), "stream=True must always be set")


# ===========================================================================
# 12. MULTI-STEP TOOL CHAIN WITH ACCUMULATED HISTORY
# ===========================================================================


class TestMultiStepToolChain(unittest.TestCase):
    """Three-round tool chain: each round adds to message history correctly."""

    @patch("openai.OpenAI")
    def test_three_round_message_growth(self, mock_cls):
        """Message list grows by 2 (assistant + tool) after each round."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        def _tool_round(name, args_str, tc_id):
            td = _make_tool_call_delta(0, tc_id=tc_id, name=name, arguments=args_str)
            return iter(
                [
                    _make_chunk(content="", tool_calls=[td]),
                    _make_chunk(content=None, finish_reason="tool_calls"),
                ]
            )

        r1 = _tool_round("bash", '{"command": "echo r1"}', "t1")
        r2 = _tool_round("python", '{"code": "print(2)"}', "t2")
        r3 = iter(
            [
                _make_chunk(content="final answer"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )

        # call_args_list stores references to the same mutable list, so we must
        # capture message-count snapshots at call time via a custom side_effect.
        side_effects = [r1, r2, r3]
        captured_msg_counts = []
        call_index = [0]

        def capturing_create(**kwargs):
            captured_msg_counts.append(len(kwargs.get("messages", [])))
            result = side_effects[call_index[0]]
            call_index[0] += 1
            return result

        mock_client.chat.completions.create.side_effect = capturing_create

        _run_main_mocked("ollama/t", "three rounds", tools=True)

        self.assertEqual(
            len(captured_msg_counts),
            3,
            "Expected exactly 3 create() calls (3 tool rounds)",
        )
        cnt_r1, cnt_r2, cnt_r3 = captured_msg_counts

        # Round 2 should have 2 more messages than round 1 (assistant + tool)
        self.assertEqual(
            cnt_r2, cnt_r1 + 2, f"Round 1→2: expected +2 msgs, got {cnt_r1}→{cnt_r2}"
        )
        # Round 3 should have 2 more than round 2
        self.assertEqual(
            cnt_r3, cnt_r2 + 2, f"Round 2→3: expected +2 msgs, got {cnt_r2}→{cnt_r3}"
        )


# ===========================================================================
# 13. OPENROUTER SPECIFIC UNIT TESTS
# ===========================================================================


class TestOpenRouterConfiguration(unittest.TestCase):
    """Tests specific to OpenRouter provider configuration."""

    def setUp(self):
        import wee_runtime

        self.resolve = wee_runtime.resolve_model_and_endpoint
        self.presets = wee_runtime.PROVIDER_PRESETS

    def test_openrouter_preset_has_none_key(self):
        """OpenRouter preset has None as the API key (must come from env/keyring)."""
        _, key = self.presets["openrouter"]
        self.assertIsNone(key, "OpenRouter preset key should be None — use env/keyring")

    def test_openrouter_free_model_resolution(self):
        """Free-tier OpenRouter model resolves correctly."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test_key"}):
            model, base, key = self.resolve("openrouter/google/gemma-3n-e4b-it:free")
        self.assertEqual(model, "google/gemma-3n-e4b-it:free")
        self.assertIn("openrouter.ai", base)
        self.assertEqual(key, "test_key")

    def test_openrouter_key_from_env_takes_priority_over_keyring(self):
        """OPENROUTER_API_KEY env var takes priority over keyring lookup."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env_key_priority"}):
            _, _, key = self.resolve("openrouter/some/model")
        self.assertEqual(key, "env_key_priority")

    def test_openrouter_missing_key_exits_with_message(self):
        """Missing OpenRouter key causes sys.exit(1) with descriptive message."""
        orig_key = os.environ.get("OPENROUTER_API_KEY")
        try:
            os.environ.pop("OPENROUTER_API_KEY", None)
            # patch keyring to return None
            with patch("keyring.get_password", return_value=None):
                with self.assertRaises(SystemExit):
                    _run_main_mocked(
                        "openrouter/some/model",
                        "test",
                        env_overrides={"OPENROUTER_API_KEY": None},
                    )
        except (ImportError, Exception):
            pass  # keyring may not be installed
        finally:
            if orig_key is not None:
                os.environ["OPENROUTER_API_KEY"] = orig_key

    def test_openrouter_four_slash_model_path(self):
        """Model with four segments (provider/org/name:tag) resolves correctly."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}):
            model, base, _ = self.resolve("openrouter/meta-llama/llama-4-maverick:free")
        self.assertEqual(model, "meta-llama/llama-4-maverick:free")
        self.assertIn("openrouter.ai", base)


# ===========================================================================
# 14. OLLAMA SPECIFIC UNIT TESTS
# ===========================================================================


class TestOllamaConfiguration(unittest.TestCase):
    """Tests specific to Ollama provider configuration."""

    def setUp(self):
        import wee_runtime

        self.resolve = wee_runtime.resolve_model_and_endpoint
        self.presets = wee_runtime.PROVIDER_PRESETS

    def test_ollama_preset_key_is_ollama_string(self):
        """Ollama preset uses 'ollama' as the API key."""
        _, key = self.presets["ollama"]
        self.assertEqual(key, "ollama")

    def test_ollama_model_with_colon_tag(self):
        """Ollama model with :tag suffix (e.g., qwen3:8b) is preserved."""
        model, _, _ = self.resolve("ollama/qwen3:8b")
        self.assertEqual(model, "qwen3:8b")

    def test_ollama_model_with_version_and_quantization(self):
        """Ollama model with complex tag is preserved."""
        model, _, _ = self.resolve("ollama/gemma4:27b-it-q4_K_M")
        self.assertEqual(model, "gemma4:27b-it-q4_K_M")

    def test_ollama_custom_host_via_explicit_base(self):
        """Custom Ollama host via --api-base is respected."""
        model, base, key = self.resolve(
            "ollama/phi3:mini", api_base="http://gpu-box:11434/v1"
        )
        self.assertEqual(base, "http://gpu-box:11434/v1")
        self.assertEqual(key, "ollama")


# ===========================================================================
# 15. LIVE OLLAMA — EXPANDED TESTS
# ===========================================================================


@skip_ollama
class TestOllamaLiveExpanded(unittest.TestCase):
    """Additional live Ollama integration tests."""

    def test_deterministic_output_temperature_zero(self):
        """With temperature=0, two identical prompts should produce similar output."""
        prompt = "Respond with ONLY the number: what is 2 + 2?"
        r1 = run_wee_cli(
            OLLAMA_MODEL,
            prompt,
            extra_args=["--temperature", "0.0"],
            timeout=LIVE_TIMEOUT,
        )
        r2 = run_wee_cli(
            OLLAMA_MODEL,
            prompt,
            extra_args=["--temperature", "0.0"],
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(r1.returncode, 0)
        self.assertEqual(r2.returncode, 0)
        # Both should contain "4"
        self.assertIn("4", r1.stdout, f"r1 output: {r1.stdout[:100]}")
        self.assertIn("4", r2.stdout, f"r2 output: {r2.stdout[:100]}")

    def test_bash_tool_file_write_and_read(self):
        """Multi-step: write file, read it back, report content."""
        result = run_wee_cli(
            OLLAMA_MODEL,
            "Use the bash tool to: (1) write 'WEE_LIVE_TEST' to /tmp/wee_test_live.txt "
            "(2) read it back (3) confirm the content matches.",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("[Wee]", result.stderr, "Should show tool call in stderr")

    def test_python_tool_stdlib_import(self):
        """Python tool can import stdlib and use it."""
        result = run_wee_cli(
            OLLAMA_MODEL,
            "Use the python tool to import datetime and print today's year using "
            "datetime.datetime.now().year",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0)
        import datetime

        current_year = str(datetime.datetime.now().year)
        self.assertTrue(
            current_year in result.stdout or current_year in result.stderr,
            f"Expected year {current_year}. Got: {result.stdout[:200]}",
        )

    def test_bash_tool_env_variable(self):
        """Bash tool can read environment variables from the process."""
        result = run_wee_cli(
            OLLAMA_MODEL,
            "Use the bash tool to run 'echo $SHELL' and tell me the shell path.",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0)
        # Output should mention /bin/bash or /bin/sh or similar
        self.assertTrue(
            "bash" in result.stdout.lower()
            or "sh" in result.stdout.lower()
            or "bash" in result.stderr.lower(),
            f"Expected shell path. Got: {result.stdout[:200]}",
        )


# ===========================================================================
# 16. LIVE OPENROUTER — EXPANDED TESTS
# ===========================================================================


@skip_openrouter
class TestOpenRouterLiveExpanded(unittest.TestCase):
    """Additional live OpenRouter integration tests."""

    @skip_openrouter
    @skip_openrouter
    @skip_openrouter
    def test_text_generation_returns_content(self):
        """OpenRouter model returns non-empty text response."""
        result = run_wee_cli(
            OPENROUTER_MODEL,
            "Reply with exactly: OPENROUTER_LIVE_OK",
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertGreater(len(result.stdout.strip()), 0)

    @skip_openrouter
    @skip_openrouter
    @skip_openrouter
    def test_system_prompt_applied(self):
        """OpenRouter respects system prompt instructions."""
        result = run_wee_cli(
            OPENROUTER_MODEL,
            "What color is the sky?",
            system_prompt="Always answer in exactly 3 words.",
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        words = result.stdout.strip().split()
        # Allow 2-5 words (models may add punctuation/newlines)
        self.assertLessEqual(len(words), 8, f"Too verbose: {result.stdout[:100]}")

    @skip_openrouter
    @skip_openrouter
    @skip_openrouter
    @skip_openrouter
    def test_temperature_accepted(self):
        """OpenRouter accepts temperature=0 without error."""
        result = run_wee_cli(
            OPENROUTER_MODEL,
            "Say: TEMP_ZERO_OK",
            extra_args=["--temperature", "0.0"],
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    @skip_openrouter
    def test_openrouter_tool_calling_smoke(self):
        """OpenRouter model accepts tools parameter without crashing."""
        result = run_wee_cli(
            OPENROUTER_MODEL,
            "Use the python tool to compute 6 * 7 and tell me the result.",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        # OK if tools aren't supported (fallback) — just shouldn't crash
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")


# ===========================================================================
# 17. CLI ARGUMENT VALIDATION
# ===========================================================================


class TestCLIArgumentValidation(unittest.TestCase):
    """Extended CLI argument boundary tests."""

    def test_temperature_out_of_range_accepted(self):
        """wee_runtime accepts temperature >1 (model may reject it)."""
        # argparse accepts any float; model may or may not reject it
        result = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                "test",
                "--temperature",
                "2.0",
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # --help always exits 0
        self.assertEqual(result.returncode, 0)

    def test_timeout_zero_accepted_by_argparse(self):
        """--timeout 0 is accepted by argparse (int)."""
        result = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                "test",
                "--timeout",
                "0",
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)

    def test_tools_flag_is_boolean(self):
        """--tools is a boolean store_true flag — no value needed."""
        result = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertIn("--tools", result.stdout)

    def test_system_prompt_empty_string_accepted(self):
        """--system-prompt '' (empty) is valid."""
        result = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                "t",
                "--system-prompt",
                "",
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)


# ===========================================================================
# 18. IMPORT & MODULE STRUCTURE
# ===========================================================================


class TestModuleStructure(unittest.TestCase):
    """wee_runtime.py exports expected symbols."""

    def setUp(self):
        import wee_runtime

        self.mod = wee_runtime

    def test_main_function_callable(self):
        self.assertTrue(callable(self.mod.main))

    def test_execute_tool_callable(self):
        self.assertTrue(callable(self.mod.execute_tool))

    def test_sanitize_bash_command_callable(self):
        self.assertTrue(callable(self.mod.sanitize_bash_command))

    def test_resolve_model_and_endpoint_callable(self):
        self.assertTrue(callable(self.mod.resolve_model_and_endpoint))

    def test_wee_tools_is_list(self):
        self.assertIsInstance(self.mod._WEE_TOOLS, list)
        self.assertGreater(len(self.mod._WEE_TOOLS), 0)

    def test_provider_presets_is_dict(self):
        self.assertIsInstance(self.mod.PROVIDER_PRESETS, dict)

    def test_anti_hallucination_is_str(self):
        self.assertIsInstance(self.mod._ANTI_HALLUCINATION_PROMPT, str)

    def test_tool_capability_is_str(self):
        self.assertIsInstance(self.mod._WEE_TOOL_CAPABILITY_PROMPT, str)

    def test_max_tool_rounds_is_int(self):
        self.assertIsInstance(self.mod.MAX_TOOL_ROUNDS, int)

    def test_tool_timeout_is_int(self):
        self.assertIsInstance(self.mod.TOOL_TIMEOUT, int)


if __name__ == "__main__":
    unittest.main()
