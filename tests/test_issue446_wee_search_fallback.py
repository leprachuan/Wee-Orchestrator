"""Regression coverage for a Wee model that promises, but does not call, search."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def test_prose_search_promises_are_detected_without_matching_completed_searches():
    from agent_manager import SessionManager

    assert SessionManager._wee_response_promises_search(
        "I'll search for current data on this topic."
    )
    assert SessionManager._wee_response_promises_search(
        "Let me web search for an authoritative source."
    )
    assert not SessionManager._wee_response_promises_search(
        "Web search completed. Here are the results."
    )
    assert not SessionManager._wee_response_promises_search(
        "I cannot search the web from this session."
    )


@patch("openai.OpenAI")
def test_prose_search_promise_returns_tool_results(mock_openai):
    """A provider's prose promise must not become the final user response."""
    from agent_manager import SessionManager

    manager = SessionManager.__new__(SessionManager)
    manager.command_timeout = 30
    manager.AGENTS = {"orchestrator": {"path": "/tmp"}}
    manager._stream_buffers = {}
    client = MagicMock()
    mock_openai.return_value = client
    client.chat.completions.create.return_value = iter(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="I'll search for current data on this topic.",
                            tool_calls=None,
                        )
                    )
                ]
            )
        ]
    )

    with (
        patch.object(manager, "get_or_create_session_data", return_value={"channel": "webui"}),
        patch.object(manager, "build_agent_context_prompt", return_value="context"),
        patch.object(manager, "_wee_augment_system_prompt_with_tools", side_effect=lambda value: value),
        patch.object(manager, "_wee_load_free_config", return_value={}),
        patch.object(manager, "_wee_is_free_model", return_value=False),
        patch.object(manager, "_wee_load_messages", return_value=[]),
        patch.object(manager, "_wee_get_context_limit_for_api", return_value=16_000),
        patch.object(manager, "_wee_maybe_compact", side_effect=lambda _client, _sid, messages, *_args, **_kwargs: messages),
        patch.object(manager, "_wee_execute_tool", return_value="Search results for: test\n1. Source") as execute_tool,
        patch.object(manager, "_wee_save_messages"),
    ):
        result = manager._run_wee_openai_fallback(
            prompt="test",
            model="ollama/test",
            agent="orchestrator",
            session_id=None,
            resume=False,
            n8n_session_id="search-fallback",
            timeout=30,
            render_type="text",
        )

    assert result == "Web search completed.\n\nSearch results for: test\n1. Source"
    execute_tool.assert_called_once_with(
        "search", {"q": "test", "count": 5, "format": "text"}, "orchestrator", "search-fallback"
    )
