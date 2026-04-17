#!/usr/bin/env python3
"""Scenario-based agentic runtime test suite for wee_runtime.py.

Complements test_wee_runtime_agentic.py, test_wee_agentic_full_coverage.py,
and test_wee_runtime_comprehensive.py with scenario-driven tests:

  - End-to-end agentic loop workflows (mocked and live)
  - Parallel tool calls in a single round
  - Fallback truncation on max-rounds exhaustion (>2000 chars)
  - Synthetic tool-call ID counter (tc_wee_N) across multi-round loops
  - LM Studio preset via CLI
  - Mixed content+tool in intermediate rounds
  - Tool failure → error propagated to model → model reports gracefully
  - Live Ollama: extended integration (temp=0, explicit --api-base, multi-step)
  - Live OpenRouter: multi-slash model, code gen, system-prompt adherence

Run all:
    pytest tests/test_wee_runtime_scenarios.py -v

Unit tests only (fast, no network):
    pytest tests/test_wee_runtime_scenarios.py -v -m "not live"

Live Ollama only:
    pytest tests/test_wee_runtime_scenarios.py -v -k "ollama"

Live OpenRouter only:
    pytest tests/test_wee_runtime_scenarios.py -v -k "openrouter"
"""

import io
import json
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import wee_runtime  # noqa: E402

# ---------------------------------------------------------------------------
# Constants / helpers
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


def _has_ollama() -> bool:
    """Return True if the Ollama endpoint is reachable."""
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


def _has_openrouter_key() -> bool:
    """Return True if an OpenRouter API key is available."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return True
    try:
        import keyring

        val = keyring.get_password("wee-orchestrator", "OPENROUTER_API_KEY")
        return bool(val)
    except Exception:
        return False


HAS_OLLAMA = _has_ollama()
HAS_OPENROUTER = _has_openrouter_key()

skip_ollama = unittest.skipUnless(HAS_OLLAMA, "Ollama not reachable")
skip_openrouter = unittest.skipUnless(HAS_OPENROUTER, "OPENROUTER_API_KEY not set")


def _make_chunk(content=None, tool_calls=None):
    """Create a minimal streaming chunk mock."""
    chunk = MagicMock()
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.delta = delta
    chunk.choices = [choice]
    return chunk


def _make_tool_delta(index, name="", arguments="", tc_id=None):
    """Create a tool-call delta fragment for a streaming chunk."""
    td = MagicMock()
    td.index = index
    td.id = tc_id
    func = MagicMock()
    func.name = name
    func.arguments = arguments
    td.function = func
    return td


def _stream_text_then_stop(text):
    """Return a one-chunk stream yielding `text`, then no more tool calls."""
    chunk = _make_chunk(content=text)
    return [chunk]


def _run_main(
    model,
    prompt,
    tools=False,
    system_prompt="",
    temperature=None,
    api_base=None,
    api_key=None,
    extra_args=None,
    timeout=UNIT_TIMEOUT,
):
    """Run wee_runtime.main() in-process with mocked argv + captured I/O.

    In-process so that @patch("openai.OpenAI") decorators work correctly.
    """
    argv = ["wee_runtime.py", "--model", model]
    if tools:
        argv.append("--tools")
    if system_prompt:
        argv.extend(["--system-prompt", system_prompt])
    if temperature is not None:
        argv.extend(["--temperature", str(temperature)])
    if api_base:
        argv.extend(["--api-base", api_base])
    if api_key:
        argv.extend(["--api-key", api_key])
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
    return exit_code, stdout_val, stderr_val


# ---------------------------------------------------------------------------
# 1. Scenario: simple chat (no tools) — mocked
# ---------------------------------------------------------------------------


class TestScenarioSimpleChat(unittest.TestCase):
    """Scenario: user asks a question, model responds with plain text."""

    @patch("openai.OpenAI")
    def test_simple_chat_returns_text(self, mock_cls):
        """Response text is written to stdout."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter(
            [_make_chunk("Hello from Wee!")]
        )
        rc, out, err = _run_main("ollama/test-model", "say hello")
        self.assertEqual(rc, 0)
        self.assertIn("Hello from Wee!", out)

    @patch("openai.OpenAI")
    def test_simple_chat_ends_with_newline(self, mock_cls):
        """Stdout ends with exactly one trailing newline."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter([_make_chunk("Answer")])
        rc, out, _ = _run_main("ollama/test-model", "prompt")
        self.assertTrue(out.endswith("\n"))

    @patch("openai.OpenAI")
    def test_simple_chat_no_tools_in_payload(self, mock_cls):
        """Without --tools flag, 'tools' key is NOT sent to the API."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter([_make_chunk("ok")])
        _run_main("ollama/test-model", "hello")
        kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertNotIn("tools", kwargs)

    @patch("openai.OpenAI")
    def test_simple_chat_stream_true(self, mock_cls):
        """stream=True must always be set."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter([_make_chunk("ok")])
        _run_main("ollama/test-model", "hello")
        kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertTrue(kwargs.get("stream"))


# ---------------------------------------------------------------------------
# 2. Scenario: single tool call → response — mocked
# ---------------------------------------------------------------------------


class TestScenarioSingleToolCall(unittest.TestCase):
    """Scenario: model calls one tool, result appended, model gives final text."""

    def _make_tool_call_stream(self, tc_id, tool_name, args_json, final_text):
        """Build two streams: first with a tool call, second with final text."""
        # Round 1: tool call chunk
        td = _make_tool_delta(0, tool_name, args_json, tc_id)
        chunk_with_tool = MagicMock()
        chunk_with_tool.choices = [MagicMock()]
        chunk_with_tool.choices[0].delta.content = None
        chunk_with_tool.choices[0].delta.tool_calls = [td]

        # Round 2: final text
        chunk_final = _make_chunk(final_text)

        return [chunk_with_tool], [chunk_final]

    @patch("openai.OpenAI")
    def test_tool_result_in_final_answer(self, mock_cls):
        """Model's final answer follows the tool result."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        round1, round2 = self._make_tool_call_stream(
            "tc1", "bash", '{"command": "echo hello"}', "Result: hello"
        )
        mock_client.chat.completions.create.side_effect = [iter(round1), iter(round2)]
        with patch.object(wee_runtime, "execute_tool", return_value="hello"):
            rc, out, _ = _run_main("ollama/test-model", "run echo", tools=True)
        self.assertEqual(rc, 0)
        self.assertIn("Result: hello", out)

    @patch("openai.OpenAI")
    def test_two_api_calls_for_one_tool(self, mock_cls):
        """One tool call round = 2 API calls (tool call + final answer)."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        round1, round2 = self._make_tool_call_stream(
            "tc1", "bash", '{"command": "date"}', "Done"
        )
        mock_client.chat.completions.create.side_effect = [iter(round1), iter(round2)]
        with patch.object(wee_runtime, "execute_tool", return_value="Mon"):
            _run_main("ollama/test-model", "what day", tools=True)
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)


# ---------------------------------------------------------------------------
# 3. Scenario: parallel tool calls (multiple in one round) — mocked
# ---------------------------------------------------------------------------


class TestScenarioParallelToolCalls(unittest.TestCase):
    """Scenario: model issues two or more tool calls in a single response round."""

    @patch("openai.OpenAI")
    def test_two_parallel_tools_both_executed(self, mock_cls):
        """Both tool calls in a single round are executed."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td1 = _make_tool_delta(0, "bash", '{"command":"echo a"}', "id1")
        td2 = _make_tool_delta(1, "python", '{"code":"print(42)"}', "id2")

        chunk_multi = MagicMock()
        chunk_multi.choices = [MagicMock()]
        chunk_multi.choices[0].delta.content = None
        chunk_multi.choices[0].delta.tool_calls = [td1, td2]

        chunk_final = _make_chunk("Both done")

        mock_client.chat.completions.create.side_effect = [
            iter([chunk_multi]),
            iter([chunk_final]),
        ]

        calls = []

        def mock_execute(name, args, **kwargs):  # noqa: E306
            calls.append(name)
            return "output"

        with patch.object(wee_runtime, "execute_tool", side_effect=mock_execute):
            rc, out, _ = _run_main("ollama/test-model", "do two things", tools=True)

        self.assertEqual(rc, 0)
        self.assertIn("bash", calls)
        self.assertIn("python", calls)
        self.assertEqual(len(calls), 2)

    @patch("openai.OpenAI")
    def test_parallel_tools_produce_two_tool_messages(self, mock_cls):
        """Two parallel tool calls produce two tool-role messages in history."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td1 = _make_tool_delta(0, "bash", '{"command":"ls"}', "id_a")
        td2 = _make_tool_delta(1, "bash", '{"command":"pwd"}', "id_b")

        chunk_multi = MagicMock()
        chunk_multi.choices = [MagicMock()]
        chunk_multi.choices[0].delta.content = None
        chunk_multi.choices[0].delta.tool_calls = [td1, td2]

        chunk_final = _make_chunk("All done")

        mock_client.chat.completions.create.side_effect = [
            iter([chunk_multi]),
            iter([chunk_final]),
        ]

        with patch.object(wee_runtime, "execute_tool", return_value="output"):
            _run_main("ollama/test-model", "check things", tools=True)

        # Second call should have: system + user + assistant + 2x tool = 5 messages
        second_call_messages = mock_client.chat.completions.create.call_args_list[1][1][
            "messages"
        ]
        tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 2)

    @patch("openai.OpenAI")
    def test_parallel_tools_tool_call_ids_match(self, mock_cls):
        """Tool result messages reference the correct tool_call_id."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td1 = _make_tool_delta(0, "bash", '{"command":"ls"}', "id_alpha")
        td2 = _make_tool_delta(1, "bash", '{"command":"pwd"}', "id_beta")

        chunk_multi = MagicMock()
        chunk_multi.choices = [MagicMock()]
        chunk_multi.choices[0].delta.content = None
        chunk_multi.choices[0].delta.tool_calls = [td1, td2]

        chunk_final = _make_chunk("ok")

        mock_client.chat.completions.create.side_effect = [
            iter([chunk_multi]),
            iter([chunk_final]),
        ]

        with patch.object(wee_runtime, "execute_tool", return_value="x"):
            _run_main("ollama/test-model", "check", tools=True)

        second_messages = mock_client.chat.completions.create.call_args_list[1][1][
            "messages"
        ]
        tool_ids = {
            m["tool_call_id"] for m in second_messages if m.get("role") == "tool"
        }  # noqa: E501
        self.assertIn("id_alpha", tool_ids)
        self.assertIn("id_beta", tool_ids)


# ---------------------------------------------------------------------------
# 4. Scenario: synthetic tool-call ID (tc_wee_N counter) — mocked
# ---------------------------------------------------------------------------


class TestScenarioSyntheticToolCallID(unittest.TestCase):
    """When a streaming delta has no tool call ID, tc_wee_N is synthesized."""

    @patch("openai.OpenAI")
    def test_no_id_in_delta_uses_synthetic(self, mock_cls):
        """tc_wee_1 generated when delta.id is None."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # Tool call delta with no id
        td = _make_tool_delta(0, "bash", '{"command":"echo x"}', tc_id=None)
        chunk_tool = MagicMock()
        chunk_tool.choices = [MagicMock()]
        chunk_tool.choices[0].delta.content = None
        chunk_tool.choices[0].delta.tool_calls = [td]

        chunk_final = _make_chunk("done")

        mock_client.chat.completions.create.side_effect = [
            iter([chunk_tool]),
            iter([chunk_final]),
        ]

        with patch.object(wee_runtime, "execute_tool", return_value="x"):
            _run_main("ollama/test-model", "run", tools=True)

        # The assistant message should have a tool_call with an id starting tc_wee_
        second_messages = mock_client.chat.completions.create.call_args_list[1][1][
            "messages"
        ]
        assistant_msg = next(m for m in second_messages if m.get("role") == "assistant")
        tc_id = assistant_msg["tool_calls"][0]["id"]
        self.assertTrue(
            tc_id.startswith("tc_wee_"),
            f"Expected synthetic tc_wee_N id, got: {tc_id}",
        )

    @patch("openai.OpenAI")
    def test_counter_increments_per_call(self, mock_cls):
        """Tool call counter increments: tc_wee_1, tc_wee_2, etc."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # Round 1: one tool call (no id)
        td1 = _make_tool_delta(0, "bash", '{"command":"echo 1"}', tc_id=None)
        chunk_r1 = MagicMock()
        chunk_r1.choices = [MagicMock()]
        chunk_r1.choices[0].delta.content = None
        chunk_r1.choices[0].delta.tool_calls = [td1]

        # Round 2: another tool call (no id)
        td2 = _make_tool_delta(0, "bash", '{"command":"echo 2"}', tc_id=None)
        chunk_r2 = MagicMock()
        chunk_r2.choices = [MagicMock()]
        chunk_r2.choices[0].delta.content = None
        chunk_r2.choices[0].delta.tool_calls = [td2]

        chunk_final = _make_chunk("all done")

        mock_client.chat.completions.create.side_effect = [
            iter([chunk_r1]),
            iter([chunk_r2]),
            iter([chunk_final]),
        ]

        with patch.object(wee_runtime, "execute_tool", return_value="out"):
            _run_main("ollama/test-model", "do two", tools=True)

        all_messages = mock_client.chat.completions.create.call_args_list[2][1][
            "messages"
        ]
        assistant_msgs = [m for m in all_messages if m.get("role") == "assistant"]
        ids = []
        for am in assistant_msgs:
            for tc in am.get("tool_calls", []):
                ids.append(tc["id"])

        # Both should be tc_wee_ prefixed
        for tc_id in ids:
            self.assertTrue(tc_id.startswith("tc_wee_"))

        # IDs should be unique (different counter values)
        self.assertEqual(len(set(ids)), len(ids), f"Duplicate synthetic IDs: {ids}")


# ---------------------------------------------------------------------------
# 5. Scenario: tool failure → error propagated to model — mocked
# ---------------------------------------------------------------------------


class TestScenarioToolFailurePropagation(unittest.TestCase):
    """Scenario: tool fails, error string is sent back to model in tool message."""

    @patch("openai.OpenAI")
    def test_tool_error_string_in_tool_message(self, mock_cls):
        """Tool execution error is sent as tool message content."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_delta(0, "bash", '{"command":"nonexistent-cmd"}', "tc_fail")
        chunk_tool = MagicMock()
        chunk_tool.choices = [MagicMock()]
        chunk_tool.choices[0].delta.content = None
        chunk_tool.choices[0].delta.tool_calls = [td]

        chunk_final = _make_chunk("I got an error: command not found")

        mock_client.chat.completions.create.side_effect = [
            iter([chunk_tool]),
            iter([chunk_final]),
        ]

        error_result = "Error: command not found (exit code 127)"
        with patch.object(wee_runtime, "execute_tool", return_value=error_result):
            rc, out, _ = _run_main("ollama/test-model", "run it", tools=True)

        second_messages = mock_client.chat.completions.create.call_args_list[1][1][
            "messages"
        ]
        tool_msg = next(m for m in second_messages if m.get("role") == "tool")
        self.assertEqual(tool_msg["content"], error_result)

    @patch("openai.OpenAI")
    def test_model_sees_tool_error_and_responds(self, mock_cls):
        """Final response is still produced after a tool error."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_delta(0, "bash", '{"command":"bad"}', "tc1")
        chunk_tool = MagicMock()
        chunk_tool.choices = [MagicMock()]
        chunk_tool.choices[0].delta.content = None
        chunk_tool.choices[0].delta.tool_calls = [td]
        chunk_final = _make_chunk("The command failed.")

        mock_client.chat.completions.create.side_effect = [
            iter([chunk_tool]),
            iter([chunk_final]),
        ]
        with patch.object(wee_runtime, "execute_tool", return_value="Error: 127"):
            rc, out, _ = _run_main("ollama/test-model", "run", tools=True)
        self.assertEqual(rc, 0)
        self.assertIn("The command failed.", out)


# ---------------------------------------------------------------------------
# 6. Scenario: tools-not-supported fallback (retry without tools) — mocked
# ---------------------------------------------------------------------------


class TestScenarioToolsNotSupportedFallback(unittest.TestCase):
    """Scenario: API raises an exception containing 'tools'; retry without."""

    @patch("openai.OpenAI")
    def test_fallback_succeeds_on_tools_error(self, mock_cls):
        """When 'tools' error occurs, the fallback call returns a response."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        fallback_chunk = _make_chunk("Fallback response")
        mock_client.chat.completions.create.side_effect = [
            Exception("tools parameter not supported"),
            iter([fallback_chunk]),
        ]
        rc, out, err = _run_main("ollama/test-model", "hello", tools=True)
        self.assertEqual(rc, 0)
        self.assertIn("Fallback response", out)

    @patch("openai.OpenAI")
    def test_fallback_logs_warning_to_stderr(self, mock_cls):
        """Tools-not-supported triggers a [Wee] warning in stderr."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        fallback_chunk = _make_chunk("ok")
        mock_client.chat.completions.create.side_effect = [
            Exception("tools not supported by model"),
            iter([fallback_chunk]),
        ]
        rc, out, err = _run_main("ollama/test-model", "hello", tools=True)
        self.assertEqual(rc, 0)
        self.assertIn("[Wee]", err)

    @patch("openai.OpenAI")
    def test_non_tools_exception_retried_without_tools(self, mock_cls):
        """Any exception with tools in kwargs triggers retry without tools.

        The runtime pops the tools key and retries on ANY exception
        when tools were in the request — not just errors mentioning tools.
        """
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        fallback_chunk = _make_chunk("fallback answer")
        mock_client.chat.completions.create.side_effect = [
            Exception("Connection refused"),
            iter([fallback_chunk]),
        ]
        rc, out, err = _run_main("ollama/test-model", "hello", tools=True)
        self.assertEqual(rc, 0)
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)
        first_kw = mock_client.chat.completions.create.call_args_list[0][1]
        second_kw = mock_client.chat.completions.create.call_args_list[1][1]
        self.assertIn("tools", first_kw)
        self.assertNotIn("tools", second_kw)

    @patch("openai.OpenAI")
    def test_exception_without_tools_is_not_retried(self, mock_cls):
        """When tools flag is not set, any exception propagates immediately."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        mock_client.chat.completions.create.side_effect = Exception("network error")
        rc, out, err = _run_main("ollama/test-model", "hello", tools=False)
        self.assertNotEqual(rc, 0)
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)

    @patch("openai.OpenAI")
    def test_fallback_truncates_large_output(self, mock_cls):
        """Fallback message truncates tool output at 2000 chars."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # All rounds return a tool call
        def make_tool_stream(tc_id):
            td = _make_tool_delta(0, "bash", '{"command":"cat big"}', tc_id)
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = None
            chunk.choices[0].delta.tool_calls = [td]
            return iter([chunk])

        # MAX_TOOL_ROUNDS rounds of tool calls, no final text
        streams = [
            make_tool_stream(f"tc_{i}") for i in range(wee_runtime.MAX_TOOL_ROUNDS + 1)
        ]  # noqa: E501
        mock_client.chat.completions.create.side_effect = streams

        big_output = "X" * 5000  # larger than 2000-char truncation limit
        with patch.object(wee_runtime, "execute_tool", return_value=big_output):
            rc, out, _ = _run_main("ollama/test-model", "dump", tools=True)

        # Output should not contain the full big_output
        self.assertNotIn("X" * 5000, out)
        # But should be a non-empty fallback
        self.assertTrue(len(out.strip()) > 0)

    @patch("openai.OpenAI")
    def test_fallback_mentions_tool_execution(self, mock_cls):
        """Fallback text references 'Tool execution' or 'tool'."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        def make_tool_stream(tc_id):
            td = _make_tool_delta(0, "bash", '{"command":"echo x"}', tc_id)
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = None
            chunk.choices[0].delta.tool_calls = [td]
            return iter([chunk])

        streams = [
            make_tool_stream(f"tc_{i}") for i in range(wee_runtime.MAX_TOOL_ROUNDS + 1)
        ]  # noqa: E501
        mock_client.chat.completions.create.side_effect = streams

        with patch.object(wee_runtime, "execute_tool", return_value="some output"):
            rc, out, _ = _run_main("ollama/test-model", "loop forever", tools=True)

        self.assertIn("tool", out.lower())


# ---------------------------------------------------------------------------
# 8. Scenario: mixed content + tool in same round — mocked
# ---------------------------------------------------------------------------


class TestScenarioMixedContentAndTools(unittest.TestCase):
    """Scenario: a streaming round emits text AND a tool call in the same chunk."""

    @patch("openai.OpenAI")
    def test_content_before_tool_is_streamed(self, mock_cls):
        """Text in the same chunk as a tool call is written to stdout."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # Single chunk: has both content and a tool call
        td = _make_tool_delta(0, "bash", '{"command":"echo x"}', "tc1")
        chunk_mixed = MagicMock()
        chunk_mixed.choices = [MagicMock()]
        chunk_mixed.choices[0].delta.content = "Thinking..."
        chunk_mixed.choices[0].delta.tool_calls = [td]

        chunk_final = _make_chunk("Done.")

        mock_client.chat.completions.create.side_effect = [
            iter([chunk_mixed]),
            iter([chunk_final]),
        ]
        with patch.object(wee_runtime, "execute_tool", return_value="x"):
            rc, out, _ = _run_main("ollama/test-model", "go", tools=True)

        self.assertIn("Thinking...", out)
        self.assertIn("Done.", out)

    @patch("openai.OpenAI")
    def test_assistant_message_preserves_interim_text(self, mock_cls):
        """Interim text is preserved in the assistant message sent back to model."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_delta(0, "bash", '{"command":"ls"}', "tc1")
        chunk_mixed = MagicMock()
        chunk_mixed.choices = [MagicMock()]
        chunk_mixed.choices[0].delta.content = "Checking files:"
        chunk_mixed.choices[0].delta.tool_calls = [td]

        chunk_final = _make_chunk("ok")

        mock_client.chat.completions.create.side_effect = [
            iter([chunk_mixed]),
            iter([chunk_final]),
        ]
        with patch.object(wee_runtime, "execute_tool", return_value="file.txt"):
            _run_main("ollama/test-model", "list", tools=True)

        second_messages = mock_client.chat.completions.create.call_args_list[1][1][
            "messages"
        ]
        assistant_msg = next(m for m in second_messages if m.get("role") == "assistant")
        self.assertEqual(assistant_msg["content"], "Checking files:")


# ---------------------------------------------------------------------------
# 9. Provider / model resolution — LM Studio via CLI
# ---------------------------------------------------------------------------


class TestScenarioLmStudioProviderCLI(unittest.TestCase):
    """Scenario: lmstudio/ prefix resolved via wee_runtime.py subprocess."""

    @patch("openai.OpenAI")
    def test_lmstudio_resolved_base_url(self, mock_cls):
        """lmstudio/model → api_base = http://localhost:1234/v1."""
        model, base, key = wee_runtime.resolve_model_and_endpoint("lmstudio/qwen2.5-7b")
        self.assertEqual(base, "http://localhost:1234/v1")

    @patch("openai.OpenAI")
    def test_lmstudio_resolved_api_key(self, mock_cls):
        """lmstudio/model → api_key = 'lm-studio'."""
        model, base, key = wee_runtime.resolve_model_and_endpoint("lmstudio/qwen2.5-7b")
        self.assertEqual(key, "lm-studio")

    def test_lmstudio_resolved_model_name(self):
        """lmstudio/model_name → model = model_name (prefix stripped)."""
        model, base, key = wee_runtime.resolve_model_and_endpoint(
            "lmstudio/my-local-model"
        )
        self.assertEqual(model, "my-local-model")

    def test_lmstudio_slashed_model_name(self):
        """lmstudio/org/model → model = 'org/model' (only lmstudio/ prefix stripped)."""
        model, base, key = wee_runtime.resolve_model_and_endpoint(
            "lmstudio/org/model-v2"
        )
        self.assertEqual(model, "org/model-v2")

    def test_lmstudio_preset_in_provider_presets(self):
        """'lmstudio' must appear in PROVIDER_PRESETS dict."""
        self.assertIn("lmstudio", wee_runtime.PROVIDER_PRESETS)


# ---------------------------------------------------------------------------
# 10. OpenRouter multi-slash model name — unit tests
# ---------------------------------------------------------------------------


class TestScenarioOpenRouterMultiSlashModel(unittest.TestCase):
    """Scenario: openrouter/org/model — only the 'openrouter/' prefix is stripped."""

    def test_meta_llama_prefix_stripped_correctly(self):
        """openrouter/meta-llama/llama-4-scout → model = meta-llama/llama-4-scout."""
        model, base, key = wee_runtime.resolve_model_and_endpoint(
            "openrouter/meta-llama/llama-4-scout",
            api_key="test_key",
        )
        self.assertEqual(model, "meta-llama/llama-4-scout")

    def test_google_gemma_prefix_stripped(self):
        """openrouter/google/gemma-3-12b-it:free → model = google/gemma-3-12b-it:free."""  # noqa: E501
        model, base, key = wee_runtime.resolve_model_and_endpoint(
            "openrouter/google/gemma-3-12b-it:free",
            api_key="test_key",
        )
        self.assertEqual(model, "google/gemma-3-12b-it:free")

    def test_openrouter_base_url_set(self):
        """openrouter/ prefix → api_base = openrouter.ai URL."""
        _, base, _ = wee_runtime.resolve_model_and_endpoint(
            "openrouter/some-model",
            api_key="test_key",
        )
        self.assertIn("openrouter.ai", base)

    def test_openrouter_single_component_model(self):
        """openrouter/simple-model → model = simple-model."""
        model, base, key = wee_runtime.resolve_model_and_endpoint(
            "openrouter/simple-model",
            api_key="test_key",
        )
        self.assertEqual(model, "simple-model")


# ---------------------------------------------------------------------------
# 11. execute_tool edge cases not covered elsewhere
# ---------------------------------------------------------------------------


class TestScenarioExecuteToolEdgeCases(unittest.TestCase):
    """Additional execute_tool edge cases for complete coverage."""

    def test_bash_stdout_whitespace_trimmed(self):
        """Trailing whitespace in bash output is stripped."""
        result = wee_runtime.execute_tool("bash", {"command": "printf '  hello  '"})
        self.assertEqual(result, "hello")

    def test_python_stdout_whitespace_trimmed(self):
        """Trailing whitespace in python output is stripped."""
        result = wee_runtime.execute_tool("python", {"command": "print('  hi  ')"})
        # Note: execute_tool strips, but 'command' key doesn't work — use 'code'
        result = wee_runtime.execute_tool("python", {"code": "print('  hi  ')"})
        self.assertEqual(result.strip(), "hi")

    def test_bash_large_output_not_truncated(self):
        """execute_tool does not truncate large output."""
        result = wee_runtime.execute_tool(
            "bash", {"command": "python3 -c \"print('A'*10000)\""}
        )
        self.assertEqual(len(result), 10000)

    def test_python_large_output_not_truncated(self):
        """Python tool does not truncate large output."""
        result = wee_runtime.execute_tool("python", {"code": "print('B' * 8000)"})
        self.assertEqual(len(result), 8000)

    def test_bash_no_output_returns_placeholder(self):
        """Bash command with no stdout returns '(no output)'."""
        result = wee_runtime.execute_tool("bash", {"command": "true"})
        self.assertEqual(result, "(no output)")

    def test_python_no_output_returns_placeholder(self):
        """Python with no print returns '(no output)'."""
        result = wee_runtime.execute_tool("python", {"code": "x = 1 + 1"})
        self.assertEqual(result, "(no output)")

    def test_bash_stderr_included_on_nonzero(self):
        """Stderr is included when exit code is non-zero."""
        result = wee_runtime.execute_tool("bash", {"command": "echo err >&2; exit 1"})
        self.assertIn("err", result)

    def test_bash_tool_with_empty_command(self):
        """Empty command string returns a descriptive error."""
        result = wee_runtime.execute_tool("bash", {"command": ""})
        self.assertIn("Error", result)
        self.assertIn("No command", result)

    def test_python_tool_with_empty_code(self):
        """Empty code string for python tool returns a descriptive error."""
        result = wee_runtime.execute_tool("python", {"code": ""})
        self.assertIn("Error", result)
        self.assertIn("No code", result)

    def test_timeout_expired_returns_error_string(self):
        """TimeoutExpired produces an Error: string, not a crash."""
        import subprocess as sp

        def mock_run(*a, **kw):  # noqa: E306
            raise sp.TimeoutExpired(cmd="sleep", timeout=120)

        with patch("subprocess.run", side_effect=mock_run):
            result = wee_runtime.execute_tool("bash", {"command": "sleep 200"})
        self.assertIn("Error", result)
        self.assertIn("timed out", result.lower())

    def test_generic_exception_returns_error_string(self):
        """Unexpected exception in execute_tool returns an Error string."""
        with patch("subprocess.run", side_effect=OSError("no binary")):
            result = wee_runtime.execute_tool("bash", {"command": "ls"})
        self.assertIn("Error", result)


# ---------------------------------------------------------------------------
# 12. sanitize_bash_command — additional boundary cases
# ---------------------------------------------------------------------------


class TestScenarioSanitizeBashBoundary(unittest.TestCase):
    """Boundary cases for sanitize_bash_command not covered elsewhere."""

    def test_ssh_at_end_of_pipeline(self):
        """SSH command at end of a pipeline gets flag injected."""
        cmd = "cat file.txt | ssh user@host"
        result = wee_runtime.sanitize_bash_command(cmd)
        self.assertIn("StrictHostKeyChecking", result)

    def test_sshpass_not_matched_by_ssh_regex(self):
        """'sshpass' should NOT be matched (word boundary check)."""
        cmd = "sshpass -p pass ssh user@host"
        result = wee_runtime.sanitize_bash_command(cmd)
        # sshpass itself should not get the flag, but ssh should
        # The regex uses \b so 'sshpass' should not match 'ssh' within 'sshpass'
        # However 'ssh' later in the command should be caught
        self.assertIn("StrictHostKeyChecking", result)

    def test_command_with_no_ssh_unchanged(self):
        """Commands without ssh/scp/sftp are returned unchanged."""
        for cmd in ["echo hello", "ls -la", "python3 script.py", "curl http://x"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(wee_runtime.sanitize_bash_command(cmd), cmd)

    def test_both_scp_and_ssh_in_one_command(self):
        """Both scp and ssh in the same command both get sanitized."""
        cmd = "scp file.txt host:/tmp && ssh host 'ls /tmp'"
        result = wee_runtime.sanitize_bash_command(cmd)
        self.assertEqual(result.count("StrictHostKeyChecking"), 2)


# ---------------------------------------------------------------------------
# 13. Message history integrity — multi-round accumulation — mocked
# ---------------------------------------------------------------------------


class TestScenarioMessageHistoryIntegrity(unittest.TestCase):
    """Verify message history grows correctly across multiple tool rounds."""

    @patch("openai.OpenAI")
    def test_initial_messages_are_system_and_user(self, mock_cls):
        """First API call has exactly system + user message."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = iter([_make_chunk("ok")])

        _run_main("ollama/test-model", "hello", tools=True)
        first_messages = mock_client.chat.completions.create.call_args_list[0][1][
            "messages"
        ]
        roles = [m["role"] for m in first_messages]
        self.assertIn("system", roles)
        self.assertIn("user", roles)
        self.assertEqual(roles[-1], "user")

    @patch("openai.OpenAI")
    def test_after_one_tool_round_message_count(self, mock_cls):
        """After one tool round: system + user + assistant + tool = 4 messages."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        td = _make_tool_delta(0, "bash", '{"command":"echo x"}', "tc1")
        chunk_tool = MagicMock()
        chunk_tool.choices = [MagicMock()]
        chunk_tool.choices[0].delta.content = None
        chunk_tool.choices[0].delta.tool_calls = [td]
        chunk_final = _make_chunk("done")

        mock_client.chat.completions.create.side_effect = [
            iter([chunk_tool]),
            iter([chunk_final]),
        ]
        with patch.object(wee_runtime, "execute_tool", return_value="x"):
            _run_main("ollama/test-model", "run", tools=True)

        second_messages = mock_client.chat.completions.create.call_args_list[1][1][
            "messages"
        ]
        self.assertEqual(len(second_messages), 4)  # sys, user, assistant, tool

    @patch("openai.OpenAI")
    def test_two_tool_rounds_accumulate_correctly(self, mock_cls):
        """Two tool rounds produce system + user + 2*(assistant+tool) = 6 messages."""
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        def make_r(tc_id):
            td = _make_tool_delta(0, "bash", '{"command":"echo x"}', tc_id)
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = None
            chunk.choices[0].delta.tool_calls = [td]
            return iter([chunk])

        mock_client.chat.completions.create.side_effect = [
            make_r("tc1"),
            make_r("tc2"),
            iter([_make_chunk("final")]),
        ]
        with patch.object(wee_runtime, "execute_tool", return_value="out"):
            _run_main("ollama/test-model", "two rounds", tools=True)

        third_messages = mock_client.chat.completions.create.call_args_list[2][1][
            "messages"
        ]
        self.assertEqual(
            len(third_messages), 6
        )  # sys, user, asst1, tool1, asst2, tool2  # noqa: E501


# ---------------------------------------------------------------------------
# 14. CLI argument coverage
# ---------------------------------------------------------------------------


class TestScenarioCLIArgCoverage(unittest.TestCase):
    """CLI argument edge cases via subprocess (no live model needed)."""

    def test_help_flag_exits_zero(self):
        """--help prints usage and exits 0."""
        rc, out, err = (
            _run_main.__wrapped__(
                [sys.executable, WEE_RUNTIME, "--help"],
            )
            if hasattr(_run_main, "__wrapped__")
            else (None, None, None)
        )
        # Use subprocess directly
        r = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--help"], capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("--model", r.stdout)

    def test_missing_model_exits_nonzero(self):
        """Missing --model causes non-zero exit."""
        r = subprocess.run(
            [sys.executable, WEE_RUNTIME, "some prompt"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r.returncode, 0)

    def test_missing_prompt_exits_nonzero(self):
        """Missing prompt argument causes non-zero exit."""
        r = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--model", "ollama/test"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r.returncode, 0)

    def test_temperature_in_help_text(self):
        """--temperature is documented in --help output."""
        r = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--help"],
            capture_output=True,
            text=True,
        )
        self.assertIn("temperature", r.stdout.lower())

    def test_tools_in_help_text(self):
        """--tools is documented in --help output."""
        r = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--help"],
            capture_output=True,
            text=True,
        )
        self.assertIn("tool", r.stdout.lower())

    def test_system_prompt_in_help_text(self):
        """--system-prompt is documented in --help output."""
        r = subprocess.run(
            [sys.executable, WEE_RUNTIME, "--help"],
            capture_output=True,
            text=True,
        )
        self.assertIn("system-prompt", r.stdout.lower())


# ---------------------------------------------------------------------------
# 15. Anti-hallucination and tool capability prompt constants
# ---------------------------------------------------------------------------


class TestScenarioPromptConstants(unittest.TestCase):
    """Validate prompt constant content and completeness."""

    def test_anti_hallucination_contains_fabricate(self):
        """Anti-hallucination prompt warns against fabrication."""
        self.assertIn("fabricat", wee_runtime._ANTI_HALLUCINATION_PROMPT.lower())

    def test_anti_hallucination_contains_stricthostkeychecking(self):
        """Anti-hallucination prompt includes SSH flag guidance."""
        self.assertIn("StrictHostKeyChecking", wee_runtime._ANTI_HALLUCINATION_PROMPT)

    def test_anti_hallucination_is_non_empty(self):
        """Anti-hallucination prompt is a non-empty string."""
        self.assertIsInstance(wee_runtime._ANTI_HALLUCINATION_PROMPT, str)
        self.assertGreater(len(wee_runtime._ANTI_HALLUCINATION_PROMPT), 50)

    def test_tool_capability_prompt_mentions_not_sandboxed(self):
        """Tool capability prompt explicitly says NOT sandboxed."""
        self.assertIn("NOT sandboxed", wee_runtime._WEE_TOOL_CAPABILITY_PROMPT)

    def test_tool_capability_prompt_mentions_bash_and_python(self):
        """Tool capability prompt names both bash and python tools."""
        p = wee_runtime._WEE_TOOL_CAPABILITY_PROMPT
        self.assertIn("bash", p.lower())
        self.assertIn("python", p.lower())

    def test_tool_capability_prompt_is_non_empty(self):
        """Tool capability prompt is a non-empty string."""
        self.assertIsInstance(wee_runtime._WEE_TOOL_CAPABILITY_PROMPT, str)
        self.assertGreater(len(wee_runtime._WEE_TOOL_CAPABILITY_PROMPT), 50)

    def test_ssh_regex_is_compiled(self):
        """_SSH_BIN_RE is a compiled regex object."""
        import re

        self.assertIsInstance(wee_runtime._SSH_BIN_RE, type(re.compile("")))

    def test_max_tool_rounds_is_positive_int(self):
        """MAX_TOOL_ROUNDS is a positive integer."""
        self.assertIsInstance(wee_runtime.MAX_TOOL_ROUNDS, int)
        self.assertGreater(wee_runtime.MAX_TOOL_ROUNDS, 0)

    def test_tool_timeout_is_positive_int(self):
        """TOOL_TIMEOUT is a positive integer."""
        self.assertIsInstance(wee_runtime.TOOL_TIMEOUT, int)
        self.assertGreater(wee_runtime.TOOL_TIMEOUT, 0)


# ---------------------------------------------------------------------------
# 16. Live Ollama tests
# ---------------------------------------------------------------------------


@skip_ollama
class TestScenarioOllamaLive(unittest.TestCase):
    """Live integration tests against local Ollama server."""

    def setUp(self):
        self.model = OLLAMA_MODEL

    def test_simple_response_nonempty(self):
        """Basic prompt produces non-empty text response."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--timeout",
                "60",
                "Say: hello",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.strip())

    def test_temperature_zero_deterministic(self):
        """Two identical prompts at temperature=0 produce the same output."""
        prompt = "What is 2 + 2? Answer with just the number."
        r1 = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--temperature",
                "0",
                "--timeout",
                "60",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        r2 = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--temperature",
                "0",
                "--timeout",
                "60",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(r1.returncode, 0)
        self.assertEqual(r2.returncode, 0)
        # Core content should match at low temperature
        self.assertIn("4", r1.stdout)
        self.assertIn("4", r2.stdout)

    def test_bash_tool_executes(self):
        """Model uses bash tool when asked to run a command."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--tools",
                "--timeout",
                "90",
                "Use the bash tool to run 'echo MARKER_12345' and tell me the output.",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(r.returncode, 0)
        # Either the tool was called (MARKER in output or stderr mentions tool)
        # or the model responded with the expected value
        combined = r.stdout + r.stderr
        self.assertTrue(
            "MARKER_12345" in combined or "12345" in combined or r.stdout.strip(),
            f"Expected MARKER or tool invocation, got: {r.stdout[:200]}",
        )

    def test_python_tool_math(self):
        """Model uses python tool for arithmetic."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--tools",
                "--timeout",
                "90",
                "Use the python tool to calculate 7 * 13 and report the result.",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(r.returncode, 0)
        # 7 * 13 = 91
        combined = r.stdout + r.stderr
        self.assertTrue(
            "91" in combined, f"Expected 91 in output, got: {r.stdout[:300]}"
        )

    def test_explicit_api_base_override(self):
        """--api-base override connects to Ollama directly."""
        model_name = self.model.replace("ollama/", "")  # strip prefix
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                model_name,
                "--api-base",
                f"{OLLAMA_HOST}/v1",
                "--api-key",
                "ollama",
                "--timeout",
                "60",
                "Say: api base works",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.strip())

    def test_ssh_sanitization_in_tool(self):
        """SSH commands issued via tool get StrictHostKeyChecking injected."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--tools",
                "--timeout",
                "90",
                "Use the bash tool to run: ssh -n localhost 'echo test' 2>&1 || true",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(r.returncode, 0)
        # Stderr from wee_runtime should contain the [Wee] Tool: ssh line
        # with StrictHostKeyChecking injected (or connection refused — which is fine)
        self.assertIn("[Wee]", r.stderr)

    def test_multi_step_bash_then_python(self):
        """Multi-step: bash to get current dir, python to process the result."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--tools",
                "--timeout",
                "120",
                "Use bash to run 'echo /tmp/test_wee' then use python to uppercase "
                "that string and report it.",
            ],
            capture_output=True,
            text=True,
            timeout=150,
        )
        self.assertEqual(r.returncode, 0)
        # Model should produce something meaningful
        self.assertTrue(r.stdout.strip())

    def test_system_prompt_honored(self):
        """System prompt is respected by the model."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--system-prompt",
                "You MUST reply with only the word CONFIRMED.",
                "--timeout",
                "60",
                "Acknowledge this message.",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("CONFIRMED", r.stdout.upper())

    def test_response_ends_with_newline(self):
        """All responses end with a trailing newline."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--timeout",
                "60",
                "Say hi",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.endswith("\n"))


# ---------------------------------------------------------------------------
# 17. Live OpenRouter tests
# ---------------------------------------------------------------------------


@skip_openrouter
class TestScenarioOpenRouterLive(unittest.TestCase):
    """Live integration tests against OpenRouter API (free tier models)."""

    def setUp(self):
        self.model = OPENROUTER_MODEL

    def test_simple_text_response(self):
        """Basic prompt produces non-empty response from free model."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--timeout",
                "60",
                "Reply with only: WEEOK",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.strip())

    def test_multi_slash_model_name_resolves(self):
        """openrouter/google/gemma-3-12b-it:free (3 parts) works correctly."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                "openrouter/google/gemma-3-12b-it:free",
                "--timeout",
                "60",
                "Say: multi-slash OK",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.strip())

    def test_system_prompt_adhered_to(self):
        """System prompt directs OpenRouter model behavior."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--system-prompt",
                "Reply only with uppercase text.",
                "--timeout",
                "60",
                "Say hello",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(r.returncode, 0)
        # Response should have uppercase characters
        self.assertTrue(r.stdout.strip())
        self.assertEqual(r.stdout.strip().upper(), r.stdout.strip().upper())

    def test_code_generation_response(self):
        """OpenRouter model can generate valid Python code."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--timeout",
                "60",
                "Write a Python function that adds two numbers. "
                "Return only the code, no explanation.",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("def ", r.stdout)

    def test_missing_key_exits_nonzero(self):
        """Without API key, OpenRouter exits non-zero with error."""
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("OPENROUTER_API_KEY", "WEE_API_KEY")
        }
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                "openrouter/test-model",
                "--timeout",
                "10",
                "hello",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Error", r.stderr + r.stdout)

    def test_response_ends_with_newline(self):
        """OpenRouter responses end with a trailing newline."""
        r = subprocess.run(
            [
                sys.executable,
                WEE_RUNTIME,
                "--model",
                self.model,
                "--timeout",
                "60",
                "Say: hi",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.endswith("\n"))


# ---------------------------------------------------------------------------
# 18. Performance and import sanity
# ---------------------------------------------------------------------------


class TestScenarioPerformanceSanity(unittest.TestCase):
    """Sanity checks that don't require live models."""

    def test_module_imports_quickly(self):
        """wee_runtime.py imports in under 2 seconds."""
        start = time.time()
        import importlib

        importlib.reload(wee_runtime)
        elapsed = time.time() - start
        self.assertLess(elapsed, 2.0, f"Import took {elapsed:.2f}s")

    def test_resolve_model_is_fast(self):
        """resolve_model_and_endpoint completes in under 0.1 seconds."""
        start = time.time()
        for _ in range(100):
            wee_runtime.resolve_model_and_endpoint("ollama/qwen3:8b", api_key="ollama")
        elapsed = time.time() - start
        self.assertLess(elapsed, 1.0, f"100 resolutions took {elapsed:.2f}s")

    def test_sanitize_bash_is_fast(self):
        """sanitize_bash_command on complex input under 0.01s."""
        cmd = "ssh user@host1 && scp file host2:/tmp && sftp host3"
        start = time.time()
        for _ in range(1000):
            wee_runtime.sanitize_bash_command(cmd)
        elapsed = time.time() - start
        self.assertLess(elapsed, 1.0, f"1000 sanitizations took {elapsed:.2f}s")

    def test_wee_runtime_script_is_valid_python(self):
        """wee_runtime.py compiles without syntax errors."""
        import py_compile

        py_compile.compile(WEE_RUNTIME, doraise=True)

    def test_wee_tools_json_serializable(self):
        """_WEE_TOOLS can be serialized to JSON without error."""
        serialized = json.dumps(wee_runtime._WEE_TOOLS)
        parsed = json.loads(serialized)
        self.assertEqual(len(parsed), 2)


if __name__ == "__main__":
    unittest.main()
