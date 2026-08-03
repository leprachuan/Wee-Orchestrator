"""Regression coverage for a Wee model that promises, but does not call, search."""

import pytest


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


@pytest.mark.skip(
    reason="Covered the #446 fix inside _run_wee_openai_fallback, which #443 "
    "removed when the Wee runtime moved to the Copilot SDK's own agentic "
    "loop. The predicate above still holds; the end-to-end behaviour needs "
    "re-implementing and re-testing against the SDK path before this can be "
    "considered fixed on the current architecture. See the note on issue #446."
)
def test_prose_search_promise_returns_tool_results():
    """Placeholder for the SDK-path re-implementation of #446.

    What must be asserted once the behaviour is ported: when the model streams
    a prose promise to search and emits no structured ``search`` tool call, the
    runtime executes the search itself and the user receives the sourced result
    rather than the bare promise.
    """
