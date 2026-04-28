"""Regression tests for Issue #273: Context Window Management and Compaction."""

import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

from wee_cli import TokenTracker  # noqa: E402
from wee_runtime import (  # noqa: E402
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
        tracker = TokenTracker(context_window=10000)
        usage = MagicMock()
        usage.prompt_tokens = 1000
        usage.completion_tokens = 500
        usage.total_tokens = 1500
        tracker.update(usage)
        assert tracker.session_total == 1500
        assert abs(tracker.percent_used() - 15.0) < 0.01

    def test_percent_used_capped_at_100(self):
        tracker = TokenTracker(context_window=100)
        usage = MagicMock()
        usage.prompt_tokens = 0
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

    def test_compact_trigger_fraction(self):
        assert COMPACT_TRIGGER_FRACTION == 0.75


if __name__ == "__main__":
    unittest.main(verbosity=2)
