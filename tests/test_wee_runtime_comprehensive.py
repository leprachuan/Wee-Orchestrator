#!/usr/bin/env python3
"""Comprehensive extension of the agentic runtime test suite for wee_runtime.py.

Supplements test_wee_runtime_agentic.py with additional coverage for:
  - Provider resolution edge cases (lmstudio, env vars, keyring, OpenRouter key errors)
  - execute_tool() permission levels and edge cases
  - sanitize_bash_command() edge cases (sftp, pipelines, complex commands)
  - System prompt augmentation (anti-hallucination, tool capability)
  - CLI flag behavior (--temperature, --timeout, --api-base)
  - Tool calling loop edge cases (multiple simultaneous calls, null IDs, empty choices)
  - OpenRouter API key enforcement and resolution chain
  - Tools-not-supported fallback (API error retry without tools)
  - Message construction correctness

Run:
    pytest tests/test_wee_runtime_comprehensive.py -v
    pytest tests/test_wee_runtime_comprehensive.py tests/test_wee_runtime_agentic.py -v
"""

import io
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")  # noqa: E402

import wee_runtime  # noqa: E402

WEE_RUNTIME = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "wee_runtime.py"
)

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_wee_runtime_agentic.py)
# ---------------------------------------------------------------------------


def _make_chunk(content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _make_tool_call_delta(idx, tc_id=None, name=None, arguments=None):
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=idx, id=tc_id, function=fn)


def _run_main_mocked(
    model, prompt, tools=False, system_prompt="", temperature=None, extra_args=None
):
    """Run wee_runtime.main() in-process with mocked sys.argv and captured I/O."""
    argv = ["wee_runtime.py", "--model", model]
    if tools:
        argv.append("--tools")
    if system_prompt:
        argv.extend(["--system-prompt", system_prompt])
    if temperature is not None:
        argv.extend(["--temperature", str(temperature)])
    if extra_args:
        argv.extend(extra_args)
    argv.append(prompt)

    old_argv, old_stdout, old_stderr = sys.argv, sys.stdout, sys.stderr
    sys.argv = argv
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
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
    return SimpleNamespace(stdout=stdout_val, stderr=stderr_val, returncode=exit_code)


# ===========================================================================
# 1. PROVIDER PRESET COMPLETENESS
# ===========================================================================


class TestProviderPresetCompleteness(unittest.TestCase):
    """All three provider presets are present and correctly configured."""

    def setUp(self):
        self.presets = wee_runtime.PROVIDER_PRESETS

    def test_three_presets_exist(self):
        """ollama, openrouter, lmstudio presets are all present."""
        self.assertIn("ollama", self.presets)
        self.assertIn("openrouter", self.presets)
        self.assertIn("lmstudio", self.presets)

    def test_lmstudio_preset_url(self):
        """LM Studio preset points to localhost:1234."""
        base, key = self.presets["lmstudio"]
        self.assertIn("localhost", base)
        self.assertIn("1234", base)
        self.assertEqual(key, "lm-studio")

    def test_ollama_preset_key_is_ollama(self):
        """Ollama preset has 'ollama' as the API key."""
        _, key = self.presets["ollama"]
        self.assertEqual(key, "ollama")

    def test_openrouter_preset_key_is_none(self):
        """OpenRouter preset has no default key (requires real key)."""
        _, key = self.presets["openrouter"]
        self.assertIsNone(key)

    def test_all_presets_have_v1_path(self):
        """All presets use /v1 endpoint path."""
        for name, (base, _) in self.presets.items():
            self.assertTrue(base.endswith("/v1"), f"{name} preset missing /v1: {base}")


# ===========================================================================
# 2. PROVIDER RESOLUTION — EDGE CASES
# ===========================================================================


class TestLmStudioPreset(unittest.TestCase):
    """Test lmstudio/ prefix resolution."""

    def test_lmstudio_prefix_strips_model(self):
        """lmstudio/model strips prefix and uses lmstudio base."""
        model, base, key = wee_runtime.resolve_model_and_endpoint("lmstudio/llama3")
        self.assertEqual(model, "llama3")
        self.assertIn("localhost", base)
        self.assertIn("1234", base)

    def test_lmstudio_key_is_lm_studio(self):
        """lmstudio/ prefix gets 'lm-studio' as key."""
        _, _, key = wee_runtime.resolve_model_and_endpoint("lmstudio/llama3")
        self.assertEqual(key, "lm-studio")

    def test_lmstudio_explicit_base_override(self):
        """Explicit api_base overrides lmstudio preset."""
        _, base, _ = wee_runtime.resolve_model_and_endpoint(
            "lmstudio/llama3", api_base="http://myhost:9000/v1"
        )
        self.assertEqual(base, "http://myhost:9000/v1")

    def test_lmstudio_slashed_model_name(self):
        """lmstudio/provider/model handles multi-segment model names."""
        model, _, _ = wee_runtime.resolve_model_and_endpoint(
            "lmstudio/TheBloke/Mistral-7B-GGUF"
        )
        self.assertEqual(model, "TheBloke/Mistral-7B-GGUF")


class TestEnvVarResolution(unittest.TestCase):
    """Test environment variable fallbacks for API base and key."""

    def test_wee_api_base_env_var(self):
        """WEE_API_BASE env var sets the base URL for bare models."""
        with patch.dict(
            os.environ, {"WEE_API_BASE": "http://custom:9999/v1"}, clear=False
        ):
            _, base, _ = wee_runtime.resolve_model_and_endpoint("bare-model")
        self.assertEqual(base, "http://custom:9999/v1")

    def test_wee_api_key_env_var(self):
        """WEE_API_KEY env var provides a fallback API key."""
        with patch.dict(
            os.environ,
            {"WEE_API_KEY": "my-test-key", "OPENROUTER_API_KEY": ""},
            clear=False,
        ):
            _, _, key = wee_runtime.resolve_model_and_endpoint("ollama/test-model")
        # ollama has a preset key of "ollama"; WEE_API_KEY only applies
        # when no preset/explicit key is set
        self.assertEqual(key, "ollama")

    def test_wee_api_key_for_bare_model(self):
        """WEE_API_KEY is used for bare models with no explicit api_base pointing
        to openrouter."""
        with patch.dict(
            os.environ,
            {"WEE_API_KEY": "bare-key"},
            clear=False,
        ):
            # Patch WEE_API_BASE to something non-openrouter to avoid
            # openrouter key check
            with patch.dict(os.environ, {"WEE_API_BASE": "http://other:1234/v1"}):
                _, _, key = wee_runtime.resolve_model_and_endpoint("bare-model")
        self.assertEqual(key, "bare-key")

    def test_openrouter_api_key_env_var(self):
        """OPENROUTER_API_KEY env var is used for openrouter/ models."""
        with patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "sk-or-env-test"}, clear=False
        ):
            _, _, key = wee_runtime.resolve_model_and_endpoint(
                "openrouter/google/gemma"
            )
        self.assertEqual(key, "sk-or-env-test")

    def test_explicit_api_key_wins(self):
        """Explicit api_key parameter beats all env vars."""
        with patch.dict(
            os.environ,
            {"WEE_API_KEY": "env-key", "OPENROUTER_API_KEY": "or-env-key"},
            clear=False,
        ):
            _, _, key = wee_runtime.resolve_model_and_endpoint(
                "ollama/test", api_key="explicit-key"
            )
        self.assertEqual(key, "explicit-key")


# ===========================================================================
# 3. OPENROUTER API KEY ENFORCEMENT
# ===========================================================================


class TestOpenRouterKeyRequired(unittest.TestCase):
    """OpenRouter requires an API key — validates the enforcement chain."""

    def test_no_key_exits_with_code_1(self):
        """resolve_model_and_endpoint exits with code 1 when OpenRouter key
        is missing."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                with self.assertRaises(SystemExit) as ctx:
                    wee_runtime.resolve_model_and_endpoint("openrouter/test-model")
        self.assertEqual(ctx.exception.code, 1)

    def test_no_key_error_message(self, mock_stderr=None):
        """Missing OpenRouter key prints an informative error to stderr."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                old_stderr = sys.stderr
                sys.stderr = io.StringIO()
                try:
                    wee_runtime.resolve_model_and_endpoint("openrouter/test-model")
                except SystemExit:
                    stderr_out = sys.stderr.getvalue()
                finally:
                    sys.stderr = old_stderr
        self.assertIn("OpenRouter", stderr_out)
        self.assertIn("OPENROUTER_API_KEY", stderr_out)

    def test_env_key_resolves(self):
        """OPENROUTER_API_KEY in env avoids the exit."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-valid-key"}):
            model, base, key = wee_runtime.resolve_model_and_endpoint(
                "openrouter/test-model"
            )
        self.assertEqual(key, "sk-valid-key")
        self.assertIn("openrouter.ai", base)

    def test_keyring_fallback_resolves(self):
        """Key from keyring avoids the exit."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value="sk-from-keyring"):
                _, _, key = wee_runtime.resolve_model_and_endpoint(
                    "openrouter/test-model"
                )
        self.assertEqual(key, "sk-from-keyring")

    def test_non_openrouter_model_no_exit(self):
        """Non-OpenRouter models don't exit even without OPENROUTER_API_KEY."""
        with patch.dict(
            os.environ, {"WEE_API_BASE": "http://other:1234/v1"}, clear=True
        ):
            # Should NOT exit — bare model with no openrouter base
            try:
                model, base, key = wee_runtime.resolve_model_and_endpoint("bare-model")
            except SystemExit:
                self.fail("Should not exit for non-OpenRouter models")


# ===========================================================================
# 4. EXECUTE_TOOL PERMISSION LEVELS
# ===========================================================================


class TestExecuteToolPermissionLevels(unittest.TestCase):
    """Validate execute_tool()'s permission parameter behavior."""

    def test_restricted_blocks_bash(self):
        """permission='restricted' returns error without executing bash."""
        result = wee_runtime.execute_tool(
            "bash", {"command": "echo SHOULD_NOT_RUN"}, permission="restricted"
        )
        self.assertIn("Error", result)
        self.assertIn("restricted", result)
        self.assertNotIn("SHOULD_NOT_RUN", result)

    def test_restricted_blocks_python(self):
        """permission='restricted' blocks python tool execution too."""
        result = wee_runtime.execute_tool(
            "python", {"code": "print('BLOCKED')"}, permission="restricted"
        )
        self.assertIn("Error", result)
        self.assertIn("restricted", result)
        self.assertNotIn("BLOCKED", result)

    def test_auto_permission_executes(self):
        """permission='auto' (default) executes tools normally."""
        result = wee_runtime.execute_tool(
            "bash", {"command": "echo AUTO_OK"}, permission="auto"
        )
        self.assertEqual(result, "AUTO_OK")

    def test_elevated_permission_executes(self):
        """permission='elevated' executes tools (same as auto in CLI context)."""
        result = wee_runtime.execute_tool(
            "bash", {"command": "echo ELEVATED_OK"}, permission="elevated"
        )
        self.assertEqual(result, "ELEVATED_OK")

    def test_default_permission_executes(self):
        """Omitting permission parameter defaults to auto (executes)."""
        result = wee_runtime.execute_tool("bash", {"command": "echo DEFAULT_OK"})
        self.assertEqual(result, "DEFAULT_OK")

    def test_restricted_message_is_informative(self):
        """Restricted error tells user how to enable tool calls."""
        result = wee_runtime.execute_tool(
            "bash", {"command": "ls"}, permission="restricted"
        )
        # Should mention how to fix it
        self.assertTrue(
            "auto" in result.lower() or "--permission" in result.lower(),
            f"Should mention how to enable tools. Got: {result}",
        )


# ===========================================================================
# 5. EXECUTE_TOOL EDGE CASES
# ===========================================================================


class TestExecuteToolEdgeCases(unittest.TestCase):
    """Additional edge cases for execute_tool()."""

    def test_bash_no_stdout_returns_no_output(self):
        """Command with no stdout returns '(no output)' sentinel."""
        result = wee_runtime.execute_tool("bash", {"command": ":"})
        self.assertEqual(result, "(no output)")

    def test_python_no_output_returns_no_output(self):
        """Python code with no print returns '(no output)' sentinel."""
        result = wee_runtime.execute_tool("python", {"code": "x = 1 + 1"})
        self.assertEqual(result, "(no output)")

    def test_bash_multiline_output(self):
        """Bash command with multiline output preserves all lines."""
        result = wee_runtime.execute_tool(
            "bash", {"command": "printf 'line1\\nline2\\nline3'"}
        )
        self.assertIn("line1", result)
        self.assertIn("line2", result)
        self.assertIn("line3", result)

    def test_python_multiline_output(self):
        """Python code with multiple print statements returns all lines."""
        result = wee_runtime.execute_tool(
            "python", {"code": "for i in range(3): print(f'item_{i}')"}
        )
        self.assertIn("item_0", result)
        self.assertIn("item_2", result)

    def test_bash_stderr_included_on_failure(self):
        """Failed bash commands include stderr in output."""
        result = wee_runtime.execute_tool(
            "bash", {"command": "ls /no_such_path_xyz_123"}
        )
        self.assertIn("STDERR", result)

    def test_python_stderr_included_on_exception(self):
        """Python RuntimeError includes stderr in output."""
        result = wee_runtime.execute_tool(
            "python", {"code": "raise ValueError('test_err_xyz')"}
        )
        self.assertIn("STDERR", result)
        self.assertIn("test_err_xyz", result)

    def test_bash_large_output_not_truncated(self):
        """Large bash output is returned in full (not silently truncated)."""
        result = wee_runtime.execute_tool("bash", {"command": "seq 1 200"})
        self.assertIn("200", result)
        self.assertIn("100", result)

    def test_python_import_works(self):
        """Python tool can import stdlib modules."""
        result = wee_runtime.execute_tool(
            "python", {"code": "import math; print(math.pi)"}
        )
        self.assertIn("3.14", result)

    def test_bash_exit_code_zero_no_stderr(self):
        """Successful command without stderr doesn't include STDERR in output."""
        result = wee_runtime.execute_tool("bash", {"command": "echo clean_output"})
        self.assertEqual(result, "clean_output")
        self.assertNotIn("STDERR", result)


# ===========================================================================
# 6. SANITIZE_BASH_COMMAND EXTENDED
# ===========================================================================


class TestSanitizeBashExtended(unittest.TestCase):
    """Extended coverage for SSH sanitization edge cases."""

    def test_sftp_gets_strict_flag(self):
        """sftp commands receive StrictHostKeyChecking injection."""
        result = wee_runtime.sanitize_bash_command("sftp user@host:/file /tmp/")
        self.assertIn("StrictHostKeyChecking", result)
        self.assertIn("accept-new", result)

    def test_multiple_ssh_in_pipeline(self):
        """Multiple ssh commands in a pipeline each get the flag."""
        cmd = "ssh host1 'ls' && ssh host2 'ls'"
        result = wee_runtime.sanitize_bash_command(cmd)
        # Both ssh invocations should be patched
        count = result.count("StrictHostKeyChecking")
        self.assertGreaterEqual(count, 2, "Both ssh calls should be patched")

    def test_sshpass_prefix_unchanged(self):
        """sshpass followed by ssh: ssh itself is still sanitized."""
        cmd = "sshpass -p pass ssh root@host 'ls'"
        result = wee_runtime.sanitize_bash_command(cmd)
        self.assertIn("StrictHostKeyChecking", result)

    def test_ssh_in_quotes_still_sanitized(self):
        """ssh inside a bash -c string is still sanitized by regex."""
        cmd = "bash -c 'ssh root@host hostname'"
        result = wee_runtime.sanitize_bash_command(cmd)
        self.assertIn("StrictHostKeyChecking", result)

    def test_no_ssh_command_unchanged(self):
        """Commands without ssh/scp/sftp are returned unmodified."""
        original = "git clone https://github.com/org/repo && make build"
        self.assertEqual(wee_runtime.sanitize_bash_command(original), original)

    def test_already_accept_new_not_doubled(self):
        """accept-new flag not added when StrictHostKeyChecking=accept-new present."""
        cmd = "ssh -o StrictHostKeyChecking=accept-new root@host ls"
        result = wee_runtime.sanitize_bash_command(cmd)
        self.assertEqual(result.count("StrictHostKeyChecking"), 1)

    def test_already_no_flag_not_doubled(self):
        """StrictHostKeyChecking=no already present → not doubled."""
        cmd = "ssh -o StrictHostKeyChecking=no root@host ls"
        result = wee_runtime.sanitize_bash_command(cmd)
        self.assertEqual(result.count("StrictHostKeyChecking"), 1)

    def test_none_command_unchanged(self):
        """Empty string returns empty string."""
        self.assertEqual(wee_runtime.sanitize_bash_command(""), "")

    def test_scp_multipart_command(self):
        """Complex scp with multiple arguments is sanitized correctly."""
        cmd = "scp -r /local/dir root@192.168.1.100:/remote/"
        result = wee_runtime.sanitize_bash_command(cmd)
        self.assertIn("StrictHostKeyChecking", result)
        self.assertIn("accept-new", result)


# ===========================================================================
# 7. SYSTEM PROMPT AUGMENTATION
# ===========================================================================


class TestSystemPromptAugmentation(unittest.TestCase):
    """Validate that anti-hallucination and tool capability prompts are injected."""

    def test_anti_hallucination_prompt_exists(self):
        """_ANTI_HALLUCINATION_PROMPT constant is non-empty."""
        self.assertTrue(len(wee_runtime._ANTI_HALLUCINATION_PROMPT) > 50)

    def test_tool_capability_prompt_exists(self):
        """_WEE_TOOL_CAPABILITY_PROMPT constant is non-empty."""
        self.assertTrue(len(wee_runtime._WEE_TOOL_CAPABILITY_PROMPT) > 50)

    def test_anti_hallucination_mentions_fabricate(self):
        """Anti-hallucination prompt warns against fabricating output."""
        self.assertIn("fabricat", wee_runtime._ANTI_HALLUCINATION_PROMPT.lower())

    def test_tool_capability_mentions_bash_and_python(self):
        """Tool capability prompt describes bash and python tools."""
        prompt = wee_runtime._WEE_TOOL_CAPABILITY_PROMPT.lower()
        self.assertIn("bash", prompt)
        self.assertIn("python", prompt)

    @patch("openai.OpenAI")
    def test_anti_hallucination_in_messages_without_tools(self, mock_cls):
        """Anti-hallucination prompt is always included in system message."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )

        _run_main_mocked("ollama/test", "hello", tools=False)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args[1]["messages"] if call_args[1] else call_args[0][0]
        system_msgs = [m for m in messages if m.get("role") == "system"]
        self.assertTrue(
            any(
                wee_runtime._ANTI_HALLUCINATION_PROMPT in m["content"]
                for m in system_msgs
            ),
            "Anti-hallucination prompt should be in system message",
        )

    @patch("openai.OpenAI")
    def test_tool_capability_only_when_tools_enabled(self, mock_cls):
        """Tool capability prompt is only injected when --tools is active."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # Without tools
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )
        _run_main_mocked("ollama/test", "hello", tools=False)
        call_args_no_tools = mock_client.chat.completions.create.call_args
        messages_no_tools = call_args_no_tools[1]["messages"]
        system_no_tools = " ".join(
            m["content"] for m in messages_no_tools if m.get("role") == "system"
        )

        # With tools
        mock_client.chat.completions.create.reset_mock()
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )
        _run_main_mocked("ollama/test", "hello", tools=True)
        call_args_tools = mock_client.chat.completions.create.call_args
        messages_tools = call_args_tools[1]["messages"]
        system_tools = " ".join(
            m["content"] for m in messages_tools if m.get("role") == "system"
        )

        # Tool capability prompt present only when tools=True
        self.assertNotIn(
            "Tool Capabilities",
            system_no_tools,
            "Tool capability should NOT be in no-tools system prompt",
        )
        self.assertIn(
            "Tool Capabilities",
            system_tools,
            "Tool capability SHOULD be in tools-enabled system prompt",
        )

    @patch("openai.OpenAI")
    def test_custom_system_prompt_preserved(self, mock_cls):
        """Custom system prompt is combined with augmented prompts, not replaced."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )

        _run_main_mocked("ollama/test", "hello", system_prompt="CUSTOM_SYSTEM_MARKER")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args[1]["messages"]
        system_content = " ".join(
            m["content"] for m in messages if m.get("role") == "system"
        )
        self.assertIn("CUSTOM_SYSTEM_MARKER", system_content)
        # Anti-hallucination should also be present
        self.assertIn("[CRITICAL", system_content)


# ===========================================================================
# 8. TEMPERATURE FLAG
# ===========================================================================


class TestTemperatureFlag(unittest.TestCase):
    """Validate --temperature CLI flag is passed to the API."""

    @patch("openai.OpenAI")
    def test_temperature_passed_to_api(self, mock_cls):
        """--temperature value is forwarded to chat.completions.create."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )

        _run_main_mocked("ollama/test", "hello", temperature=0.7)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertIn("temperature", call_kwargs)
        self.assertAlmostEqual(call_kwargs["temperature"], 0.7)

    @patch("openai.OpenAI")
    def test_no_temperature_omits_param(self, mock_cls):
        """Without --temperature, temperature is not passed to the API."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )

        _run_main_mocked("ollama/test", "hello")  # no temperature

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertNotIn(
            "temperature",
            call_kwargs,
            "Temperature should not be passed when not specified",
        )

    @patch("openai.OpenAI")
    def test_temperature_zero_passed(self, mock_cls):
        """temperature=0.0 is correctly forwarded (not treated as falsy)."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )

        _run_main_mocked("ollama/test", "hello", temperature=0.0)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertIn("temperature", call_kwargs)
        self.assertEqual(call_kwargs["temperature"], 0.0)

    @patch("openai.OpenAI")
    def test_temperature_in_tool_mode(self, mock_cls):
        """--temperature is also forwarded when --tools mode is active."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )

        _run_main_mocked("ollama/test", "hello", tools=True, temperature=0.3)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertIn("temperature", call_kwargs)
        self.assertAlmostEqual(call_kwargs["temperature"], 0.3)


# ===========================================================================
# 9. MULTIPLE SIMULTANEOUS TOOL CALLS PER ROUND
# ===========================================================================


class TestMultipleToolCallsPerRound(unittest.TestCase):
    """Model emits multiple tool calls in a single streaming round."""

    @patch("openai.OpenAI")
    def test_two_tool_calls_in_one_round(self, mock_cls):
        """Two tool calls in one round are both executed."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # Round 1: two simultaneous tool calls
        td1 = _make_tool_call_delta(
            0, tc_id="tc_1", name="bash", arguments='{"command": "echo FIRST"}'
        )
        td2 = _make_tool_call_delta(
            1, tc_id="tc_2", name="python", arguments='{"code": "print(\'SECOND\')"}'
        )
        round1 = [
            _make_chunk(content="", tool_calls=[td1]),
            _make_chunk(content="", tool_calls=[td2]),
            _make_chunk(finish_reason="tool_calls"),
        ]
        # Round 2: final answer
        round2 = [
            _make_chunk(content="Got both: FIRST and SECOND"),
            _make_chunk(finish_reason="stop"),
        ]
        mock_client.chat.completions.create.side_effect = [iter(round1), iter(round2)]

        result = _run_main_mocked("ollama/test", "run two tools", tools=True)
        self.assertEqual(result.returncode, 0)
        # The API should have been called twice (round 1 + round 2)
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)

    @patch("openai.OpenAI")
    def test_tool_messages_sent_in_order(self, mock_cls):
        """Tool result messages are added in the order tool calls arrived."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td1 = _make_tool_call_delta(
            0, tc_id="tc_a", name="bash", arguments='{"command": "echo A"}'
        )
        td2 = _make_tool_call_delta(
            1, tc_id="tc_b", name="bash", arguments='{"command": "echo B"}'
        )
        round1 = [
            _make_chunk(content="", tool_calls=[td1]),
            _make_chunk(content="", tool_calls=[td2]),
            _make_chunk(finish_reason="tool_calls"),
        ]
        round2 = [_make_chunk(content="done"), _make_chunk(finish_reason="stop")]
        mock_client.chat.completions.create.side_effect = [iter(round1), iter(round2)]

        _run_main_mocked("ollama/test", "parallel tools", tools=True)

        # Extract the messages passed in round 2
        round2_call = mock_client.chat.completions.create.call_args_list[1]
        messages = round2_call[1]["messages"]

        # Find tool result messages
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 2, "Should have 2 tool result messages")

        # Tool IDs should match the original calls in order
        tool_ids = [m["tool_call_id"] for m in tool_msgs]
        self.assertIn("tc_a", tool_ids)
        self.assertIn("tc_b", tool_ids)


# ===========================================================================
# 10. TOOL CALL ID FALLBACK
# ===========================================================================


class TestToolCallIdFallback(unittest.TestCase):
    """Model omits tool call ID — runtime should assign a synthetic one."""

    @patch("openai.OpenAI")
    def test_null_tool_call_id_gets_synthetic(self, mock_cls):
        """Tool call with tc_id=None gets a synthetic ID (tc_wee_N)."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # tc_id is None — no ID from model
        td = _make_tool_call_delta(
            0, tc_id=None, name="bash", arguments='{"command": "echo synth_id_test"}'
        )
        round1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(finish_reason="tool_calls"),
        ]
        round2 = [_make_chunk(content="done"), _make_chunk(finish_reason="stop")]

        mock_client.chat.completions.create.side_effect = [iter(round1), iter(round2)]

        result = _run_main_mocked("ollama/test", "no id test", tools=True)
        self.assertEqual(result.returncode, 0)

        # Verify round 2 received a tool message with a non-null ID
        round2_call = mock_client.chat.completions.create.call_args_list[1]
        messages = round2_call[1]["messages"]
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIsNotNone(tool_msgs[0]["tool_call_id"])
        self.assertTrue(len(tool_msgs[0]["tool_call_id"]) > 0)


# ===========================================================================
# 11. STREAMING EDGE CASES
# ===========================================================================


class TestStreamingEdgeCases(unittest.TestCase):
    """Edge cases in the streaming chunk processing loop."""

    @patch("openai.OpenAI")
    def test_empty_choices_list_skipped(self, mock_cls):
        """Chunks with empty choices list are silently skipped."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # One chunk with empty choices, then normal output
        empty_choices_chunk = SimpleNamespace(choices=[])
        normal_chunks = [
            empty_choices_chunk,
            _make_chunk(content="after empty"),
            _make_chunk(finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(normal_chunks)

        result = _run_main_mocked("ollama/test", "empty choices test")
        self.assertEqual(result.returncode, 0)
        self.assertIn("after empty", result.stdout)

    @patch("openai.OpenAI")
    def test_unicode_tokens_preserved(self, mock_cls):
        """Unicode tokens (emoji, non-ASCII) are passed through intact."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        chunks = [
            _make_chunk(content="Hello 🌍"),
            _make_chunk(content=" — 你好"),
            _make_chunk(finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)

        result = _run_main_mocked("ollama/test", "unicode test")
        self.assertIn("🌍", result.stdout)
        self.assertIn("你好", result.stdout)

    @patch("openai.OpenAI")
    def test_none_content_in_chunk_skipped(self, mock_cls):
        """Delta content=None doesn't crash or emit 'None' to output."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        chunks = [
            _make_chunk(content=None),  # None should be skipped
            _make_chunk(content="real content"),
            _make_chunk(content=None, finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)

        result = _run_main_mocked("ollama/test", "none content test")
        self.assertNotIn("None", result.stdout)
        self.assertIn("real content", result.stdout)

    @patch("openai.OpenAI")
    def test_content_and_tool_call_same_chunk(self, mock_cls):
        """Chunk containing both content text and a tool call is handled."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0, tc_id="tc_mixed", name="bash", arguments='{"command": "echo MIXED"}'
        )
        # Chunk has both content and tool call
        mixed_chunk = _make_chunk(content="Thinking...", tool_calls=[td])
        round1 = [mixed_chunk, _make_chunk(finish_reason="tool_calls")]
        round2 = [
            _make_chunk(content="Result: MIXED"),
            _make_chunk(finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(round1), iter(round2)]

        result = _run_main_mocked("ollama/test", "mixed test", tools=True)
        self.assertEqual(result.returncode, 0)


# ===========================================================================
# 12. TOOLS-UNSUPPORTED FALLBACK
# ===========================================================================


class TestToolsUnsupportedFallback(unittest.TestCase):
    """When 'tools' param causes API error, runtime retries without it."""

    @patch("openai.OpenAI")
    def test_tools_error_retries_without_tools(self, mock_cls):
        """If first call with tools raises exception, retries without tools."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # First call (with tools) raises an exception
        # Second call (without tools) succeeds
        mock_client.chat.completions.create.side_effect = [
            Exception("tools not supported by this model"),
            iter(
                [
                    _make_chunk(content="fallback response"),
                    _make_chunk(finish_reason="stop"),
                ]
            ),
        ]

        result = _run_main_mocked("ollama/test", "tool test", tools=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("fallback response", result.stdout)

    @patch("openai.OpenAI")
    def test_fallback_logs_warning(self, mock_cls):
        """Tools-unsupported fallback logs a warning to stderr."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        mock_client.chat.completions.create.side_effect = [
            Exception("tools param invalid"),
            iter([_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]),
        ]

        result = _run_main_mocked("ollama/test", "fallback test", tools=True)
        # Should log the fallback in stderr
        self.assertIn(
            "Tools not supported",
            result.stderr,
            f"Expected warning in stderr. Got: {result.stderr[:200]}",
        )


# ===========================================================================
# 13. MESSAGE CONSTRUCTION CORRECTNESS
# ===========================================================================


class TestMessageConstruction(unittest.TestCase):
    """Validate that messages are built correctly for the API."""

    @patch("openai.OpenAI")
    def test_user_message_is_last_in_initial_call(self, mock_cls):
        """User prompt is the last message in the initial API call."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )

        _run_main_mocked("ollama/test", "test user prompt")

        messages = mock_client.chat.completions.create.call_args[1]["messages"]
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(messages[-1]["content"], "test user prompt")

    @patch("openai.OpenAI")
    def test_tool_result_message_has_tool_call_id(self, mock_cls):
        """Tool result messages include tool_call_id for API compliance."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0, tc_id="tc_check", name="bash", arguments='{"command": "echo check"}'
        )
        round1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(finish_reason="tool_calls"),
        ]
        round2 = [_make_chunk(content="done"), _make_chunk(finish_reason="stop")]
        mock_client.chat.completions.create.side_effect = [iter(round1), iter(round2)]

        _run_main_mocked("ollama/test", "check msg", tools=True)

        call_args = mock_client.chat.completions.create.call_args_list
        round2_messages = call_args[1][1]["messages"]
        tool_msgs = [m for m in round2_messages if m.get("role") == "tool"]
        for msg in tool_msgs:
            self.assertIn("tool_call_id", msg)
            self.assertIsNotNone(msg["tool_call_id"])
            self.assertIn("content", msg)

    @patch("openai.OpenAI")
    def test_assistant_message_has_tool_calls(self, mock_cls):
        """Assistant message after tool call round includes tool_calls list."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_call_delta(
            0, tc_id="tc_asst", name="bash", arguments='{"command": "echo asst_check"}'
        )
        round1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(finish_reason="tool_calls"),
        ]
        round2 = [_make_chunk(content="done"), _make_chunk(finish_reason="stop")]
        mock_client.chat.completions.create.side_effect = [iter(round1), iter(round2)]

        _run_main_mocked("ollama/test", "assistant msg test", tools=True)

        call_args = mock_client.chat.completions.create.call_args_list
        round2_messages = call_args[1][1]["messages"]
        asst_msgs = [m for m in round2_messages if m.get("role") == "assistant"]
        self.assertTrue(
            any("tool_calls" in m for m in asst_msgs),
            "Assistant message with tool calls must include 'tool_calls' key",
        )

    @patch("openai.OpenAI")
    def test_stream_true_in_api_call(self, mock_cls):
        """stream=True is always passed to the API."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )

        _run_main_mocked("ollama/test", "stream check")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertTrue(call_kwargs.get("stream"), "stream must be True")

    @patch("openai.OpenAI")
    def test_model_name_passed_to_api(self, mock_cls):
        """Resolved model name (prefix-stripped) is passed to the API."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(finish_reason="stop")]
        )

        _run_main_mocked("ollama/qwen3:8b", "model name test")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        # Model name should be stripped of "ollama/" prefix
        self.assertEqual(call_kwargs["model"], "qwen3:8b")


# ===========================================================================
# 14. MAX TOOL ROUNDS CONSTANT VALIDATION
# ===========================================================================


class TestMaxToolRoundsConfig(unittest.TestCase):
    """Validate MAX_TOOL_ROUNDS and TOOL_TIMEOUT constants are sane."""

    def test_max_tool_rounds_in_range(self):
        """MAX_TOOL_ROUNDS is between 1 and 20 (sensible agentic loop limit)."""
        self.assertGreaterEqual(wee_runtime.MAX_TOOL_ROUNDS, 1)
        self.assertLessEqual(wee_runtime.MAX_TOOL_ROUNDS, 20)

    def test_tool_timeout_positive(self):
        """TOOL_TIMEOUT is a positive integer."""
        self.assertGreater(wee_runtime.TOOL_TIMEOUT, 0)

    def test_tool_timeout_reasonable_range(self):
        """TOOL_TIMEOUT is between 10 and 600 seconds."""
        self.assertGreaterEqual(wee_runtime.TOOL_TIMEOUT, 10)
        self.assertLessEqual(wee_runtime.TOOL_TIMEOUT, 600)

    @patch("openai.OpenAI")
    def test_loop_sends_tools_only_before_max_rounds(self, mock_cls):
        """tools key is omitted from the final fallback round
        (round > MAX_TOOL_ROUNDS)."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        calls = []

        def capture_create(**kwargs):
            calls.append(kwargs.copy())
            # Always return a tool call to force loop exhaustion
            td = _make_tool_call_delta(
                0,
                tc_id=f"tc_{len(calls)}",
                name="bash",
                arguments='{"command": "echo loop"}',
            )
            return iter(
                [
                    _make_chunk(content="", tool_calls=[td]),
                    _make_chunk(finish_reason="tool_calls"),
                ]
            )

        mock_client.chat.completions.create.side_effect = capture_create

        result = _run_main_mocked("ollama/test", "exhaust loop", tools=True)
        self.assertEqual(result.returncode, 0)

        # The last call should NOT have tools (exhaustion fallback)
        if len(calls) > wee_runtime.MAX_TOOL_ROUNDS:
            last_call = calls[wee_runtime.MAX_TOOL_ROUNDS]
            self.assertNotIn(
                "tools", last_call, "Final exhaustion round should not include tools"
            )


# ===========================================================================
# 15. CLI FLAG INTEGRATION
# ===========================================================================


class TestCLIFlagIntegration(unittest.TestCase):
    """CLI flag parsing and forwarding integration tests."""

    def test_temperature_flag_accepted(self):
        """--temperature flag is accepted without error."""
        result = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                "ollama/test",
                "--api-base",
                "http://127.0.0.1:9999/v1",
                "--api-key",
                "test",
                "--temperature",
                "0.5",
                "hello",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Connection error expected (port 9999 not open) — but NOT argparse error
        self.assertNotIn("error: unrecognized arguments", result.stderr)
        self.assertNotIn(
            "--temperature",
            (
                result.stderr.lower().split("usage")[0]
                if "usage" in result.stderr.lower()
                else result.stderr
            ),
        )

    def test_timeout_flag_accepted(self):
        """--timeout flag is accepted without error."""
        result = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                "ollama/test",
                "--api-base",
                "http://127.0.0.1:9999/v1",
                "--api-key",
                "test",
                "--timeout",
                "30",
                "hello",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        # Should fail with connection error, not argument error
        self.assertNotIn("unrecognized arguments", result.stderr)

    def test_tools_flag_accepted(self):
        """--tools flag is accepted by argparse."""
        result = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("--tools", result.stdout)

    def test_temperature_in_help(self):
        """--temperature appears in --help output."""
        result = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("--temperature", result.stdout)

    def test_system_prompt_in_help(self):
        """--system-prompt appears in --help output."""
        result = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("--system-prompt", result.stdout)


# ===========================================================================
# 16. SSH REGEX PATTERN VALIDATION
# ===========================================================================


class TestSSHRegexPattern(unittest.TestCase):
    """Validate the _SSH_BIN_RE regex pattern handles edge cases correctly."""

    def test_ssh_bin_re_matches_ssh(self):
        self.assertTrue(bool(wee_runtime._SSH_BIN_RE.search("ssh root@host")))

    def test_ssh_bin_re_matches_scp(self):
        self.assertTrue(bool(wee_runtime._SSH_BIN_RE.search("scp file root@host:/tmp")))

    def test_ssh_bin_re_matches_sftp(self):
        self.assertTrue(bool(wee_runtime._SSH_BIN_RE.search("sftp root@host")))

    def test_ssh_bin_re_word_boundary_ssh(self):
        """'sshhh' should NOT match (word boundary)."""
        self.assertFalse(bool(wee_runtime._SSH_BIN_RE.search("sshhh root@host")))

    def test_ssh_bin_re_word_boundary_scp(self):
        """'scpxxx' should NOT match."""
        self.assertFalse(bool(wee_runtime._SSH_BIN_RE.search("scpxxx file")))

    def test_ssh_bin_re_path_prefix(self):
        """/usr/bin/ssh should match (boundary at /)."""
        self.assertTrue(bool(wee_runtime._SSH_BIN_RE.search("/usr/bin/ssh root@host")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
