"""
Regression test for GitHub Issue #282:
Bug: runtime crashes with unbound local variable 'messages'.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestIssue282UnboundMessages:
    """Tests for unbound local variable 'messages' bug (#282)"""

    def test_wee_native_method_exists(self):
        """Test that run_wee_native method exists in SessionManager"""
        from agent_manager import SessionManager

        manager = SessionManager()
        assert hasattr(manager, "run_wee_native"), "run_wee_native method should exist"

    def test_wee_load_messages_returns_list(self):
        """Test that _wee_load_messages always returns a list"""
        from agent_manager import SessionManager

        manager = SessionManager()

        result = manager._wee_load_messages(
            n8n_session_id="test",
            context_prompt="test",
            resume=True,
        )

        assert isinstance(result, list), "Should return a list"
        assert len(result) >= 0, "List should be valid"

    def test_wee_load_messages_with_no_session(self):
        """Test _wee_load_messages with non-existent session"""
        from agent_manager import SessionManager

        manager = SessionManager()

        result = manager._wee_load_messages(
            n8n_session_id="non_existent_session_282",
            context_prompt="Test context",
            resume=True,
        )

        assert isinstance(result, list), "Should return a list"

    def test_run_wee_native_load_messages_exception_does_not_crash(self):
        """Regression: _wee_load_messages raising must not cause UnboundLocalError.

        This is the primary regression test for issue #282. On the unfixed code,
        if _wee_load_messages raises, 'messages' is never assigned and the
        subsequent messages.append(...) call raises UnboundLocalError.
        """
        from agent_manager import SessionManager

        manager = SessionManager()

        # Minimal stream mock that yields one chunk then stops.
        mock_chunk = MagicMock()
        mock_chunk.choices = [MagicMock()]
        mock_chunk.choices[0].delta.content = "hi"
        mock_chunk.choices[0].delta.tool_calls = None
        mock_chunk.choices[0].finish_reason = "stop"

        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([mock_chunk]))

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_stream

        # OpenAI is imported locally inside run_wee_native — patch at source.
        with patch.object(
            manager, "_wee_load_messages", side_effect=Exception("disk error")
        ), patch("openai.OpenAI", return_value=mock_client), patch.object(
            manager, "_wee_save_messages", return_value=None
        ), patch.object(
            manager, "build_agent_context_prompt", return_value="system prompt"
        ), patch.object(
            manager,
            "_wee_augment_system_prompt_with_tools",
            return_value="system prompt",
        ):
            try:
                result = manager.run_wee_native(
                    prompt="hello",
                    model="ollama/test-model",
                    agent="orchestrator",
                    session_id=None,
                    resume=False,
                    n8n_session_id="test-session-282",
                    timeout=30,
                )
                # Consume if iterable (streaming generator).
                if hasattr(result, "__iter__") and not isinstance(result, str):
                    list(result)
            except UnboundLocalError as e:
                pytest.fail(f"UnboundLocalError raised — bug #282 regression: {e}")
            except Exception:
                # Other failures (network, config, etc.) are acceptable —
                # we only care that UnboundLocalError does NOT occur.
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
