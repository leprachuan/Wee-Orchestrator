"""Regression tests for Issue #112: wee runtime returns empty response when
LLM generates no text after tool execution.

Root cause: After tool execution in the wee agentic loop, if the LLM's
synthesis round returns empty text with no tool calls, `run_wee_native()`
returned `response: ''`. The fix adds a fallback that surfaces the last
tool result when the model produces empty synthesis.

Scenarios tested:
- Empty synthesis after single tool call -> fallback to tool result
- Empty synthesis after multiple tool calls -> fallback to last result
- Empty synthesis with empty tool output -> "(Tool executed but produced no output)"
- Normal non-empty synthesis -> no fallback triggered
- Tool result truncation at 4000 chars
- Stream buffer receives fallback content
- Whitespace-only synthesis treated as empty
- Reproduces T3, T7, T9 from issue report
"""

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_chunk(content=None, tool_calls=None, finish_reason=None):
    """Create a mock streaming chunk matching OpenAI format."""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _make_tool_call_delta(index, tc_id=None, name=None, arguments=None):
    """Create a mock tool_call delta for streaming."""
    func = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=tc_id, function=func)


class FakeStreamBuffer:
    """Capture stream buffer pushes for verification."""

    def __init__(self):
        self.events = []

    def push(self, event_type, data):
        self.events.append((event_type, data))


@pytest.fixture
def session_mgr():
    """Create a SessionManager instance for testing with minimal setup."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    mgr._env_claude_models = {}
    mgr._env_gemini_models = {}
    mgr._env_codex_models = {}
    mgr._env_devin_models = {}
    mgr._env_cursor_models = {}
    mgr._env_wee_models = None
    mgr.command_timeout = 300
    mgr.AGENTS = {
        "orchestrator": {"path": "/opt/fosterbot-home"},
        "wee-dev": {"path": "/opt/wee-dev"},
    }
    mgr.session_map_file = MagicMock()
    mgr.session_map_file.exists.return_value = False
    mgr._session_map_lock = MagicMock()
    mgr._session_map_lock.__enter__ = MagicMock(return_value=None)
    mgr._session_map_lock.__exit__ = MagicMock(return_value=False)
    mgr._stream_buffers = {}
    return mgr


def _run_wee_with_mock(
    session_mgr,
    prompt,
    tool_result,
    round_chunks_list,
    session_id="test-112",
    stream_buffer=None,
):
    """Helper to run run_wee_native with mocked OpenAI client."""
    call_count = {"n": 0}

    def mock_create(**kwargs):
        call_count["n"] += 1
        idx = min(call_count["n"] - 1, len(round_chunks_list) - 1)
        return iter(round_chunks_list[idx])

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = mock_create

    n8n_sid = "sess-" + session_id
    if stream_buffer:
        session_mgr._stream_buffers[n8n_sid] = stream_buffer

    execute_side = tool_result if callable(tool_result) else None
    execute_return = None if callable(tool_result) else tool_result

    with (
        patch.object(
            session_mgr,
            "get_or_create_session_data",
            return_value={
                "channel": "webui",
            },
        ),
        patch.object(
            session_mgr,
            "_wee_load_messages",
            return_value=[
                {"role": "system", "content": "You are a helpful assistant."},
            ],
        ),
        patch.object(session_mgr, "_wee_save_messages"),
        patch.object(session_mgr, "build_agent_context_prompt", return_value="ctx"),
        patch.object(
            session_mgr, "_wee_augment_system_prompt_with_tools", return_value="ctx"
        ),
        patch.object(session_mgr, "_wee_anti_hallucination_prompt", return_value=""),
        patch.object(
            session_mgr,
            "_wee_execute_tool",
            side_effect=execute_side,
            return_value=execute_return,
        ),
        patch("openai.OpenAI", return_value=mock_client),
    ):

        result = session_mgr.run_wee_native(
            prompt=prompt,
            model="ollama/qwen3:8b",
            agent="orchestrator",
            session_id=session_id,
            resume=False,
            n8n_session_id=n8n_sid,
        )

    return result


def _tool_call_chunks(tc_id="tc_1", cmd="ls"):
    """Chunks for a single bash tool call round."""
    return [
        _make_chunk(
            tool_calls=[
                _make_tool_call_delta(0, tc_id=tc_id, name="bash", arguments=None),
            ]
        ),
        _make_chunk(
            tool_calls=[
                _make_tool_call_delta(0, arguments=json.dumps({"command": cmd})),
            ]
        ),
        _make_chunk(finish_reason="tool_calls"),
    ]


def _empty_synthesis_chunks():
    """Chunks for empty synthesis round (the bug trigger)."""
    return [_make_chunk(content=""), _make_chunk(finish_reason="stop")]


def _no_content_synthesis_chunks():
    """Chunks with no content tokens at all."""
    return [_make_chunk(finish_reason="stop")]


# ---- Empty synthesis after single tool call ----


class TestEmptySynthesisSingleTool:

    def test_issue_112_empty_synthesis_returns_tool_result(self, session_mgr):
        """Core regression: empty synthesis must return tool result."""
        tool_result = "drwxr-xr-x 2 root root 4096 Apr 11 12:00 memories"
        result = _run_wee_with_mock(
            session_mgr,
            prompt="List the memories directory",
            tool_result=tool_result,
            round_chunks_list=[
                _tool_call_chunks(cmd="ls -la /opt/memories"),
                _empty_synthesis_chunks(),
            ],
            session_id="112-a",
        )
        assert result.strip() != "", "Output must not be empty"
        assert tool_result in result
        assert "Tool execution result" in result

    def test_issue_112_no_content_tokens_returns_tool_result(self, session_mgr):
        """Model returns zero content tokens (None throughout)."""
        tool_result = "MEMORY.md  daily/"
        result = _run_wee_with_mock(
            session_mgr,
            prompt="List files",
            tool_result=tool_result,
            round_chunks_list=[
                _tool_call_chunks(cmd="ls"),
                _no_content_synthesis_chunks(),
            ],
            session_id="112-b",
        )
        assert result.strip() != ""
        assert tool_result in result


# ---- Empty synthesis after multiple tool calls ----


class TestEmptySynthesisMultipleTools:

    def test_issue_112_multi_tool_uses_last_result(self, session_mgr):
        """Two tool calls, empty synthesis -> shows last result."""
        results = [
            "File exists",
            "# Lipkey Family\n- Foster\n- Leslie\n- Parker\n- Oliver",
        ]
        call_idx = {"n": 0}

        def tool_side_effect(name, args, agent):
            r = results[call_idx["n"]]
            call_idx["n"] += 1
            return r

        result = _run_wee_with_mock(
            session_mgr,
            prompt="Read the MEMORY.md file",
            tool_result=tool_side_effect,
            round_chunks_list=[
                _tool_call_chunks(tc_id="tc_1", cmd="test -f MEMORY.md"),
                _tool_call_chunks(tc_id="tc_2", cmd="cat MEMORY.md"),
                _no_content_synthesis_chunks(),
            ],
            session_id="112-c",
        )
        assert result.strip() != ""
        assert "Lipkey Family" in result


# ---- Empty tool output + empty synthesis ----


class TestEmptyToolEmptySynthesis:

    def test_issue_112_empty_tool_empty_synthesis(self, session_mgr):
        """Tool returns empty, model returns empty -> descriptive output."""
        result = _run_wee_with_mock(
            session_mgr,
            prompt="Run echo -n",
            tool_result="",
            round_chunks_list=[
                _tool_call_chunks(cmd="echo -n"),
                _no_content_synthesis_chunks(),
            ],
            session_id="112-d",
        )
        assert result.strip() != "", "Must not return empty string"
        assert "no output" in result.lower() or "Tool" in result


# ---- Normal synthesis (no fallback) ----


class TestNormalSynthesisNoFallback:

    def test_issue_112_normal_synthesis_unchanged(self, session_mgr):
        """Non-empty synthesis passes through without fallback."""
        synthesis_chunks = [
            _make_chunk(content="The following "),
            _make_chunk(content="dev services are active."),
            _make_chunk(finish_reason="stop"),
        ]
        result = _run_wee_with_mock(
            session_mgr,
            prompt="Check services",
            tool_result="agent-manager-api-dev running",
            round_chunks_list=[
                _tool_call_chunks(cmd="systemctl list-units"),
                synthesis_chunks,
            ],
            session_id="112-e1",
        )
        assert result == "The following dev services are active."
        assert "Tool execution result" not in result

    def test_issue_112_no_tool_calls_no_fallback(self, session_mgr):
        """Prompt with no tool calls -> no fallback needed."""
        chunks = [
            _make_chunk(content="Hello! How can I help you?"),
            _make_chunk(finish_reason="stop"),
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter(chunks)

        with (
            patch.object(
                session_mgr,
                "get_or_create_session_data",
                return_value={
                    "channel": "webui",
                },
            ),
            patch.object(
                session_mgr,
                "_wee_load_messages",
                return_value=[
                    {"role": "system", "content": "ctx"},
                ],
            ),
            patch.object(session_mgr, "_wee_save_messages"),
            patch.object(session_mgr, "build_agent_context_prompt", return_value="ctx"),
            patch.object(
                session_mgr, "_wee_augment_system_prompt_with_tools", return_value="ctx"
            ),
            patch.object(
                session_mgr, "_wee_anti_hallucination_prompt", return_value=""
            ),
            patch("openai.OpenAI", return_value=mock_client),
        ):

            result = session_mgr.run_wee_native(
                prompt="Hello",
                model="ollama/qwen3:8b",
                agent="orchestrator",
                session_id="test-112-f",
                resume=False,
                n8n_session_id="sess-112-f",
            )

        assert result == "Hello! How can I help you?"
        assert "Tool execution result" not in result


# ---- Tool result truncation ----


class TestToolResultTruncation:

    def test_issue_112_large_result_truncated(self, session_mgr):
        """Tool result > 4000 chars truncated in fallback."""
        large_result = "X" * 5000
        result = _run_wee_with_mock(
            session_mgr,
            prompt="Cat big file",
            tool_result=large_result,
            round_chunks_list=[
                _tool_call_chunks(cmd="cat big_file"),
                _no_content_synthesis_chunks(),
            ],
            session_id="112-g",
        )
        assert "Tool execution result" in result
        assert len(result) <= 4100


# ---- Stream buffer receives fallback ----


class TestStreamBufferFallback:

    def test_issue_112_stream_buffer_gets_fallback(self, session_mgr):
        """SSE stream receives fallback text as chunk event."""
        fake_buffer = FakeStreamBuffer()
        tool_result = "service is running"

        result = _run_wee_with_mock(  # noqa: F841
            session_mgr,
            prompt="Check service status",
            tool_result=tool_result,
            round_chunks_list=[
                _tool_call_chunks(cmd="systemctl status"),
                _no_content_synthesis_chunks(),
            ],
            session_id="112-h",
            stream_buffer=fake_buffer,
        )

        chunk_events = [e for e in fake_buffer.events if e[0] == "chunk"]
        assert len(chunk_events) > 0, "Buffer should receive fallback chunk"
        fallback_text = chunk_events[-1][1]["text"]
        assert tool_result in fallback_text

        done_events = [e for e in fake_buffer.events if e[0] == "done"]
        assert len(done_events) == 1


# ---- Whitespace-only synthesis ----


class TestWhitespaceOnlySynthesis:

    def test_issue_112_whitespace_triggers_fallback(self, session_mgr):
        """Whitespace-only synthesis triggers fallback."""
        ws_chunks = [
            _make_chunk(content="\n"),
            _make_chunk(content="  "),
            _make_chunk(content="\n"),
            _make_chunk(finish_reason="stop"),
        ]
        tool_result = "total 42"
        result = _run_wee_with_mock(
            session_mgr,
            prompt="Disk usage",
            tool_result=tool_result,
            round_chunks_list=[
                _tool_call_chunks(cmd="du -sh"),
                ws_chunks,
            ],
            session_id="112-i",
        )
        assert result.strip() != ""
        assert tool_result in result


# ---- Reproduce exact issue scenarios (T3, T7, T9) ----


class TestIssueScenarios:

    def test_issue_112_t3_filesystem_read(self, session_mgr):
        """T3: Read MEMORY.md - model calls tool, empty synthesis."""
        content = (
            "# Lipkey Family Memory\n- Foster is the father\n- Leslie is the mother"
        )
        result = _run_wee_with_mock(
            session_mgr,
            prompt="Read /opt/memories/MEMORY.md and tell me about the Lipkey family.",
            tool_result=content,
            round_chunks_list=[
                _tool_call_chunks(cmd="cat /opt/memories/MEMORY.md"),
                _empty_synthesis_chunks(),
            ],
            session_id="112-t3",
        )
        assert result.strip() != "", "T3: Must not return empty response"
        assert "Lipkey" in result

    def test_issue_112_t7_passphrase_store(self, session_mgr):
        """T7: Secret passphrase store - tool succeeds with empty output."""
        result = _run_wee_with_mock(
            session_mgr,
            prompt="My secret passphrase is STARFISH-7. Remember it.",
            tool_result="",
            round_chunks_list=[
                _tool_call_chunks(cmd="/secret set passphrase STARFISH-7"),
                _no_content_synthesis_chunks(),
            ],
            session_id="112-t7",
        )
        assert result.strip() != "", "T7: Must not return empty response"

    def test_issue_112_t9_ssh_systemctl(self, session_mgr):
        """T9: SSH + systemctl - tool returns service list, empty synthesis."""
        output = (
            "agent-manager-api-dev.service   loaded active running\n"
            "task-scheduler-executor-dev.service loaded active running"
        )
        result = _run_wee_with_mock(
            session_mgr,
            prompt="SSH to 192.168.1.100 and list active dev services",
            tool_result=output,
            round_chunks_list=[
                _tool_call_chunks(
                    cmd="ssh root@192.168.1.100 systemctl list-units | grep dev"
                ),
                _empty_synthesis_chunks(),
            ],
            session_id="112-t9",
        )
        assert result.strip() != "", "T9: Must not return empty response"
        assert "agent-manager-api-dev" in result
