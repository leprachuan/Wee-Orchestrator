"""Tests for Issue #203: Copilot session token refresh for long-running tasks.

Validates that run_copilot:
1. Detects "Session token expired" in subprocess output and restarts
2. Injects prior context into the continuation session
3. Implements proactive refresh by capping segments at the refresh interval
4. Does NOT loop forever — respects max retries and overall timeout
5. Returns combined output from all segments on natural completion
"""

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


def _get_session_mgr():
    """Create a minimal SessionManager for testing without full constructor."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    mgr.mode = None
    mgr.command_timeout = 1800
    import threading

    mgr._session_map_lock = threading.Lock()
    import pathlib

    mgr.session_map_file = pathlib.Path("/tmp/_test_203_session_map.json")
    mgr.session_state_dir = pathlib.Path("/tmp/_test_203_sessions")
    mgr.session_state_dir.mkdir(parents=True, exist_ok=True)
    mgr.copilot_bin = "/usr/local/bin/copilot"
    mgr.AGENTS = {
        "orchestrator": {
            "path": "/opt/n8n-copilot-shim-dev",
            "description": "Test",
            "name": "orchestrator",
        }
    }
    mgr.skill_repositories = []
    mgr._bg_identity = None
    mgr._stream_buffers = {}
    mgr._stream_queues = {}
    mgr._last_exit_codes = {}
    mgr._running_queries = {}
    mgr._running_queries_lock = threading.Lock()
    return mgr


class TestCopilotTokenRefresh(unittest.TestCase):
    """Test suite for Issue #203: Copilot session token refresh."""

    def setUp(self):
        self.mgr = _get_session_mgr()
        self.session_id = "test-203-session"

    # ── Helpers ─────────────────────────────────────────────────────────

    def _make_run_copilot_args(self, prompt="Do work", timeout=3600):
        return dict(
            prompt=prompt,
            model="gpt-4o",
            agent="orchestrator",
            session_id=None,
            resume=False,
            n8n_session_id=self.session_id,
            timeout=timeout,
            render_type="text",
        )

    # ── Test 1: No token expiry — normal single-shot completion ──────────

    def test_normal_completion_no_refresh(self):
        """Task completes in first segment — no retry loop entered."""
        with (
            patch.object(
                self.mgr,
                "_execute_subprocess_with_tracking",
                return_value="Task complete. All done.",
            ) as mock_exec,
            patch.object(
                self.mgr, "_parse_mode_command", return_value=("Do work", "restricted")
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr, "build_agent_context_prompt", return_value="[context] Do work"
            ),
        ):
            result = self.mgr.run_copilot(**self._make_run_copilot_args())

        # Should call subprocess exactly once
        self.assertEqual(mock_exec.call_count, 1)
        self.assertIn("Task complete", result)

    # ── Test 2: Reactive recovery — token expires mid-task ───────────────

    def test_reactive_token_expiry_triggers_restart(self):
        """When 'Session token expired' appears in output, a new session starts."""
        from agent_manager import _COPILOT_TOKEN_EXPIRED_PHRASE

        _expiry_msg = (
            f"{_COPILOT_TOKEN_EXPIRED_PHRASE}. Please resend your message."
            " (Request ID: req-test-001)"
        )
        call_outputs = [
            f"Working on step 1...\n{_expiry_msg}",
            "Continuing from step 2. Task complete.",
        ]
        call_index = [0]

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            out = call_outputs[call_index[0]]
            call_index[0] += 1
            return out

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr, "_parse_mode_command", return_value=("Do work", "restricted")
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr,
                "build_agent_context_prompt",
                return_value="[context] continuation",
            ),
        ):
            result = self.mgr.run_copilot(**self._make_run_copilot_args())

        # subprocess called twice: original + recovery
        self.assertEqual(call_index[0], 2)
        # Final combined output includes both segments
        self.assertIn("Working on step 1", result)
        self.assertIn("Continuing from step 2", result)
        # Successful recovery must NOT surface the internal expiry marker
        self.assertNotIn("Session token expired", result)

    # ── Test 3: Prior context injected in continuation prompt ────────────

    def test_continuation_prompt_contains_prior_work(self):
        """Continuation session receives a summary of prior work."""
        from agent_manager import _COPILOT_TOKEN_EXPIRED_PHRASE

        prior_work = "Finished deploying service-A. Updated config."
        _expiry_msg_2 = (
            f"{_COPILOT_TOKEN_EXPIRED_PHRASE}. Please resend your message."
            " (Request ID: req-test-002)"
        )
        call_outputs = [
            f"{prior_work}\n{_expiry_msg_2}",
            "Done.",
        ]
        call_index = [0]
        captured_context_prompts = []

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            out = call_outputs[call_index[0]]
            call_index[0] += 1
            return out

        def fake_build_context(  # noqa: E501
            agent, user_prompt, sess_id, render, timeout, runtime, model, channel
        ):
            captured_context_prompts.append(user_prompt)
            return f"[ctx] {user_prompt}"

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr,
                "_parse_mode_command",
                return_value=("Original task", "restricted"),
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr, "build_agent_context_prompt", side_effect=fake_build_context
            ),
        ):
            self.mgr.run_copilot(**self._make_run_copilot_args(prompt="Original task"))

        # First build_agent_context_prompt call is for the initial session
        # Second call should include prior work context
        self.assertGreaterEqual(len(captured_context_prompts), 2)
        continuation_prompt = captured_context_prompts[1]
        self.assertIn("SESSION CONTINUATION", continuation_prompt)
        self.assertIn(prior_work[:20], continuation_prompt)

    def test_expiry_without_prior_output_is_not_used_as_progress(self):
        """A pure expiry chunk must not be injected as prior session progress."""
        from agent_manager import _COPILOT_TOKEN_EXPIRED_PHRASE

        expiry_only = (
            f"{_COPILOT_TOKEN_EXPIRED_PHRASE}. Please resend your message."
            " (Request ID: req-test-002b)"
        )
        call_outputs = [expiry_only, "Recovered successfully."]
        call_index = [0]
        captured_context_prompts = []

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            out = call_outputs[call_index[0]]
            call_index[0] += 1
            return out

        def fake_build_context(
            agent, user_prompt, sess_id, render, timeout, runtime, model, channel
        ):
            captured_context_prompts.append(user_prompt)
            return f"[ctx] {user_prompt}"

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr,
                "_parse_mode_command",
                return_value=("Recover task", "restricted"),
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr, "build_agent_context_prompt", side_effect=fake_build_context
            ),
        ):
            result = self.mgr.run_copilot(**self._make_run_copilot_args(prompt="Recover task"))

        self.assertEqual(call_index[0], 2)
        self.assertIn("Recovered successfully.", result)
        self.assertNotIn("Session token expired", result)
        self.assertGreaterEqual(len(captured_context_prompts), 2)
        continuation_prompt = captured_context_prompts[1]
        self.assertNotIn("PRIOR SESSION PROGRESS", continuation_prompt)
        self.assertNotIn("Please resend your message", continuation_prompt)

    # ── Test 4: Proactive refresh — segment timed out ────────────────────

    def test_proactive_refresh_on_segment_timeout(self):
        """Segment timeout (25-min cap hit) triggers a refresh and continuation."""
        call_outputs = [
            "Error: Command timed out (exceeded 1500s / 25.0min)",
            "Task resumed and complete.",
        ]
        call_index = [0]

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            out = call_outputs[call_index[0]]
            call_index[0] += 1
            return out

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr,
                "_parse_mode_command",
                return_value=("Long task", "restricted"),
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr,
                "build_agent_context_prompt",
                return_value="[ctx] continuation",
            ),
        ):
            result = self.mgr.run_copilot(**self._make_run_copilot_args(timeout=7200))

        # Both segments ran
        self.assertEqual(call_index[0], 2)
        self.assertIn("Task resumed and complete", result)
        # Intermediate timeout error must be stripped from final transcript
        self.assertNotIn("Error: Command timed out", result)

    # ── Test 5: Segment timeout is capped at refresh interval ────────────

    def test_segment_timeout_capped_at_refresh_interval(self):
        """Each subprocess call uses min(refresh_interval, remaining) as timeout."""
        from agent_manager import COPILOT_TOKEN_REFRESH_INTERVAL

        captured_timeouts = []

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            captured_timeouts.append(timeout)
            return "Done."

        overall_timeout = COPILOT_TOKEN_REFRESH_INTERVAL * 3  # 75 min

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr, "_parse_mode_command", return_value=("task", "restricted")
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr, "build_agent_context_prompt", return_value="[ctx] task"
            ),
        ):
            self.mgr.run_copilot(**self._make_run_copilot_args(timeout=overall_timeout))

        # The single call (task completes naturally) should use
        # at most COPILOT_TOKEN_REFRESH_INTERVAL as its timeout
        self.assertEqual(len(captured_timeouts), 1)
        self.assertLessEqual(
            captured_timeouts[0],
            COPILOT_TOKEN_REFRESH_INTERVAL,
            f"Segment timeout {captured_timeouts[0]} exceeded refresh interval "
            f"{COPILOT_TOKEN_REFRESH_INTERVAL}",
        )

    # ── Test 6: Max retries respected ────────────────────────────────────

    def test_max_retries_prevents_infinite_loop(self):
        """Token keeps expiring: loop stops after COPILOT_TOKEN_REFRESH_MAX_RETRIES.

        Regression for blocker #2 (Issue #203 Round 4): when all retries are
        exhausted the function must return an *explicit error*, NOT silently
        truncate the partial output as if the task succeeded.
        """
        from agent_manager import _COPILOT_TOKEN_EXPIRED_PHRASE

        call_count = [0]

        def always_expire(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            call_count[0] += 1
            _msg = (
                f"{_COPILOT_TOKEN_EXPIRED_PHRASE}. Please resend your message."
                " (Request ID: req-test-003)"
            )
            return f"Some work\n{_msg}"

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=always_expire
            ),
            patch.object(
                self.mgr, "_parse_mode_command", return_value=("task", "restricted")
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(self.mgr, "build_agent_context_prompt", return_value="[ctx]"),
        ):
            result = self.mgr.run_copilot(
                **self._make_run_copilot_args(timeout=3600 * 10)
            )

        from agent_manager import COPILOT_TOKEN_REFRESH_MAX_RETRIES

        # Exactly MAX_RETRIES + 1 calls (initial + max_retries refreshes)
        # The loop breaks when _refresh_count >= COPILOT_TOKEN_REFRESH_MAX_RETRIES
        self.assertEqual(call_count[0], COPILOT_TOKEN_REFRESH_MAX_RETRIES + 1)
        # Recovery was abandoned — result MUST be an explicit error, not partial success
        self.assertTrue(
            result.startswith("Error:"),
            f"Expected explicit error on retry exhaustion, got: {result[:120]!r}",
        )
        # The token-expiry phrase must NOT appear in the error
        # (stripped for readability)
        self.assertNotIn(_COPILOT_TOKEN_EXPIRED_PHRASE, result)
        # Partial work from earlier segments should be surfaced in the error message
        self.assertIn("Some work", result)

    # ── Test 6b: Retry exhaustion via time budget (< 60s remaining) ──────

    def test_low_remaining_time_fails_explicitly(self):
        """When <60s remains after a token expiry, recovery fails explicitly.

        Regression for blocker #2 (Issue #203 Round 4): the time-budget exit
        path must also return an explicit error, not silently return partial work.
        """
        from agent_manager import _COPILOT_TOKEN_EXPIRED_PHRASE

        call_count = [0]

        def expire_then_stop(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            call_count[0] += 1
            _msg = (
                f"{_COPILOT_TOKEN_EXPIRED_PHRASE}. Please resend your message."
                " (Request ID: req-test-004)"
            )
            return f"Partial work\n{_msg}"

        with (
            patch.object(
                self.mgr,
                "_execute_subprocess_with_tracking",
                side_effect=expire_then_stop,
            ),
            patch.object(
                self.mgr, "_parse_mode_command", return_value=("task", "restricted")
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(self.mgr, "build_agent_context_prompt", return_value="[ctx]"),
            patch("time.time") as mock_time,
        ):
            # Simulate: start at t=0, first segment finishes at t=10,
            # then _remaining_after = 120 - 61 = 59 < 60 → breaks before retry
            mock_time.side_effect = [0.0, 10.0, 61.0]
            result = self.mgr.run_copilot(**self._make_run_copilot_args(timeout=120))

        self.assertEqual(call_count[0], 1, "Should only dispatch one segment")
        self.assertTrue(
            result.startswith("Error:"),
            f"Expected explicit error when time budget exhausted, "
            f"got: {result[:120]!r}",
        )
        self.assertNotIn(_COPILOT_TOKEN_EXPIRED_PHRASE, result)

    # ── Test 7: Module-level constants are importable ─────────────────────

    def test_module_constants_are_defined(self):
        """Required constants exist and have sensible defaults."""
        import agent_manager as am

        self.assertIsInstance(am.COPILOT_TOKEN_REFRESH_INTERVAL, int)
        self.assertGreater(am.COPILOT_TOKEN_REFRESH_INTERVAL, 0)
        self.assertLess(am.COPILOT_TOKEN_REFRESH_INTERVAL, 30 * 60 + 1)  # <= 30 min

        self.assertIsInstance(am.COPILOT_TOKEN_REFRESH_MAX_RETRIES, int)
        self.assertGreater(am.COPILOT_TOKEN_REFRESH_MAX_RETRIES, 0)

        self.assertIsInstance(am.COPILOT_CONTEXT_SUMMARY_MAX_CHARS, int)
        self.assertGreater(am.COPILOT_CONTEXT_SUMMARY_MAX_CHARS, 0)

        self.assertIsInstance(am._COPILOT_TOKEN_EXPIRED_PHRASE, str)
        self.assertIn("Session token expired", am._COPILOT_TOKEN_EXPIRED_PHRASE)

    # ── Test 8: Sub-30-second total timeout regression ──────────────────

    def test_segment_timeout_never_exceeds_remaining_budget(self):
        """Segment timeout must never exceed the overall task budget.

        Regression for the bug where max(30, effective_timeout - elapsed)
        caused a 5-second task to still dispatch a 30-second subprocess.
        """
        captured_timeouts = []

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            captured_timeouts.append(timeout)
            return "Done."

        short_timeout = 5  # overall task budget is 5 seconds

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr, "_parse_mode_command", return_value=("task", "restricted")
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr, "build_agent_context_prompt", return_value="[ctx] task"
            ),
        ):
            self.mgr.run_copilot(**self._make_run_copilot_args(timeout=short_timeout))

        if captured_timeouts:
            self.assertLessEqual(
                captured_timeouts[0],
                short_timeout,
                f"Segment timeout {captured_timeouts[0]}s exceeded overall budget "
                f"of {short_timeout}s — max(30, ...) floor bug has returned.",
            )

    # ── Test 9: Sub-second remaining budget — loop breaks without overshoot ──

    def test_sub_second_remaining_budget_breaks_loop(self):
        """When remaining budget drops below 1s, the loop breaks without dispatching.

        Regression for two prior QA rejections:
        - Round 2: int(min(1500, 0.4)) == 0 → zero-second subprocess
          (divide-by-zero class)
        - Round 3: max(1, int(min(1500, 0.4))) == 1 → overshoots 0.4s budget

        Correct behaviour (Round 4): when _remaining < 1, break immediately.
        This test proves exactly one dispatch happens, then the loop breaks on the
        second iteration when _remaining == 0.5 < 1.
        """
        captured_timeouts = []
        call_count = [0]

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            call_count[0] += 1
            captured_timeouts.append(timeout)
            # Simulate proactive timeout to keep the loop going
            return "Error: Command timed out (exceeded 1500s / 25.0min)"

        overall_timeout = 120  # 2 minutes — big enough so _remaining_after >= 60

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr, "_parse_mode_command", return_value=("task", "restricted")
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr, "build_agent_context_prompt", return_value="[ctx] task"
            ),
            patch("time.time") as mock_time,
        ):
            # time.time() call sequence (4 calls total):
            #   1. _task_start = time.time()                          → 0.0
            #   2. Loop iter 1: _elapsed = time.time() - _task_start  → 10.0
            #      _remaining = 120 - 10 = 110 ≥ 1 → dispatches segment
            #      Segment returns "Error: Command timed out..."
            #   3. _remaining_after = 120 - (time.time() - 0)        → 90 ≥ 60
            #      So we continue (not break early) and rebuild continuation
            #   4. Loop iter 2: _elapsed = time.time() - _task_start  → 119.5
            #      _remaining = 120 - 119.5 = 0.5 < 1 → BREAK (the fix)
            mock_time.side_effect = [0.0, 10.0, 30.0, 119.5]

            self.mgr.run_copilot(**self._make_run_copilot_args(timeout=overall_timeout))

        # Exactly ONE dispatch: second iteration hit _remaining < 1 and broke
        self.assertEqual(
            call_count[0],
            1,
            f"Expected 1 dispatch (loop breaks when _remaining=0.5 < 1), "
            f"got {call_count[0]}",
        )
        self.assertEqual(len(captured_timeouts), 1)
        # First dispatch timeout must be within the overall budget
        self.assertLessEqual(
            captured_timeouts[0],
            overall_timeout,
            f"Segment timeout {captured_timeouts[0]}s exceeded "
            f"overall budget {overall_timeout}s",
        )
        # And it must be positive
        self.assertGreater(captured_timeouts[0], 0, "Segment timeout must be > 0")

    # ── Test 10: Blocking path honours timeout even with chatty subprocess ──
    def test_segment_timeout_interrupts_chatty_subprocess(self):
        """_execute_subprocess_with_tracking returns timeout error even when
        the subprocess is actively writing stdout.

        Regression for blocker #1 (Issue #203 Round 4): the old code looped
        over process.stdout without any deadline check, so a process that kept
        emitting lines could run past the requested timeout uninterrupted.

        Fix: stdout is drained on a background thread while a queue-based loop
        enforces the deadline on every iteration.
        """
        import sys
        from unittest.mock import patch as _patch

        # Process emits one line immediately then sleeps for 5 s.
        # With a 1-second timeout the function must terminate it early.
        cmd = [
            sys.executable,
            "-c",
            (
                "import time, sys;"
                " print('partial-output', flush=True);"
                " time.sleep(5)"
            ),
        ]

        mgr = self.mgr
        # Provide the attributes that _execute_subprocess_with_tracking needs
        # but that the minimal test SessionManager does not have.
        import pathlib as _pl

        mgr.running_queries_file = _pl.Path("/tmp/_test_203_rq.json")
        with (
            _patch.object(mgr, "set_live_status"),
            _patch.object(mgr, "clear_live_status"),
            _patch.object(mgr, "update_query_output"),
            _patch.object(mgr, "track_running_query"),
            _patch.object(mgr, "clear_running_query"),
        ):
            import time as _time

            _t0 = _time.time()
            result = mgr._execute_subprocess_with_tracking(
                cmd=cmd,
                cwd="/tmp",
                timeout=1,
                runtime="copilot",
                agent="orchestrator",
                prompt="test",
                n8n_session_id=self.session_id,
            )
            _elapsed = _time.time() - _t0

        # Must return a timeout error — not the partial stdout
        self.assertTrue(
            result.startswith("Error: Command timed out"),
            f"Expected timeout error, got: {result[:120]!r}",
        )
        # Must complete well before the subprocess 5-second sleep finishes
        self.assertLess(
            _elapsed,
            4.0,
            f"_execute_subprocess_with_tracking took {_elapsed:.2f}s — "
            "timeout was not enforced during stdout reading",
        )

    # ── Test 11: Phrase in transcript body does NOT trigger refresh ──────────

    def test_phrase_in_transcript_body_does_not_trigger_refresh(self):
        """Ordinary agent output that mentions the phrase must NOT trigger refresh.

        Regression for Issue #203 QA rejection: _execute_subprocess_with_tracking
        was monkeypatched to return normal transcript text containing the phrase
        'Session token expired' and run_copilot() incorrectly fired a refresh.
        """
        from agent_manager import (
            _COPILOT_EXPIRY_TAIL_WINDOW,
            _COPILOT_TOKEN_EXPIRED_PHRASE,
        )

        # Construct output where the phrase is clearly in the body, not the tail.
        # Pad with enough text so the phrase is far outside the tail window.
        body_prefix = (
            "I investigated the auth subsystem. The root cause was that "
            f"'{_COPILOT_TOKEN_EXPIRED_PHRASE}' errors were not handled "
            "gracefully by the connector. I have now applied a fix: the "
            "connector catches this specific error and re-authenticates before "
            "retrying. All 12 unit tests pass. "
        )
        # Append enough trailing content so the phrase is > TAIL_WINDOW chars from end.
        padding = "x" * (_COPILOT_EXPIRY_TAIL_WINDOW + 10)
        transcript = body_prefix + padding

        call_count = [0]

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            call_count[0] += 1
            return transcript

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr,
                "_parse_mode_command",
                return_value=("Investigate auth", "restricted"),
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr,
                "build_agent_context_prompt",
                return_value="[ctx] Investigate auth",
            ),
        ):
            result = self.mgr.run_copilot(**self._make_run_copilot_args())

        # Must complete in exactly one subprocess call — no refresh triggered
        self.assertEqual(
            call_count[0],
            1,
            f"Expected exactly 1 subprocess call (no refresh), got {call_count[0]}. "
            "Phrase in transcript body should not trigger token-expiry detection.",
        )
        # Full transcript must be returned untruncated
        self.assertIn(body_prefix[:40], result)

    # ── Test 12: _is_copilot_token_expired unit test ─────────────────────────

    def test_is_copilot_token_expired_helper(self):
        """Direct unit test for the _is_copilot_token_expired helper function."""
        from agent_manager import (
            _COPILOT_EXPIRY_TAIL_WINDOW,
            _COPILOT_TOKEN_EXPIRED_PHRASE,
            _is_copilot_token_expired,
        )

        # Real failure: phrase at the tail of output
        real_failure = (
            f"Some work done.\n{_COPILOT_TOKEN_EXPIRED_PHRASE}. Please resend"
            " your message. (Request ID: req-test-005)"
        )
        self.assertTrue(
            _is_copilot_token_expired(real_failure),
            "Should detect phrase in tail (real Copilot auth-expiry shape)",
        )

        # Phrase only in the body (far from tail)
        body_text = f"The error '{_COPILOT_TOKEN_EXPIRED_PHRASE}' was logged. "
        padding = "z" * (_COPILOT_EXPIRY_TAIL_WINDOW + 50)
        transcript_with_phrase_in_body = body_text + padding
        self.assertFalse(
            _is_copilot_token_expired(transcript_with_phrase_in_body),
            "Should NOT detect phrase buried in body text (false positive)",
        )

        # Empty output
        self.assertFalse(
            _is_copilot_token_expired(""), "Empty output must return False"
        )

        # Output shorter than window but containing the phrase
        short_output = (
            f"Step 1 done.\n{_COPILOT_TOKEN_EXPIRED_PHRASE}. Please resend"
            " your message. (Request ID: req-test-006)"
        )
        self.assertTrue(
            _is_copilot_token_expired(short_output),
            "Short output with full error format should be detected",
        )

    # ── Test 13: Bare phrase at tail DOES NOT trigger refresh (false positive) ──

    def test_bare_phrase_at_tail_does_not_trigger_refresh(self):
        """Output ending with the bare 'Session token expired' phrase must NOT
        trigger a session refresh.

        Regression for Issue #203 Round 5 QA blocker 1: the tail-window check
        fired on any output whose last 500 chars contained the phrase, even when
        the output was a successful transcript that happened to end with a mention
        of the bare phrase (e.g. an agent writing a summary like "root cause was
        Session token expired"). Only the full Copilot error format with
        "Please resend your message. (Request ID: ...)" is a real auth failure.
        """
        from agent_manager import _COPILOT_TOKEN_EXPIRED_PHRASE

        # Normal successful transcript that ends with the bare phrase only —
        # no "Please resend your message. (Request ID: ...)" suffix.
        transcript = "Analysis complete. Root cause: " + _COPILOT_TOKEN_EXPIRED_PHRASE

        call_count = [0]

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            call_count[0] += 1
            return transcript

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr,
                "_parse_mode_command",
                return_value=("Analyse root cause", "restricted"),
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr, "build_agent_context_prompt", return_value="[ctx] Analyse"
            ),
        ):
            result = self.mgr.run_copilot(**self._make_run_copilot_args())

        # Must complete in exactly ONE subprocess call — bare phrase is not a failure
        self.assertEqual(
            call_count[0],
            1,
            f"Expected 1 call (bare phrase at tail is NOT a real expiry), "
            f"got {call_count[0]}. False positive regression detected.",
        )
        # Full transcript returned untouched
        self.assertIn("Root cause", result)

    # ── Test 14: Real expiry after earlier valid mention — rfind strips correctly ──

    def test_real_expiry_after_earlier_mention_strips_from_last_occurrence(self):
        """When a transcript mentions the phrase earlier and a real expiry follows,
        context stripping must cut only at the LAST occurrence.

        Regression for Issue #203 Round 5 QA blocker 2: _prior_raw.find() (first
        occurrence) was used to locate the expiry marker in the accumulated context.
        When an earlier valid mention existed, find() cut there instead — discarding
        all intermediate valid work between the mention and the real failure.

        Correct behaviour (rfind): only strip from the LAST occurrence so that
        valid work after an earlier mention is preserved in the continuation prompt.
        """
        from agent_manager import _COPILOT_TOKEN_EXPIRED_PHRASE

        full_expiry = (
            _COPILOT_TOKEN_EXPIRED_PHRASE
            + ". Please resend your message. (Request ID: req-test-014)"
        )

        # First segment: contains a valid earlier mention AND then real expiry at end.
        # valid_middle_work is the content that MUST be preserved in context.
        valid_middle_work = "Deployed service-B successfully. Config updated."
        first_segment = (
            f"Investigated prior failure: '{_COPILOT_TOKEN_EXPIRED_PHRASE}'"
            f" seen last week. {valid_middle_work}\n{full_expiry}"
        )
        # Second segment: resumes normally.
        second_segment = "Continuation complete."

        call_index = [0]
        captured_continuation_prompts = []

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            out = [first_segment, second_segment][call_index[0]]
            call_index[0] += 1
            return out

        def fake_build_context(
            agent, user_prompt, sess_id, render, timeout, runtime, model, channel
        ):
            captured_continuation_prompts.append(user_prompt)
            return f"[ctx] {user_prompt}"

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr,
                "_parse_mode_command",
                return_value=("Deploy and analyse", "restricted"),
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr, "build_agent_context_prompt", side_effect=fake_build_context
            ),
        ):
            self.mgr.run_copilot(
                **self._make_run_copilot_args(prompt="Deploy and analyse")
            )

        # Must have dispatched exactly 2 segments
        self.assertEqual(call_index[0], 2, "Expected initial + 1 refresh")
        # The continuation prompt (second build_agent_context_prompt call) must
        # include the valid work that appeared AFTER the earlier phrase mention.
        self.assertGreaterEqual(len(captured_continuation_prompts), 2)
        continuation = captured_continuation_prompts[1]
        self.assertIn(
            valid_middle_work[:30],
            continuation,
            "valid_middle_work should be in continuation context — rfind strips "
            "only from last occurrence, preserving intermediate valid output.",
        )

    # ── Test 16: Final successful output with bare phrase in prose not stripped

    def test_final_output_with_bare_phrase_in_prose_not_stripped(self):
        """Successful final chunk containing the bare phrase in prose is intact.

        Regression for Issue #203 QA R6 rejection: the final assembly loop
        called rfind(_COPILOT_TOKEN_EXPIRED_PHRASE) on every chunk regardless
        of whether _is_copilot_token_expired() returned True, truncating valid
        output that merely mentioned the phrase.

        Repro: first segment ends with real expiry; second segment succeeds and
        contains the bare phrase in discussion text:
          'Work complete. Root cause: Session token expired in old logs. Done.'
        Broken: result was 'Work complete. Root cause:'
        Fixed: full transcript returned unchanged.
        """
        from agent_manager import _COPILOT_TOKEN_EXPIRED_PHRASE

        expiry_segment = (
            f"Starting work.\n"
            f"{_COPILOT_TOKEN_EXPIRED_PHRASE}. Please resend your message. "
            f"(Request ID: req-test-r6)"
        )
        success_segment = (
            f"Work complete. Root cause discussion: "
            f"{_COPILOT_TOKEN_EXPIRED_PHRASE} in old logs. Final success."
        )
        call_outputs = [expiry_segment, success_segment]
        call_index = [0]

        def fake_exec(cmd, cwd, timeout, runtime, agent, prompt, session_id):
            out = call_outputs[call_index[0]]
            call_index[0] += 1
            return out

        with (
            patch.object(
                self.mgr, "_execute_subprocess_with_tracking", side_effect=fake_exec
            ),
            patch.object(
                self.mgr,
                "_parse_mode_command",
                return_value=("Do work", "restricted"),
            ),
            patch.object(
                self.mgr, "_resolve_permission_mode", return_value="restricted"
            ),
            patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"channel": "webui"},
            ),
            patch.object(
                self.mgr,
                "build_agent_context_prompt",
                return_value="[ctx] Do work",
            ),
        ):
            result = self.mgr.run_copilot(**self._make_run_copilot_args())

        # Two calls: expiry + recovery
        self.assertEqual(call_index[0], 2)
        # Final success text must be present — bare phrase in prose must not strip
        self.assertIn(
            "Final success",
            result,
            "Successful segment was truncated. The cleanup must only strip when "
            "_is_copilot_token_expired() is True.",
        )
        self.assertIn(
            "Root cause discussion",
            result,
            "Text before the bare phrase was lost — unconditional rfind() stripping.",
        )


if __name__ == "__main__":
    unittest.main()
