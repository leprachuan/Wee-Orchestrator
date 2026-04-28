"""Regression tests for Issue #273: Context Window Management and Compaction."""

import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

from wee_cli import TokenTracker  # noqa: E402
from wee_runtime import (  # noqa: E402
    _COMPACT_WARN_PCT,
    COMPACT_TRIGGER_FRACTION,
    MODEL_CONTEXT_WINDOWS,
    compact_messages,
    count_message_tokens,
    estimate_tokens,
    execute_tool,
    get_context_window,
)


class TestModelContextWindows(unittest.TestCase):
    def test_openai_gpt4o(self):
        assert get_context_window("gpt-4o") == 128000

    def test_openai_gpt4_turbo(self):
        assert get_context_window("gpt-4-turbo") == 128000

    def test_openai_gpt4_base(self):
        assert get_context_window("gpt-4") == 8192

    def test_openai_gpt41(self):
        assert get_context_window("gpt-4.1") == 1_047_576

    def test_openai_gpt41_mini(self):
        assert get_context_window("gpt-4.1-mini") == 1_047_576

    def test_openai_gpt41_nano(self):
        assert get_context_window("gpt-4.1-nano") == 1_047_576

    def test_openai_gpt5(self):
        assert get_context_window("gpt-5") == 1_047_576

    def test_openai_gpt4o_mini_explicit(self):
        assert get_context_window("gpt-4o-mini") == 128_000

    def test_gpt41_does_not_fall_through_to_gpt4(self):
        # gpt-4.1 must NOT resolve to gpt-4's 8192 context window
        assert get_context_window("gpt-4.1") != 8192

    def test_qwen3(self):
        assert get_context_window("qwen3:8b") == 32768

    def test_gemma4(self):
        assert get_context_window("ollama/gemma4:e4b") == 131072

    def test_gemma2(self):
        assert get_context_window("gemma2:9b") == 8192

    def test_llama3(self):
        assert get_context_window("llama-3.1-8b") == 131072

    def test_unknown_model_default(self):
        assert get_context_window("unknown-model-xyz") == 4096

    def test_longest_match_wins(self):
        # gpt-4o (128000) should beat gpt-4 (8192) for "gpt-4o-mini"
        result = get_context_window("gpt-4o-mini")
        assert result == 128000, f"Expected 128000, got {result}"

    def test_model_context_windows_dict_populated(self):
        assert len(MODEL_CONTEXT_WINDOWS) >= 10


class TestEstimateTokens(unittest.TestCase):
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_nonempty_string(self):
        result = estimate_tokens("hello world this is a test")
        assert result > 0

    def test_heuristic_roughly_chars_over_4(self):
        text = "a" * 100
        result = estimate_tokens(text)
        assert result == 25  # 100 // 4

    def test_minimum_one(self):
        result = estimate_tokens("a")
        assert result >= 1


class TestCountMessageTokens(unittest.TestCase):
    def test_basic_messages(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        total = count_message_tokens(msgs)
        assert total > 0

    def test_empty_messages(self):
        assert count_message_tokens([]) == 0

    def test_none_content(self):
        msgs = [{"role": "assistant", "content": None}]
        result = count_message_tokens(msgs)
        assert result >= 4  # at least the per-message overhead

    def test_tool_call_messages_counted(self):
        # tool_calls in assistant message should be counted
        msgs = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "echo hello world"}',
                        }
                    }
                ],
            }
        ]
        result = count_message_tokens(msgs)
        # Should count function name + arguments, not just overhead
        base = count_message_tokens([{"role": "assistant", "content": None}])
        assert result > base

    def test_tool_result_content_counted(self):
        # Anthropic-style tool_result parts in content list
        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "name": "bash",
                        "content": "hello world output from the tool",
                    }
                ],
            }
        ]
        result = count_message_tokens(msgs)
        plain = count_message_tokens(
            [{"role": "user", "content": "hello world output from the tool"}]
        )
        # Tool result should contribute roughly same tokens as plain content
        assert result >= plain - 4  # within per-message overhead margin

    def test_openai_tool_role_message_counted(self):
        # OpenAI-format tool-result message: role="tool" with string content
        # and a tool_call_id field.
        tool_output = "The command output was: hello world from the shell"
        msgs = [
            {
                "role": "tool",
                "content": tool_output,
                "tool_call_id": "call_abc123",
            }
        ]
        result = count_message_tokens(msgs)
        # Must count at least the content text
        plain = count_message_tokens([{"role": "user", "content": tool_output}])
        # role="tool" should be counted (content + tool_call_id overhead)
        assert result >= plain - 4  # within one per-message overhead margin
        # Zero-content tool message still has overhead
        empty_tool = count_message_tokens([{"role": "tool", "content": ""}])
        assert empty_tool >= 4  # at least the per-message overhead

    def test_openai_tool_role_distinct_from_user_role(self):
        # Verifies role="tool" messages are not silently dropped (i.e. the
        # token count for role="tool" is the same order of magnitude as for
        # an equivalent role="user" message).
        msg_tool = [{"role": "tool", "content": "result text", "tool_call_id": "c1"}]
        msg_user = [{"role": "user", "content": "result text"}]
        # Both should produce a non-zero token count
        assert count_message_tokens(msg_tool) > 0
        assert count_message_tokens(msg_user) > 0


class TestTokenTracker(unittest.TestCase):
    def test_init_with_context_window(self):
        tracker = TokenTracker(context_window=128000)
        assert tracker.context_window == 128000
        assert tracker.session_total == 0

    def test_init_without_context_window(self):
        tracker = TokenTracker()
        assert tracker.context_window == 0
        assert tracker.percent_used() == 0.0

    def test_percent_used_calculation(self):
        # percent_used() uses last_prompt_tokens (actual current context size),
        # NOT session_total (which grows quadratically across turns).
        tracker = TokenTracker(context_window=10000)
        usage = MagicMock()
        usage.prompt_tokens = 1000
        usage.completion_tokens = 500
        usage.total_tokens = 1500
        tracker.update(usage)
        assert tracker.session_total == 1500  # cumulative still tracked
        assert tracker.last_prompt_tokens == 1000
        assert abs(tracker.percent_used() - 10.0) < 0.01  # 1000/10000 * 100

    def test_percent_used_does_not_grow_quadratically(self):
        # SEMANTIC test (not formula math): each turn re-sends the full message
        # history to the API, so session_total grows as O(turns^2) while the
        # actual context window occupancy stays constant.  percent_used() MUST
        # use last_prompt_tokens (the prompt size of the most recent API call),
        # NOT session_total (the cumulative sum across all turns).
        tracker = TokenTracker(context_window=10000)
        for i in range(20):
            usage = MagicMock()
            # Each turn the prompt is still ~500 tokens (history fits in one turn).
            usage.prompt_tokens = 500
            usage.completion_tokens = 100
            usage.total_tokens = 600
            tracker.update(usage)
        # session_total = 20 * 600 = 12000 — would give 120% under old logic
        # (using session_total / context_window).
        assert tracker.session_total == 12000
        # percent_used() must reflect the CURRENT context size (last turn only)
        assert tracker.last_prompt_tokens == 500
        # 5% = 500 / 10000 * 100 — NOT 120% from cumulative session_total
        assert abs(tracker.percent_used() - 5.0) < 0.01
        # The key invariant: percent_used() < 100% even though session_total
        # is 120% of context_window.
        assert tracker.percent_used() < 100.0
        assert tracker.session_total > tracker.context_window

    def test_percent_used_capped_at_100(self):
        tracker = TokenTracker(context_window=100)
        usage = MagicMock()
        usage.prompt_tokens = 9999  # prompt_tokens drives percent_used
        usage.completion_tokens = 0
        usage.total_tokens = 9999
        tracker.update(usage)
        assert tracker.percent_used() == 100.0

    def test_summary_includes_context_window(self):
        tracker = TokenTracker(context_window=50000)
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150
        tracker.update(usage)
        summary = tracker.summary()
        assert "Context window" in summary
        assert "50,000" in summary

    def test_summary_without_context_window(self):
        tracker = TokenTracker()
        summary = tracker.summary()
        assert "Context window" not in summary

    def test_session_total_fallback_when_total_tokens_zero(self):
        tracker = TokenTracker(context_window=10000)
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 0
        tracker.update(usage)
        assert tracker.session_total == 150  # falls back to pt+ct


class TestExecuteToolPermission(unittest.TestCase):
    def test_restricted_blocks_bash(self):
        result = execute_tool("bash", {"command": "echo hi"}, permission="restricted")
        assert "restricted" in result.lower()

    def test_restricted_blocks_python(self):
        result = execute_tool("python", {"code": "print(1)"}, permission="restricted")
        assert "restricted" in result.lower()

    def test_auto_executes_bash(self):
        result = execute_tool("bash", {"command": "echo hello273"}, permission="auto")
        assert "hello273" in result

    def test_default_permission_is_auto(self):
        result = execute_tool("bash", {"command": "echo defaultperm"})
        assert "defaultperm" in result


class TestCompactMessages(unittest.TestCase):
    def _make_client(self, summary_text="Summarized context."):
        client = MagicMock()
        response = MagicMock()
        response.choices[0].message.content = summary_text
        client.chat.completions.create.return_value = response
        return client

    def test_compaction_reduces_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
        ]
        for i in range(20):
            messages.append({"role": "user", "content": f"Question {i}"})
            messages.append({"role": "assistant", "content": f"Answer {i}"})

        client = self._make_client("Prior conversation summary.")
        compacted, summary = compact_messages(
            messages, target_tokens=500, model="qwen3:8b", client=client
        )
        assert len(compacted) < len(messages)
        assert summary == "Prior conversation summary."

    def test_short_history_not_compacted(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        client = self._make_client()
        compacted, summary = compact_messages(
            messages, target_tokens=5000, model="qwen3:8b", client=client
        )
        assert compacted == messages
        assert summary == ""

    def test_compact_warns_when_result_exceeds_target(self):
        import warnings

        # Build a history with 20 very large messages so even keep_recent=6
        # will exceed a tiny target_tokens.
        messages = [{"role": "system", "content": "You are helpful."}]
        for i in range(20):
            messages.append({"role": "user", "content": "x" * 500})
            messages.append({"role": "assistant", "content": "x" * 500})

        client = self._make_client("Summary " + "x" * 200)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            compact_messages(
                messages, target_tokens=10, model="qwen3:8b", client=client
            )
        # Should have emitted at least one warning about exceeding target
        assert any("exceeds" in str(warning.message) for warning in w)

    def test_system_prompt_preserved(self):
        sys_content = "You are a specialized assistant."
        messages = [{"role": "system", "content": sys_content}]
        for i in range(20):
            messages.append({"role": "user", "content": f"Q{i}"})
            messages.append({"role": "assistant", "content": f"A{i}"})

        client = self._make_client("Summary here.")
        compacted, _ = compact_messages(
            messages, target_tokens=200, model="qwen3:8b", client=client
        )
        assert any(
            m.get("role") == "system" and m.get("content") == sys_content
            for m in compacted
        )

    def test_compaction_error_returns_gracefully(self):
        messages = [{"role": "system", "content": "sys"}]
        for i in range(20):
            messages.append({"role": "user", "content": f"Q{i}"})
            messages.append({"role": "assistant", "content": f"A{i}"})

        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("API error")
        compacted, summary = compact_messages(
            messages, target_tokens=200, model="qwen3:8b", client=client
        )
        # Should still return something usable with error in summary
        assert len(compacted) > 0
        assert "error" in summary.lower() or "Compaction error" in summary

    def test_issue_273_compact_orphaned_tool_message(self):
        # Regression: compaction must not produce an orphaned role=tool message.
        # If to_keep[0].role == "tool", compact_messages must walk back to include
        # the paired assistant message (with tool_calls). Without it the history
        # is invalid and OpenAI APIs reject the request.

        # --- Case 1: orphaned tool message with no paired assistant-with-tool_calls ---
        # compact_messages should fall through gracefully (no crash, no tool first).
        messages2 = [{"role": "system", "content": "You are helpful."}]
        for i in range(10):
            messages2.append({"role": "user", "content": "Q{}".format(i)})
            messages2.append({"role": "assistant", "content": "A{}".format(i)})
        # Place tool message at index -6 (exactly 5 messages follow it)
        messages2.append(
            {"role": "tool", "tool_call_id": "call_orphan", "content": "orphan"}
        )
        messages2.append({"role": "user", "content": "F0"})
        messages2.append({"role": "assistant", "content": "R0"})
        messages2.append({"role": "user", "content": "F1"})
        messages2.append({"role": "assistant", "content": "R1"})
        messages2.append({"role": "user", "content": "F2"})

        client = self._make_client("summary")
        compacted, _ = compact_messages(
            messages2, target_tokens=500, model="qwen3:8b", client=client
        )
        non_system = [m for m in compacted if m.get("role") != "system"]
        assert (
            non_system[0].get("role") != "tool"
        ), "First non-system message must not be role=tool; got: {}".format(
            non_system[0]
        )

        # --- Case 2: paired assistant-with-tool_calls just before keep boundary ---
        # The assistant message must be pulled into to_keep so the tool message
        # is preceded by its assistant-with-tool_calls partner.
        messages3 = [{"role": "system", "content": "You are helpful."}]
        for i in range(10):
            messages3.append({"role": "user", "content": "Q{}".format(i)})
            messages3.append({"role": "assistant", "content": "A{}".format(i)})
        messages3.append({"role": "user", "content": "Run a command"})
        # This assistant message sits just before the keep boundary (index -7)
        messages3.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            }
        )
        # to_keep[0] will be this tool message (index -6)
        messages3.append(
            {"role": "tool", "tool_call_id": "call_2", "content": "output"}
        )
        # Exactly 5 more messages so the tool lands at position -6
        messages3.append({"role": "user", "content": "Follow0"})
        messages3.append({"role": "assistant", "content": "Resp0"})
        messages3.append({"role": "user", "content": "Follow1"})
        messages3.append({"role": "assistant", "content": "Resp1"})
        messages3.append({"role": "user", "content": "Follow2"})

        client = self._make_client("summary")
        compacted3, _ = compact_messages(
            messages3, target_tokens=2000, model="qwen3:8b", client=client
        )
        non_system3 = [m for m in compacted3 if m.get("role") != "system"]
        tool_idx = next(
            (i for i, m in enumerate(non_system3) if m.get("role") == "tool"), None
        )
        assert (
            tool_idx is not None
        ), "tool message should be present in compacted output"
        assert tool_idx > 0, "tool message must not be the first non-system message"
        preceding = non_system3[tool_idx - 1]
        assert (
            preceding.get("role") == "assistant"
        ), "Message before tool must be assistant, got {}".format(preceding.get("role"))
        assert preceding.get(
            "tool_calls"
        ), "Message before tool must have tool_calls array"

    def test_compact_trigger_fraction(self):
        assert COMPACT_TRIGGER_FRACTION == 0.75

    def test_compact_warn_pct(self):
        assert _COMPACT_WARN_PCT == 75.0


if __name__ == "__main__":
    unittest.main(verbosity=2)
