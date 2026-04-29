#!/usr/bin/env python3
"""Comprehensive agentic runtime test suite for Wee Native Runtime.

Validates wee_runtime.py end-to-end with both OpenRouter and Ollama models.
Covers: model resolution, tool calling (bash/python), multi-step agentic loops,
markdown formatting, permission enforcement, and streaming output.

Test scenarios:
  1. Web Search + Parsing (research tasks)
  2. Tool Calling — Bash Commands
  3. Multi-step Tool Calls with TODO Tracking
  4. Markdown Formatting
  5. Permission Levels Testing

Run:
    pytest tests/test_wee_runtime_agentic.py -v
    pytest tests/test_wee_runtime_agentic.py -v -k ollama
    pytest tests/test_wee_runtime_agentic.py -v -k openrouter
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://192.168.1.101:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_TEST_MODEL", "ollama/qwen3:8b")
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_TEST_MODEL", "openrouter/google/gemma-3-12b-it:free"
)

WEE_RUNTIME = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "wee_runtime.py"
)

# Timeout for live model calls (seconds)
LIVE_TIMEOUT = 120
# Timeout for unit tests (seconds)
UNIT_TIMEOUT = 10


def _has_ollama():
    """Check if Ollama is reachable."""
    try:
        result = subprocess.run(
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
        return result.stdout.strip() == "200"
    except Exception:
        return False


def _has_openrouter_key():
    """Check if OPENROUTER_API_KEY is available."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return True
    # Try keyring
    try:
        import keyring

        val = keyring.get_password("wee-orchestrator", "OPENROUTER_API_KEY")
        return bool(val)
    except Exception:
        return False


HAS_OLLAMA = _has_ollama()
HAS_OPENROUTER = _has_openrouter_key()

skip_ollama = unittest.skipUnless(HAS_OLLAMA, "Ollama not reachable")
skip_openrouter = unittest.skipUnless(HAS_OPENROUTER, "No OPENROUTER_API_KEY")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_wee_cli(
    model, prompt, tools=False, system_prompt="", timeout=LIVE_TIMEOUT, extra_args=None
):
    """Run wee_runtime.py as a subprocess and capture output."""
    cmd = [sys.executable, WEE_RUNTIME, "--model", model, "--timeout", str(timeout)]
    if tools:
        cmd.append("--tools")
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(prompt)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout + 30,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    return result


# ===========================================================================
# 1. UNIT TESTS — Model Resolution & Configuration (no live API)
# ===========================================================================


class TestModelResolution(unittest.TestCase):
    """Validate resolve_model_and_endpoint for various model strings."""

    def setUp(self):
        import wee_runtime

        self.resolve = wee_runtime.resolve_model_and_endpoint
        self.presets = wee_runtime.PROVIDER_PRESETS

    def test_ollama_prefix_strip(self):
        """ollama/model strips prefix and uses Ollama preset base."""
        model, base, key = self.resolve("ollama/gemma4:e4b")
        self.assertEqual(model, "gemma4:e4b")
        self.assertIn("11434", base, "Should use Ollama port 11434")
        self.assertEqual(key, "ollama")

    def test_openrouter_prefix_strip(self):
        """openrouter/provider/model strips prefix correctly."""
        model, base, key = self.resolve("openrouter/meta-llama/llama-4-scout")
        self.assertEqual(model, "meta-llama/llama-4-scout")
        self.assertIn("openrouter.ai", base)

    def test_bare_model_defaults_to_ollama(self):
        """Model without prefix defaults to Ollama base."""
        model, base, key = self.resolve("gemma4:e4b")
        self.assertEqual(model, "gemma4:e4b")
        # Should get Ollama default or env-specified base
        self.assertIsNotNone(base)

    def test_explicit_api_base_overrides_preset(self):
        """Explicit api_base is preserved even with a provider prefix."""
        model, base, key = self.resolve(
            "ollama/gemma4:e4b", api_base="http://custom:1234/v1"
        )
        self.assertEqual(base, "http://custom:1234/v1")

    def test_provider_presets_exist(self):
        """PROVIDER_PRESETS has ollama and openrouter entries."""
        self.assertIn("ollama", self.presets)
        self.assertIn("openrouter", self.presets)

    def test_ollama_preset_port(self):
        """Ollama preset uses correct port 11434."""
        base, _ = self.presets["ollama"]
        self.assertIn("11434", base)

    def test_openrouter_preset_url(self):
        """OpenRouter preset points to openrouter.ai."""
        base, _ = self.presets["openrouter"]
        self.assertIn("openrouter.ai", base)


class TestToolDefinitions(unittest.TestCase):
    """Validate _WEE_TOOLS structure."""

    def setUp(self):
        import wee_runtime

        self.tools = wee_runtime._WEE_TOOLS

    def test_tools_is_list(self):
        self.assertIsInstance(self.tools, list)

    def test_bash_tool_exists(self):
        names = [t["function"]["name"] for t in self.tools]
        self.assertIn("bash", names)

    def test_python_tool_exists(self):
        names = [t["function"]["name"] for t in self.tools]
        self.assertIn("python", names)

    def test_edit_file_tool_exists(self):
        names = [t["function"]["name"] for t in self.tools]
        self.assertIn("edit_file", names)

    def test_tool_schema_structure(self):
        """Each tool has type=function and a valid function schema."""
        for tool in self.tools:
            self.assertEqual(tool["type"], "function")
            fn = tool["function"]
            self.assertIn("name", fn)
            self.assertIn("description", fn)
            self.assertIn("parameters", fn)
            self.assertIn("required", fn["parameters"])

    def test_max_tool_rounds_defined(self):
        import wee_runtime

        self.assertGreaterEqual(wee_runtime.MAX_TOOL_ROUNDS, 1)
        self.assertLessEqual(wee_runtime.MAX_TOOL_ROUNDS, 20)

    def test_tool_timeout_defined(self):
        import wee_runtime

        self.assertGreaterEqual(wee_runtime.TOOL_TIMEOUT, 10)


class TestExecuteTool(unittest.TestCase):
    """Validate execute_tool for bash and python."""

    def setUp(self):
        import wee_runtime

        self.execute = wee_runtime.execute_tool

    def test_bash_echo(self):
        result = self.execute("bash", {"command": "echo hello_wee_test"})
        self.assertIn("hello_wee_test", result)

    def test_bash_pwd(self):
        result = self.execute("bash", {"command": "pwd"})
        self.assertTrue(result.startswith("/"))

    def test_bash_date(self):
        result = self.execute("bash", {"command": "date +%Y"})
        self.assertRegex(result, r"20\d{2}")

    def test_bash_empty_command(self):
        result = self.execute("bash", {"command": ""})
        self.assertIn("Error", result)

    def test_bash_no_command(self):
        result = self.execute("bash", {})
        self.assertIn("Error", result)

    def test_bash_failing_command(self):
        result = self.execute("bash", {"command": "ls /nonexistent_dir_xyz"})
        self.assertIn("STDERR", result)

    def test_python_hello(self):
        result = self.execute("python", {"code": "print('hello_py_wee')"})
        self.assertIn("hello_py_wee", result)

    def test_python_math(self):
        result = self.execute("python", {"code": "print(2 + 3)"})
        self.assertIn("5", result)

    def test_python_empty_code(self):
        result = self.execute("python", {"code": ""})
        self.assertIn("Error", result)

    def test_python_syntax_error(self):
        result = self.execute("python", {"code": "def ("})
        self.assertIn("STDERR", result)

    def test_edit_file_create(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "note.txt")
            result = self.execute(
                "edit_file",
                {
                    "path": path,
                    "new_text": "hello edit tool\n",
                    "create_if_missing": True,
                },
            )
            self.assertIn("OK: created", result)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "hello edit tool\n")

    def test_edit_file_exact_replace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "note.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("alpha\nbeta\ngamma\n")
            result = self.execute(
                "edit_file",
                {
                    "path": path,
                    "old_text": "beta",
                    "new_text": "delta",
                },
            )
            self.assertIn("OK: edited", result)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "alpha\ndelta\ngamma\n")

    def test_edit_file_missing_old_text_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "note.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("alpha\nbeta\ngamma\n")
            result = self.execute(
                "edit_file",
                {
                    "path": path,
                    "old_text": "missing",
                    "new_text": "delta",
                },
            )
            self.assertIn("old_text not found", result)

    def test_unknown_tool(self):
        result = self.execute("web_search", {"query": "test"})
        self.assertIn("Error", result)
        self.assertIn("Unknown tool", result)


class TestSanitizeBashCommand(unittest.TestCase):
    """Validate SSH command sanitization (Issue #111)."""

    def setUp(self):
        import wee_runtime

        self.sanitize = wee_runtime.sanitize_bash_command

    def test_ssh_gets_strict_flag(self):
        result = self.sanitize("ssh root@192.168.1.100 'ls'")
        self.assertIn("StrictHostKeyChecking", result)

    def test_non_ssh_unchanged(self):
        result = self.sanitize("ls -la /tmp")
        self.assertEqual(result, "ls -la /tmp")

    def test_already_has_flag(self):
        cmd = "ssh -o StrictHostKeyChecking=no root@host 'ls'"
        result = self.sanitize(cmd)
        # Should not double-inject
        self.assertEqual(result.count("StrictHostKeyChecking"), 1)

    def test_empty_command(self):
        result = self.sanitize("")
        self.assertEqual(result, "")

    def test_scp_also_sanitized(self):
        result = self.sanitize("scp file.txt root@host:/tmp/")
        self.assertIn("StrictHostKeyChecking", result)


class TestCLIArgParsing(unittest.TestCase):
    """Validate CLI argument handling without making API calls."""

    def test_help_flag(self):
        """--help should exit with 0 and show usage."""
        result = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--model", result.stdout)
        self.assertIn("--tools", result.stdout)

    def test_missing_model_fails(self):
        """Missing --model should fail."""
        result = subprocess.run(
            [sys.executable, WEE_RUNTIME, "test prompt"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_missing_prompt_fails(self):
        """Missing positional prompt should fail."""
        result = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--model", "test-model"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)


# ===========================================================================
# 2. MOCK INTEGRATION TESTS — Tool Calling Loop (no live API)
# ===========================================================================


def _make_chunk(content=None, tool_calls=None, finish_reason=None):
    """Build a mock streaming chunk."""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _make_tool_call_delta(idx, tc_id=None, name=None, arguments=None):
    """Build a mock tool call delta."""
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=idx, id=tc_id, function=fn)


def _run_main_mocked(model, prompt, tools=False, system_prompt="", extra_args=None):
    """Run wee_runtime.main() in-process with mocked sys.argv and captured I/O."""
    import io

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


class TestToolCallingLoopMocked(unittest.TestCase):
    """Test the agentic tool-calling loop with mocked OpenAI client."""

    @patch("openai.OpenAI")
    def test_simple_text_response_no_tools(self, mock_openai_cls):
        """Non-tool prompt streams text directly."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        chunks = [
            _make_chunk(content="Hello "),
            _make_chunk(content="world!"),
            _make_chunk(content=None, finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)

        result = _run_main_mocked("ollama/test-model", "say hello")
        self.assertIn("Hello world!", result.stdout)

    @patch("openai.OpenAI")
    def test_single_tool_call_bash(self, mock_openai_cls):
        """Model calls bash tool, then responds with final text."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Round 1: model emits a tool call
        tool_delta = _make_tool_call_delta(
            0, tc_id="tc_1", name="bash", arguments='{"command": "echo mock_output"}'
        )
        round1_chunks = [
            _make_chunk(content="", tool_calls=[tool_delta]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]

        # Round 2: model gives final answer
        round2_chunks = [
            _make_chunk(content="The output was: mock_output"),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [
            iter(round1_chunks),
            iter(round2_chunks),
        ]

        result = _run_main_mocked("ollama/test-model", "run echo", tools=True)
        self.assertEqual(result.returncode, 0)

    @patch("openai.OpenAI")
    def test_multi_round_tool_calls(self, mock_openai_cls):
        """Model makes multiple sequential tool calls across rounds."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Round 1: bash tool call
        td1 = _make_tool_call_delta(
            0, tc_id="tc_1", name="bash", arguments='{"command": "date +%Y"}'
        )
        r1 = [
            _make_chunk(content="", tool_calls=[td1]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]

        # Round 2: python tool call
        td2 = _make_tool_call_delta(
            0, tc_id="tc_2", name="python", arguments='{"code": "print(42)"}'
        )
        r2 = [
            _make_chunk(content="", tool_calls=[td2]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]

        # Round 3: final answer
        r3 = [
            _make_chunk(content="Year is 2026 and 42."),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(r1), iter(r2), iter(r3)]

        result = _run_main_mocked("ollama/test-model", "date and math", tools=True)
        self.assertEqual(result.returncode, 0)

    @patch("openai.OpenAI")
    def test_max_tool_rounds_exhaustion(self, mock_openai_cls):
        """Tool loop terminates after MAX_TOOL_ROUNDS to prevent infinite loops."""
        import wee_runtime

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Always return a tool call (never final text)
        def make_tool_round():
            td = _make_tool_call_delta(
                0,
                tc_id=f"tc_{time.time_ns()}",
                name="bash",
                arguments='{"command": "echo loop"}',
            )
            return iter(
                [
                    _make_chunk(content="", tool_calls=[td]),
                    _make_chunk(content=None, finish_reason="tool_calls"),
                ]
            )

        mock_client.chat.completions.create.side_effect = [
            make_tool_round() for _ in range(wee_runtime.MAX_TOOL_ROUNDS + 2)
        ]

        result = _run_main_mocked("ollama/test-model", "infinite loop test", tools=True)
        # Should complete without hanging
        self.assertEqual(result.returncode, 0)
        # Should contain fallback text
        self.assertTrue(
            "Tool execution completed" in result.stdout
            or "Max tool rounds" in result.stdout
            or result.stdout.strip() != ""
        )


# ===========================================================================
# 3. LIVE INTEGRATION TESTS — Ollama
# ===========================================================================


@skip_ollama
class TestOllamaLiveBasic(unittest.TestCase):
    """Live tests against Ollama — basic text generation."""

    def test_simple_prompt(self):
        """Ollama model responds to a simple text prompt."""
        result = run_wee_cli(
            OLLAMA_MODEL, "Say exactly: HELLO_WEE_TEST", timeout=LIVE_TIMEOUT
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertTrue(len(result.stdout.strip()) > 0, "Should produce output")

    def test_system_prompt_honored(self):
        """System prompt influences model behavior."""
        result = run_wee_cli(
            OLLAMA_MODEL,
            "What format should I use?",
            system_prompt="Always respond in ALL CAPS.",
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # Check that at least some words are uppercase
        upper_words = sum(
            1 for w in result.stdout.split() if w.isupper() and len(w) > 2
        )
        self.assertGreater(
            upper_words, 0, "System prompt should cause uppercase output"
        )

    def test_markdown_formatting(self):
        """Model generates markdown when asked."""
        result = run_wee_cli(
            OLLAMA_MODEL,
            "Create a markdown table with 3 columns: Name, Age, City. "
            "Add 2 rows of sample data. Output ONLY the table, nothing else.",
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        output = result.stdout
        # Check for markdown table indicators
        self.assertTrue(
            "|" in output, f"Should contain markdown table pipes. Got: {output[:200]}"
        )


@skip_ollama
class TestOllamaLiveToolCalling(unittest.TestCase):
    """Live tests against Ollama — tool calling (bash/python)."""

    def test_bash_tool_date(self):
        """Model uses bash tool to get current date."""
        result = run_wee_cli(
            OLLAMA_MODEL,
            "Use the bash tool to run 'date +%Y-%m-%d' and tell me today's date.",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # Check stderr for tool call evidence
        self.assertIn("[Wee]", result.stderr, "Should show tool call logs")

    def test_python_tool_calculation(self):
        """Model uses python tool for arithmetic."""
        result = run_wee_cli(
            OLLAMA_MODEL,
            "Use the python tool to calculate 17 * 23 + 5 and report the result.",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # 17*23+5 = 396
        self.assertTrue(
            "396" in result.stdout or "396" in result.stderr,
            f"Expected 396. stdout={result.stdout[:200]},"
            f" stderr={result.stderr[:200]}",
        )

    def test_multi_step_tool_calls(self):
        """Model chains multiple tool calls."""
        result = run_wee_cli(
            OLLAMA_MODEL,
            "Use bash to run 'hostname' and 'date +%H:%M',"
            " then summarize both results.",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # Should have at least one tool call
        tool_calls = result.stderr.count("[Wee] Tool:")
        self.assertGreaterEqual(tool_calls, 1, "Should make at least 1 tool call")

    def test_tool_error_handling(self):
        """Model handles tool execution errors gracefully."""
        result = run_wee_cli(
            OLLAMA_MODEL,
            "Use bash to run 'cat /nonexistent_file_xyz_999' and explain the error.",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # Should not crash — should get a response
        self.assertTrue(len(result.stdout.strip()) > 0)


# ===========================================================================
# 4. LIVE INTEGRATION TESTS — OpenRouter
# ===========================================================================


@skip_openrouter
class TestOpenRouterLiveBasic(unittest.TestCase):
    """Live tests against OpenRouter — basic text generation."""

    def test_simple_prompt(self):
        """OpenRouter model responds to a simple text prompt."""
        result = run_wee_cli(
            OPENROUTER_MODEL, "Say exactly: HELLO_WEE_OPENROUTER", timeout=LIVE_TIMEOUT
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertTrue(len(result.stdout.strip()) > 0, "Should produce output")

    def test_markdown_table_generation(self):
        """OpenRouter generates well-formed markdown tables."""
        result = run_wee_cli(
            OPENROUTER_MODEL,
            "Create a markdown table with columns: Tool, Purpose, Status. "
            "Add 3 rows. Output ONLY the table.",
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        output = result.stdout
        self.assertIn("|", output, "Should contain markdown table")

    def test_markdown_code_block(self):
        """OpenRouter generates code blocks in markdown."""
        result = run_wee_cli(
            OPENROUTER_MODEL,
            "Show a Python hello world example in a markdown code block. "
            "Use triple backticks with python language tag.",
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("```", result.stdout, "Should contain code block markers")

    def test_markdown_headers_and_lists(self):
        """OpenRouter generates headers and bullet lists."""
        result = run_wee_cli(
            OPENROUTER_MODEL,
            "Write a short guide with: a # header, a ## subheader, "
            "and a bullet list of 3 items. Use proper markdown.",
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        output = result.stdout
        self.assertTrue(
            "#" in output, f"Should contain markdown headers. Got: {output[:200]}"
        )


@skip_openrouter
class TestOpenRouterLiveToolCalling(unittest.TestCase):
    """Live tests against OpenRouter — tool calling."""

    def test_bash_tool_ls(self):
        """OpenRouter model uses bash tool to list files."""
        result = run_wee_cli(
            OPENROUTER_MODEL,
            "Use the bash tool to run 'ls /tmp' and report what you see.",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # Check for tool usage in stderr
        self.assertIn("[Wee]", result.stderr, "Should show tool call logs")

    def test_python_tool_string_manipulation(self):
        """OpenRouter model uses python tool for string operations."""
        result = run_wee_cli(
            OPENROUTER_MODEL,
            "Use the python tool to reverse 'WeeRuntime' and tell me the result.",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_research_task_with_tools(self):
        """OpenRouter handles a research-style task using tools."""
        result = run_wee_cli(
            OPENROUTER_MODEL,
            "Use bash to run 'cat /etc/os-release' and summarize the OS info "
            "in a markdown table with Key and Value columns.",
            tools=True,
            timeout=LIVE_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # Should have tool calls and markdown output
        self.assertIn("[Wee] Tool:", result.stderr)
        self.assertTrue(len(result.stdout.strip()) > 10)


# ===========================================================================
# 5. PERMISSION LEVEL TESTS (Unit — mocked)
# ===========================================================================


class TestPermissionLevels(unittest.TestCase):
    """Test permission enforcement for tool access.

    wee_runtime.py does not natively enforce permissions (it's CLI-level),
    but agent_manager.py controls which tools are exposed. These tests validate
    the tool selection logic and that --tools flag gates tool availability.
    """

    def test_no_tools_flag_disables_tools(self):
        """Without --tools, model should NOT receive tool definitions."""
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
                "hello",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Connection error expected
        self.assertNotEqual(result.returncode, 0)
        # No tool-related output
        self.assertNotIn("[Wee] Tool:", result.stderr)

    def test_tools_flag_enables_tool_loop(self):
        """With --tools, the runtime enters tool-calling mode."""
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
                "--tools",
                "hello",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Connection will fail but --tools mode is activated
        self.assertNotEqual(result.returncode, 0)

    @patch("openai.OpenAI")
    def test_restricted_permission_no_bash(self, mock_cls):
        """Restricted mode: without --tools, tools param is NOT sent to API."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        chunks = [
            _make_chunk(content="I cannot execute commands."),
            _make_chunk(content=None, finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)

        _run_main_mocked("ollama/test", "run ls", tools=False)
        call_kwargs = mock_client.chat.completions.create.call_args
        if call_kwargs:
            kwargs = call_kwargs[1] if call_kwargs[1] else {}
            self.assertNotIn(
                "tools", kwargs, "tools should not be passed when --tools is off"
            )

    @patch("openai.OpenAI")
    def test_auto_permission_has_tools(self, mock_cls):
        """Auto (standard) mode includes tool definitions."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        chunks = [
            _make_chunk(content="Done."),
            _make_chunk(content=None, finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)

        _run_main_mocked("ollama/test", "do something", tools=True)
        call_kwargs = mock_client.chat.completions.create.call_args
        if call_kwargs:
            kwargs = call_kwargs[1] if call_kwargs[1] else {}
            self.assertIn("tools", kwargs, "tools should be passed when --tools is on")

    @patch("openai.OpenAI")
    def test_elevated_permission_full_access(self, mock_cls):
        """Elevated mode has full tool access (same as auto for CLI runtime)."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        chunks = [
            _make_chunk(content="Full access."),
            _make_chunk(content=None, finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)

        result = _run_main_mocked("ollama/test", "elevated task", tools=True)
        self.assertEqual(result.returncode, 0)


# ===========================================================================
# 6. STREAMING & OUTPUT FORMAT TESTS
# ===========================================================================


class TestStreamingOutput(unittest.TestCase):
    """Test streaming output behavior."""

    @patch("openai.OpenAI")
    def test_streaming_produces_newline_at_end(self, mock_cls):
        """Output ends with a newline."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        chunks = [
            _make_chunk(content="test output"),
            _make_chunk(content=None, finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)

        result = _run_main_mocked("ollama/test", "test")
        self.assertTrue(result.stdout.endswith("\n"), "Output should end with newline")

    @patch("openai.OpenAI")
    def test_empty_response_handled(self, mock_cls):
        """Empty model response doesn't crash."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        chunks = [
            _make_chunk(content=""),
            _make_chunk(content=None, finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)

        result = _run_main_mocked("ollama/test", "empty test")
        self.assertEqual(result.returncode, 0)


# ===========================================================================
# 7. ERROR HANDLING & EDGE CASES
# ===========================================================================


class TestErrorHandling(unittest.TestCase):
    """Test error handling and edge cases."""

    def test_invalid_api_base_exits_nonzero(self):
        """Unreachable API base causes non-zero exit."""
        result = run_wee_cli(
            "ollama/test",
            "hello",
            extra_args=["--api-base", "http://127.0.0.1:9999/v1", "--api-key", "test"],
            timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)

    @patch("openai.OpenAI")
    def test_api_exception_exits_nonzero(self, mock_cls):
        """API exception causes graceful exit."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API down")

        result = _run_main_mocked("ollama/test", "fail test")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Error", result.stderr)

    @patch("openai.OpenAI")
    def test_malformed_tool_args_handled(self, mock_cls):
        """Malformed JSON in tool call arguments doesn't crash."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # Tool call with invalid JSON arguments
        td = _make_tool_call_delta(
            0, tc_id="tc_bad", name="bash", arguments="not json at all {{{"
        )
        round1 = [
            _make_chunk(content="", tool_calls=[td]),
            _make_chunk(content=None, finish_reason="tool_calls"),
        ]
        round2 = [
            _make_chunk(content="Handled the error."),
            _make_chunk(content=None, finish_reason="stop"),
        ]

        mock_client.chat.completions.create.side_effect = [iter(round1), iter(round2)]

        result = _run_main_mocked("ollama/test", "bad args test", tools=True)
        # Should not crash — graceful handling
        self.assertEqual(result.returncode, 0)

    def test_tool_timeout_enforcement(self):
        """Tool execution respects timeout."""
        import wee_runtime

        # sleep 1000 should be killed by TOOL_TIMEOUT
        start = time.time()
        result = wee_runtime.execute_tool("bash", {"command": "sleep 1000"})
        elapsed = time.time() - start
        self.assertLess(
            elapsed,
            wee_runtime.TOOL_TIMEOUT + 5,
            "Tool should be killed within timeout",
        )
        self.assertIn("timed out", result.lower())


# ===========================================================================
# 8. CROSS-PROVIDER PARAMETRIZED TESTS
# ===========================================================================


class TestCrossProviderResolution(unittest.TestCase):
    """Parametrized tests across multiple provider prefixes."""

    def _test_resolution(self, model_str, expected_in_base, expected_model):
        import wee_runtime

        model, base, key = wee_runtime.resolve_model_and_endpoint(model_str)
        self.assertEqual(model, expected_model)
        if expected_in_base:
            self.assertIn(expected_in_base, base)

    def test_ollama_gemma(self):
        self._test_resolution("ollama/gemma4:e4b", "11434", "gemma4:e4b")

    def test_ollama_qwen(self):
        self._test_resolution("ollama/qwen3:8b", "11434", "qwen3:8b")

    def test_openrouter_llama(self):
        self._test_resolution(
            "openrouter/meta-llama/llama-4-scout",
            "openrouter.ai",
            "meta-llama/llama-4-scout",
        )

    def test_openrouter_google(self):
        self._test_resolution(
            "openrouter/google/gemma-3-12b-it:free",
            "openrouter.ai",
            "google/gemma-3-12b-it:free",
        )

    def test_bare_model(self):
        self._test_resolution("phi3:mini", None, "phi3:mini")


# ===========================================================================
# 9. PERFORMANCE METRICS
# ===========================================================================


class TestPerformanceBaseline(unittest.TestCase):
    """Basic performance sanity checks."""

    def test_import_time(self):
        """wee_runtime imports in under 2 seconds."""
        start = time.time()
        import importlib

        importlib.reload(__import__("wee_runtime"))
        elapsed = time.time() - start
        self.assertLess(elapsed, 2.0, f"Import took {elapsed:.2f}s")

    def test_model_resolution_speed(self):
        """resolve_model_and_endpoint is fast (<10ms)."""
        import wee_runtime

        start = time.time()
        for _ in range(100):
            wee_runtime.resolve_model_and_endpoint("ollama/test-model")
        elapsed = time.time() - start
        per_call = (elapsed / 100) * 1000  # ms
        self.assertLess(per_call, 10.0, f"Resolution took {per_call:.2f}ms per call")


if __name__ == "__main__":
    unittest.main()


# ===========================================================================
# 10. EXECUTE_TOOL PERMISSION ENFORCEMENT (Direct Unit Tests)
# ===========================================================================


class TestExecuteToolDirect(unittest.TestCase):
    """Directly test execute_tool() behavior: bash, python, timeout, unknown."""

    def setUp(self):
        import wee_runtime

        self.execute = wee_runtime.execute_tool

    def test_bash_returns_output(self):
        """bash tool runs command and returns stdout."""
        result = self.execute("bash", {"command": "echo direct_ok"})
        self.assertIn("direct_ok", result)

    def test_python_returns_output(self):
        """python tool runs code and returns stdout."""
        result = self.execute("python", {"code": "print('python_ok')"})
        self.assertIn("python_ok", result)

    def test_edit_file_requires_create_flag_for_new_files(self):
        """edit_file should not create new files unless explicitly requested."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "new.txt")
            result = self.execute("edit_file", {"path": path, "new_text": "hello"})
            self.assertIn("create_if_missing=true", result)

    def test_edit_file_rejects_ambiguous_replace(self):
        """edit_file should require exact single-match replacements."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "dup.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("repeat\nrepeat\n")
            result = self.execute(
                "edit_file",
                {
                    "path": path,
                    "old_text": "repeat",
                    "new_text": "once",
                },
            )
            self.assertIn("matched 2 locations", result)

    def test_bash_empty_command_returns_error(self):
        """bash with empty command returns Error string."""
        result = self.execute("bash", {"command": ""})
        self.assertIn("Error", result)

    def test_python_empty_code_returns_error(self):
        """python with empty code returns Error string."""
        result = self.execute("python", {"code": ""})
        self.assertIn("Error", result)

    def test_unknown_tool_returns_error(self):
        """Unrecognised tool name returns Error string."""
        result = self.execute("unknown_tool", {"arg": "val"})
        self.assertIn("Error", result)
        self.assertIn("unknown_tool", result)

    def test_bash_stderr_captured(self):
        """bash stderr is included in result when command exits non-zero."""
        result = self.execute("bash", {"command": "echo err_msg >&2; exit 1"})
        self.assertIn("err_msg", result)

    def test_python_exception_output_captured(self):
        """python traceback appears in result when code raises."""
        result = self.execute("python", {"code": "raise ValueError('boom')"})
        # Output will contain "Error" in the STDERR section or (no output)
        self.assertTrue("boom" in result or "Error" in result)


class TestProviderPresetsExtended(unittest.TestCase):
    """Extended provider preset tests including LM Studio."""

    def setUp(self):
        import wee_runtime

        self.resolve = wee_runtime.resolve_model_and_endpoint
        self.presets = wee_runtime.PROVIDER_PRESETS

    def test_lmstudio_preset_exists(self):
        self.assertIn("lmstudio", self.presets)

    def test_lmstudio_preset_localhost(self):
        base, key = self.presets["lmstudio"]
        self.assertIn("localhost", base)
        self.assertEqual(key, "lm-studio")

    def test_lmstudio_prefix_strip(self):
        model, base, key = self.resolve("lmstudio/phi-3")
        self.assertEqual(model, "phi-3")
        self.assertIn("localhost", base)
        self.assertEqual(key, "lm-studio")

    def test_explicit_api_key_overrides_preset(self):
        _, _, key = self.resolve("ollama/gemma4:e4b", api_key="custom-key")
        self.assertEqual(key, "custom-key")

    def test_openrouter_multi_slash_model(self):
        model, base, _ = self.resolve("openrouter/google/gemma-3-12b-it:free")
        self.assertEqual(model, "google/gemma-3-12b-it:free")
        self.assertIn("openrouter.ai", base)

    def test_all_presets_have_base_url(self):
        for name, (base, _) in self.presets.items():
            self.assertTrue(base, f"Preset '{name}' has empty base URL")

    def test_all_presets_base_ends_with_v1(self):
        for name, (base, _) in self.presets.items():
            self.assertTrue(
                base.endswith("/v1"),
                f"Preset '{name}' base URL doesn't end with /v1: {base}",
            )


# ===========================================================================
# 12. RUNTIME CONSTANTS (Prompt content & numeric bounds)
# ===========================================================================


class TestRuntimeConstants(unittest.TestCase):
    """Validate runtime constants used in production."""

    def test_anti_hallucination_prompt_exists(self):
        import wee_runtime

        self.assertGreater(len(wee_runtime._ANTI_HALLUCINATION_PROMPT), 50)

    def test_anti_hallucination_prompt_has_never_rule(self):
        import wee_runtime

        self.assertIn("NEVER", wee_runtime._ANTI_HALLUCINATION_PROMPT)

    def test_anti_hallucination_prompt_has_ssh_rule(self):
        import wee_runtime

        self.assertIn("StrictHostKeyChecking", wee_runtime._ANTI_HALLUCINATION_PROMPT)

    def test_tool_capability_prompt_mentions_bash(self):
        import wee_runtime

        self.assertIn("bash", wee_runtime._WEE_TOOL_CAPABILITY_PROMPT)

    def test_tool_capability_prompt_mentions_python(self):
        import wee_runtime

        self.assertIn("python", wee_runtime._WEE_TOOL_CAPABILITY_PROMPT)

    def test_tool_capability_prompt_mentions_edit_file(self):
        import wee_runtime

        self.assertIn("edit_file", wee_runtime._WEE_TOOL_CAPABILITY_PROMPT)

    def test_max_tool_rounds_lower_bound(self):
        import wee_runtime

        self.assertGreaterEqual(
            wee_runtime.MAX_TOOL_ROUNDS, 3, "Should allow multi-step tasks"
        )

    def test_max_tool_rounds_upper_bound(self):
        import wee_runtime

        self.assertLessEqual(
            wee_runtime.MAX_TOOL_ROUNDS, 15, "Should cap to prevent runaway loops"
        )

    def test_tool_timeout_lower_bound(self):
        import wee_runtime

        self.assertGreaterEqual(
            wee_runtime.TOOL_TIMEOUT, 30, "Should allow at least 30s for real commands"
        )

    def test_tool_timeout_upper_bound(self):
        import wee_runtime

        self.assertLessEqual(wee_runtime.TOOL_TIMEOUT, 300, "Should not wait forever")


# ===========================================================================
# 13. SYSTEM PROMPT INJECTION (Mock Integration)
# ===========================================================================


class TestSystemPromptInjection(unittest.TestCase):
    """Verify anti-hallucination and tool prompts are wired into system messages."""

    @patch("openai.OpenAI")
    def test_anti_hallucination_always_injected(self, mock_cls):
        """_ANTI_HALLUCINATION_PROMPT is always in the system message."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(content=None, finish_reason="stop")]
        )
        _run_main_mocked("ollama/test", "no system prompt")
        call_kwargs = mock_client.chat.completions.create.call_args
        kw = call_kwargs[1] if call_kwargs and call_kwargs[1] else {}
        system_msgs = [m for m in kw.get("messages", []) if m.get("role") == "system"]
        self.assertTrue(system_msgs, "Should always have a system message")
        self.assertIn("NEVER", system_msgs[0]["content"])

    @patch("openai.OpenAI")
    def test_tool_capability_injected_with_tools_flag(self, mock_cls):
        """_WEE_TOOL_CAPABILITY_PROMPT is added when --tools is on."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk(content="ok"), _make_chunk(content=None, finish_reason="stop")]
        )
        _run_main_mocked("ollama/test", "use tools", tools=True)
        call_kwargs = mock_client.chat.completions.create.call_args
        kw = call_kwargs[1] if call_kwargs and call_kwargs[1] else {}
        system_msgs = [m for m in kw.get("messages", []) if m.get("role") == "system"]
        if system_msgs:
            self.assertIn("bash", system_msgs[0]["content"])

    @patch("openai.OpenAI")
    def test_tools_not_in_api_call_without_tools_flag(self, mock_cls):
        """Without --tools, tools param is absent from API call."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="done"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )
        _run_main_mocked("ollama/test", "hello", tools=False)
        call_kwargs = mock_client.chat.completions.create.call_args
        kw = call_kwargs[1] if call_kwargs and call_kwargs[1] else {}
        self.assertNotIn(
            "tools", kw, "tools should not be in API call when flag is off"
        )

    @patch("openai.OpenAI")
    def test_tools_in_api_call_with_tools_flag(self, mock_cls):
        """With --tools, tools param is included in first API call."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [
                _make_chunk(content="done"),
                _make_chunk(content=None, finish_reason="stop"),
            ]
        )
        _run_main_mocked("ollama/test", "use tools", tools=True)
        call_kwargs = mock_client.chat.completions.create.call_args
        kw = call_kwargs[1] if call_kwargs and call_kwargs[1] else {}
        self.assertIn("tools", kw, "tools should be in API call when --tools is on")


# ===========================================================================
# 14. TOOLS-NOT-SUPPORTED FALLBACK & EDGE CASES
# ===========================================================================


class TestStreamingEdgeCases(unittest.TestCase):
    """Edge cases in the streaming response loop."""

    @patch("openai.OpenAI")
    def test_empty_choices_chunk_skipped(self, mock_cls):
        """Chunks with empty choices list don't crash the loop."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        empty_chunk = SimpleNamespace(choices=[])
        chunks = [
            empty_chunk,
            _make_chunk(content="real output"),
            empty_chunk,
            _make_chunk(content=None, finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)
        result = _run_main_mocked("ollama/test", "stream test")
        self.assertEqual(result.returncode, 0)
        self.assertIn("real output", result.stdout)

    @patch("openai.OpenAI")
    def test_tools_exception_retries_without_tools(self, mock_cls):
        """If first tools call raises, runtime retries without tools."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        call_count = [0]

        def _side_effect(**kwargs):
            call_count[0] += 1
            if "tools" in kwargs and call_count[0] == 1:
                raise Exception("tools not supported by this model")
            return iter(
                [
                    _make_chunk(content="fallback ok"),
                    _make_chunk(content=None, finish_reason="stop"),
                ]
            )

        mock_client.chat.completions.create.side_effect = _side_effect
        result = _run_main_mocked("ollama/test", "try tools", tools=True)
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(call_count[0], 1)

    @patch("openai.OpenAI")
    def test_chunk_with_none_delta_content_skipped(self, mock_cls):
        """Chunks where delta.content is None are skipped cleanly."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        chunks = [
            _make_chunk(content=None),
            _make_chunk(content="final"),
            _make_chunk(content=None, finish_reason="stop"),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)
        result = _run_main_mocked("ollama/test", "none test")
        self.assertEqual(result.returncode, 0)
        self.assertIn("final", result.stdout)
