"""
Regression tests for issue #251:
Bug: call_agent hitting 429 rate limit on /api/v1/background-tasks query

Root causes addressed:
  1. No lightweight capability to list background tasks — agents were forced
     to spin up a full Copilot/LLM session just to check task counts, burning
     weekly quota and triggering rate limits.
  2. "weekly rate limit" from Copilot not mapped to its own distinct error
     code (weekly_rate_limit_exceeded) — was swallowed by the generic
     "rate_limited" bucket, losing the actionable reset-time info.
  3. GET /api/v1/background-tasks had no server-side rate limiting, leaving
     it susceptible to polling storms from misbehaving agents.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Path setup ─────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))


class TestIssue251ListBgTasksCapability(unittest.TestCase):
    """Test the new list_background_tasks wee_executor capability."""

    def _import_executor(self):
        import importlib
        import wee_executor as we
        importlib.reload(we)
        return we

    def test_capability_registered(self):
        """list_background_tasks must appear in the capability registry."""
        we = self._import_executor()
        caps = {c["name"] for c in we.list_capabilities(we.MODE_INTERACTIVE)}
        self.assertIn(
            "list_background_tasks",
            caps,
            "list_background_tasks capability not registered",
        )

    def test_capability_available_in_background_mode(self):
        """Capability must be available in background mode (agents run in background)."""
        we = self._import_executor()
        caps = {c["name"] for c in we.list_capabilities(we.MODE_BACKGROUND)}
        self.assertIn(
            "list_background_tasks",
            caps,
            "list_background_tasks must be available in background mode",
        )

    def test_capability_no_required_args(self):
        """list_background_tasks must work with no arguments."""
        we = self._import_executor()
        cap = we.CAPABILITIES.get("list_background_tasks")
        self.assertIsNotNone(cap)
        self.assertEqual(cap.get("required_args", []), [])

    def test_capability_returns_counts(self):
        """Capability should return running/queued/done/failed counts."""
        we = self._import_executor()

        fake_tasks = {
            "tasks": [
                {"task_id": "bg_aaa", "agent": "devops", "status": "running", "prompt": "check cluster"},
                {"task_id": "bg_bbb", "agent": "research", "status": "running", "prompt": "search news"},
                {"task_id": "bg_ccc", "agent": "wee-dev", "status": "queued", "prompt": "implement feature"},
                {"task_id": "bg_ddd", "agent": "email-triage", "status": "done", "prompt": "triage inbox"},
                {"task_id": "bg_eee", "agent": "devops", "status": "failed", "prompt": "deploy service"},
            ]
        }

        with patch.object(we, "_api_request", return_value=fake_tasks):
            with patch.object(we, "_check_rate_limit", return_value=True):
                result = we.cap_list_background_tasks({}, "test_session", we.MODE_INTERACTIVE)

        self.assertEqual(result["total"], 5)
        self.assertEqual(result["running"], 2)
        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["done"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(result["tasks"]), 5)

    def test_capability_status_filter(self):
        """status_filter arg should limit returned tasks to that status."""
        we = self._import_executor()

        fake_tasks = {
            "tasks": [
                {"task_id": "bg_aaa", "agent": "devops", "status": "running", "prompt": "check cluster"},
                {"task_id": "bg_bbb", "agent": "research", "status": "done", "prompt": "search news"},
            ]
        }

        with patch.object(we, "_api_request", return_value=fake_tasks):
            with patch.object(we, "_check_rate_limit", return_value=True):
                result = we.cap_list_background_tasks(
                    {"status_filter": "running"}, "test_session", we.MODE_INTERACTIVE
                )

        self.assertEqual(result["total"], 2)  # total counts all tasks
        self.assertEqual(result["running"], 1)
        self.assertEqual(len(result["tasks"]), 1)  # filter applied to task list
        self.assertEqual(result["tasks"][0]["task_id"], "bg_aaa")

    def test_capability_handles_api_error(self):
        """API error should be returned as dict with 'error' key."""
        we = self._import_executor()

        with patch.object(we, "_api_request", return_value={"error": "HTTP 503: Service unavailable", "code": "HTTP_503"}):
            with patch.object(we, "_check_rate_limit", return_value=True):
                result = we.cap_list_background_tasks({}, "test_session", we.MODE_INTERACTIVE)

        self.assertIn("error", result)

    def test_capability_does_not_invoke_llm(self):
        """list_background_tasks must call _api_request, NOT a subprocess (no LLM)."""
        we = self._import_executor()
        api_call_count = []

        def fake_api(method, path, **kwargs):
            api_call_count.append((method, path))
            return {"tasks": []}

        with patch.object(we, "_api_request", side_effect=fake_api):
            with patch.object(we, "_check_rate_limit", return_value=True):
                with patch("subprocess.run") as mock_subproc:
                    we.cap_list_background_tasks({}, "test_session", we.MODE_INTERACTIVE)
                    mock_subproc.assert_not_called()

        self.assertEqual(len(api_call_count), 1)
        self.assertEqual(api_call_count[0], ("GET", "/api/v1/background-tasks"))


class TestIssue251WeeklyRateLimitDetection(unittest.TestCase):
    """Test that 'weekly rate limit' from Copilot is detected with its own error code."""

    def _get_runtime_error_patterns(self):
        """Extract _RUNTIME_ERROR_PATTERNS from agent_manager source without full import."""
        am_path = REPO / "agent_manager.py"
        source = am_path.read_text()
        # Find the _RUNTIME_ERROR_PATTERNS list in source
        import ast
        # Parse just enough to extract the list
        start = source.index("_RUNTIME_ERROR_PATTERNS = [")
        end = source.index("]", start) + 1
        snippet = "_RUNTIME_ERROR_PATTERNS = " + source[start + len("_RUNTIME_ERROR_PATTERNS = "):end]
        tree = ast.parse(snippet)
        # The list value
        assign = tree.body[0]
        patterns = []
        for elt in assign.value.elts:
            tup = tuple(c.value for c in elt.elts)
            patterns.append(tup)
        return patterns

    def test_weekly_rate_limit_has_distinct_error_code(self):
        """'weekly rate limit' must map to weekly_rate_limit_exceeded, not rate_limited."""
        patterns = self._get_runtime_error_patterns()
        weekly_patterns = [(p, s, c) for p, s, c in patterns if "weekly" in p.lower()]
        self.assertTrue(len(weekly_patterns) > 0, "No pattern for 'weekly rate limit' found")
        for _, status, code in weekly_patterns:
            self.assertEqual(status, 429)
            self.assertEqual(
                code,
                "weekly_rate_limit_exceeded",
                f"Expected 'weekly_rate_limit_exceeded', got '{code}'",
            )

    def test_weekly_rate_limit_pattern_before_generic_rate_limit(self):
        """'weekly rate limit' pattern must appear before generic 'rate limit' to take priority."""
        patterns = self._get_runtime_error_patterns()
        pattern_names = [p for p, _, _ in patterns]
        self.assertIn("weekly rate limit", pattern_names, "weekly rate limit pattern missing")
        self.assertIn("rate limit", pattern_names, "rate limit pattern missing")
        weekly_idx = pattern_names.index("weekly rate limit")
        generic_idx = pattern_names.index("rate limit")
        self.assertLess(
            weekly_idx,
            generic_idx,
            "weekly rate limit pattern must come before generic rate limit for proper matching priority",
        )

    def test_copilot_weekly_limit_message_triggers_429(self):
        """Simulated Copilot weekly-limit message must produce a 429 with the right error code."""
        patterns = self._get_runtime_error_patterns()
        copilot_msg = "You've reached your weekly rate limit. Please wait for your limit to reset in 3 hours 42 minutes"

        matched_code = None
        matched_status = None
        for pattern, status, code in patterns:
            if pattern.lower() in copilot_msg.lower():
                matched_code = code
                matched_status = status
                break

        self.assertEqual(matched_status, 429)
        self.assertEqual(
            matched_code,
            "weekly_rate_limit_exceeded",
            f"Copilot weekly-limit message should produce weekly_rate_limit_exceeded, got {matched_code!r}",
        )


class TestIssue251BgTasksListEndpointRateLimit(unittest.TestCase):
    """Test that GET /api/v1/background-tasks has a server-side rate limit."""

    def test_endpoint_has_rate_limit_check(self):
        """GET /api/v1/background-tasks handler must call rate_limiter.check."""
        am_path = REPO / "agent_manager.py"
        source = am_path.read_text()

        # Find the list_background_tasks handler
        handler_start = source.index('@app.get("/api/v1/background-tasks")\n    async def list_background_tasks')
        # Find the next route definition after it
        next_route_start = source.index('@app.get("/api/v1/background-tasks/{task_id}")')
        handler_body = source[handler_start:next_route_start]

        self.assertIn(
            "rate_limiter.check",
            handler_body,
            "GET /api/v1/background-tasks must have a rate_limiter.check() call",
        )

    def test_rate_limit_is_generous_for_status_checks(self):
        """Rate limit for bg_tasks_list must be >= 60 req/min (status checks must not false-trigger)."""
        am_path = REPO / "agent_manager.py"
        source = am_path.read_text()

        handler_start = source.index('@app.get("/api/v1/background-tasks")\n    async def list_background_tasks')
        next_route_start = source.index('@app.get("/api/v1/background-tasks/{task_id}")')
        handler_body = source[handler_start:next_route_start]

        import re
        m = re.search(r'bg_tasks_list.*?max_requests=(\d+)', handler_body)
        self.assertIsNotNone(m, "Could not find bg_tasks_list rate limit configuration")
        limit = int(m.group(1))
        self.assertGreaterEqual(
            limit, 60,
            f"bg_tasks_list rate limit ({limit}/min) is too low — must be >= 60 to avoid false positives on routine status checks",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
