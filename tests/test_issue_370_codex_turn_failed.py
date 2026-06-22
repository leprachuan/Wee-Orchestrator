"""Regression tests for Codex turn.failed handling.

When a Codex turn fails (sandbox, error, etc.), only planning messages
were returned without error context. This ensures turn.failed and error
events are captured and surfaced in strip_metadata output.
"""

import sys
sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

from agent_manager import SessionManager


class TestCodexTurnFailed:
    def setup_method(self):
        self.mgr = SessionManager.__new__(SessionManager)

    def _make_jsonl(self, *events):
        return "\n".join(events)

    def test_normal_multiturn_returns_last_message(self):
        output = self._make_jsonl(
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"Planning step..."}}',
            '{"type":"item.completed","item":{"id":"i1","type":"command_execution","command":"ls","aggregated_output":"ok","exit_code":0,"status":"completed"}}',
            '{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"Here is the final answer."}}',
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}',
        )
        result = self.mgr.strip_metadata(output, "codex")
        assert "final answer" in result
        assert "Planning" not in result

    def test_turn_failed_with_single_planning_message(self):
        output = self._make_jsonl(
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"I will check the context."}}',
            '{"type":"turn.failed","error":{"message":"Sandbox blocked network access"}}',
        )
        result = self.mgr.strip_metadata(output, "codex")
        assert "I will check" in result
        assert "failed" in result.lower()
        assert "Sandbox" in result

    def test_turn_failed_with_multiple_messages_no_error_appended(self):
        output = self._make_jsonl(
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"Planning..."}}',
            '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"The answer is 42."}}',
            '{"type":"turn.failed","error":{"message":"minor error"}}',
        )
        result = self.mgr.strip_metadata(output, "codex")
        assert "answer is 42" in result
        assert "failed" not in result.lower()

    def test_error_event_no_messages(self):
        output = self._make_jsonl(
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"error","message":"API rate limit exceeded"}',
        )
        result = self.mgr.strip_metadata(output, "codex")
        assert "rate limit" in result.lower()

    def test_error_event_with_nested_error(self):
        output = self._make_jsonl(
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"error","message":"{\\"type\\":\\"error\\",\\"status\\":400,\\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"Model not supported\\"}}"}',
        )
        result = self.mgr.strip_metadata(output, "codex")
        assert result.strip()  # Should have some error text

    def test_turn_failed_with_empty_error(self):
        output = self._make_jsonl(
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"Planning..."}}',
            '{"type":"turn.failed","error":{}}',
        )
        result = self.mgr.strip_metadata(output, "codex")
        assert "Planning" in result
        assert "unknown error" in result.lower()
