"""
Test Issue #283: Bug - Codex responses append internal session rollout error

This test verifies that internal error logs from the Codex runtime are properly
stripped from user-facing responses.
"""

import sys
import os
from pathlib import Path

# Add parent directory to path to import agent_manager
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_manager import SessionManager


def test_issue_283_codex_error_stripping():
    """
    Verify that Codex responses with internal error logs are properly cleaned.

    The bug was that responses ended with:
    2026-04-29T01:38:52.799391Z ERROR codex_core::sessionn: failed to record rollout items: thread 019dd6e3-7e78-7c73-92ca-ad208c03ef53 not found

    This test ensures such error lines are stripped from the final output.
    """
    manager = SessionManager()

    # Sample Codex output with the problematic error at the end
    codex_output_with_error = """[Session] Starting new CODEX session with model gpt-5.3-codex in normal mode

codex
The answer is 4.

2 + 2 = 4

tokens used: 42
2026-04-29T01:38:52.799391Z ERROR codex_core::sessionn: failed to record rollout items: thread 019dd6e3-7e78-7c73-92ca-ad208c03ef53 not found"""

    # Apply strip_metadata for codex runtime
    result = manager.strip_metadata(codex_output_with_error, "codex")

    # Expected: The response should contain the actual answer but NOT the error line
    assert "The answer is 4." in result, "Response should contain the actual answer"
    assert "2 + 2 = 4" in result, "Response should contain the calculation"
    assert "ERROR" not in result, "Response should not contain ERROR lines"
    assert "failed to record rollout items" not in result, (
        "Response should not contain internal error messages"
    )
    assert "tokens used" not in result, "Response should not contain tokens metadata"

    # Verify the response is clean
    lines = result.split("\n")
    for line in lines:
        assert not line.startswith("2026-"), (
            f"Response should not contain timestamp-prefixed lines: {line}"
        )


def test_issue_283_codex_multiple_errors():
    """
    Test that Codex output with multiple error lines is properly handled.
    """
    manager = SessionManager()

    codex_output_multi_error = """[Session] Starting new CODEX session

codex
Here is your solution.

It works great.

tokens used: 100
2026-04-29T01:38:52.799391Z ERROR codex_core::sessionn: failed to record rollout items: thread 019dd6e3-7e78-7c73-92ca-ad208c03ef53 not found
2026-04-29T01:38:53.123456Z ERROR codex_core::sessions: cleanup failed"""

    result = manager.strip_metadata(codex_output_multi_error, "codex")

    assert "Here is your solution." in result
    assert "It works great." in result
    assert "ERROR" not in result
    assert "failed to record" not in result
    assert "cleanup failed" not in result


def test_issue_283_codex_clean_output():
    """
    Ensure that Codex output without errors remains unchanged.
    """
    manager = SessionManager()

    clean_codex_output = """[Session] Starting new CODEX session

codex
Clean response without errors.

tokens used: 50"""

    result = manager.strip_metadata(clean_codex_output, "codex")

    assert "Clean response without errors." in result
    assert "tokens used" not in result  # metadata stripped
    assert "ERROR" not in result
    assert len(result.strip()) > 0


if __name__ == "__main__":
    test_issue_283_codex_error_stripping()
    print("✓ test_issue_283_codex_error_stripping passed")

    test_issue_283_codex_multiple_errors()
    print("✓ test_issue_283_codex_multiple_errors passed")

    test_issue_283_codex_clean_output()
    print("✓ test_issue_283_codex_clean_output passed")

    print("\nAll tests passed!")
