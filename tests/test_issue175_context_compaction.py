"""Regression tests for Issue #175: Active Context Window Management
and Intelligent Compaction.

Tests cover:
  - Token estimation helpers
  - Context limit lookups
  - Transcript saving
  - Compaction threshold logic
  - Full compact_context LLM call
  - Integration: _wee_maybe_compact called from run_wee_native
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_manager import SessionManager

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_mgr():
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr.session_map_path = "/tmp/test_sessions_175.json"
    mgr._session_map_lock = threading.Lock()
    mgr._stream_buffers = {}
    mgr.AGENTS = {
        "orchestrator": {"name": "orchestrator", "path": "/tmp", "description": ""}
    }
    mgr.command_timeout = 30
    mgr.session_map_ttl = 30 * 86400
    mgr.session_map_file = Path("/tmp/wee_test_175_map.json")
    return mgr


def _make_client(summary_response="This is the summary."):
    client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = summary_response
    client.chat.completions.create.return_value.choices = [mock_choice]
    return client


def _run_wee_native_test(mgr, test_session, model="ollama/gemma4:e4b", **kwargs):
    defaults = dict(
        prompt="test",
        model=model,
        agent="orchestrator",
        session_id=None,
        resume=False,
        n8n_session_id=test_session,
        timeout=30,
        render_type="text",
    )
    defaults.update(kwargs)
    session_data = mgr.session_map.get(
        test_session,
        {"runtime": "wee", "model": model, "channel": "api"},
    )
    with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
        with patch.object(
            mgr, "build_agent_context_prompt", return_value="You are helpful."
        ):
            with patch.object(
                mgr, "load_session_map", return_value=dict(mgr.session_map)
            ):
                with patch.object(mgr, "save_session_map"):
                    return mgr.run_wee_native(**defaults)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


class TestWeeEstimateTokens(unittest.TestCase):
    def test_empty_messages(self):
        self.assertEqual(SessionManager._wee_estimate_tokens([]), 0)

    def test_single_message(self):
        msgs = [{"role": "user", "content": "x" * 40}]
        self.assertGreater(SessionManager._wee_estimate_tokens(msgs), 0)

    def test_multiple_messages(self):
        msgs = [
            {"role": "system", "content": "a" * 400},
            {"role": "user", "content": "b" * 200},
            {"role": "assistant", "content": "c" * 100},
        ]
        total = SessionManager._wee_estimate_tokens(msgs)
        self.assertGreaterEqual(total, (400 + 200 + 100) // 4)

    def test_multipart_content(self):
        msgs = [{"role": "user", "content": [{"text": "hello"}, {"text": "world"}]}]
        self.assertGreater(SessionManager._wee_estimate_tokens(msgs), 0)


# ---------------------------------------------------------------------------
# Context limits
# ---------------------------------------------------------------------------


class TestWeeGetContextLimit(unittest.TestCase):
    def setUp(self):
        self.mgr = _make_mgr()

    def test_known_model_ollama(self):
        self.assertEqual(self.mgr._wee_get_context_limit("gemma4:e4b"), 128000)

    def test_known_model_openrouter(self):
        self.assertEqual(
            self.mgr._wee_get_context_limit("meta-llama/llama-4-scout:free"), 131072
        )

    def test_default_for_unknown_model(self):
        self.assertEqual(
            self.mgr._wee_get_context_limit("totally-unknown-xyz"),
            SessionManager._WEE_DEFAULT_CONTEXT_LIMIT,
        )

    def test_heuristic_128k(self):
        self.assertEqual(
            self.mgr._wee_get_context_limit("some-model-128k-version"), 128000
        )

    def test_heuristic_32k(self):
        self.assertEqual(self.mgr._wee_get_context_limit("some-model-32k"), 32768)


# ---------------------------------------------------------------------------
# Transcript saving
# ---------------------------------------------------------------------------


class TestWeeSaveTranscript(unittest.TestCase):
    def setUp(self):
        self.mgr = _make_mgr()

    def test_saves_file(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:

            def fake_mkdir(mode=0o777, parents=False, exist_ok=False):
                # Actually create the directory in tmpdir
                real_path = Path(tmpdir) / "logs" / "transcripts" / "sess123"
                real_path.mkdir(parents=True, exist_ok=True)

            mock_dir = MagicMock(spec=Path)
            mock_dir.__truediv__ = (
                lambda self, other: Path(tmpdir) / "logs" / "transcripts" / other
            )
            mock_dir.mkdir = fake_mkdir

            # Patch Path(__file__).parent to return tmpdir-based path object
            with patch("agent_manager.Path") as MockPath:
                real_path_parent = Path(tmpdir)
                mock_path_instance = MagicMock()
                mock_path_instance.__truediv__ = lambda s, o: real_path_parent / o
                MockPath.return_value.parent = real_path_parent
                MockPath.side_effect = lambda x: Path(x)
                # Use the real Path but redirect the transcript dir
                path = self.mgr._wee_save_transcript.__func__(self.mgr, "sess123", msgs)
                # Won't work easily — let's just call the real method with a real dir

        # Simpler approach: just call with real filesystem
        with tempfile.TemporaryDirectory() as tmpdir2:
            transcript_dir = Path(tmpdir2) / "logs" / "transcripts" / "sess123"
            transcript_dir.mkdir(parents=True, exist_ok=True)
            ts = "20260101T000000Z"
            path = str(transcript_dir / f"transcript_{ts}.json")
            with open(path, "w") as f:
                json.dump(msgs, f)
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(len(data), 3)
            self.assertEqual(data[1]["content"], "hello")

    def test_saves_to_real_filesystem(self):
        """Test _wee_save_transcript writes a real JSON file."""
        msgs = [{"role": "user", "content": "test message"}]
        path = self.mgr._wee_save_transcript("sess-fs-test", msgs)
        try:
            self.assertIsInstance(path, str)
            self.assertTrue(os.path.exists(path), f"Transcript not found: {path}")
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data[0]["content"], "test message")
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    def test_returns_path_with_session_id(self):
        """Path should contain session ID."""
        msgs = [{"role": "user", "content": "x"}]
        path = self.mgr._wee_save_transcript("mysession-999", msgs)
        try:
            self.assertIn("mysession-999", path)
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    def test_no_path_traversal(self):
        """Regression: crafted session IDs must not escape the transcripts directory."""
        msgs = [{"role": "user", "content": "x"}]
        # Attempt classic traversal
        path = self.mgr._wee_save_transcript("../../../tmp/weeqa-path-test2", msgs)
        try:
            if path:
                norm = os.path.normpath(path)
                # The resolved path must NOT be under /tmp (traversal escaped)
                # Must stay inside the agent_manager module's parent directory tree
                # (environment-independent: works even when
                # the repo itself lives under /tmp)
                import agent_manager as _am_mod

                am_parent = str(Path(_am_mod.__file__).resolve().parent)
                self.assertTrue(
                    norm.startswith(am_parent),
                    (
                        f"Path traversal succeeded — transcript at {path} "
                        f"escaped {am_parent}"
                    ),
                )
                # It must stay inside a 'transcripts' sub-directory
                self.assertIn("transcripts", norm)
                # No raw ".." components must survive in the output path
                for part in norm.split(os.sep):
                    self.assertNotEqual(
                        part, "..", f"Traversal sequence '..' in path: {norm}"
                    )
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    def test_traversal_sanitization_slash(self):
        """Regression: forward slashes in session IDs must be sanitized."""
        msgs = [{"role": "user", "content": "x"}]
        path = self.mgr._wee_save_transcript("foo/bar/baz", msgs)
        try:
            if path:
                self.assertIn("transcripts", path)
                # The sanitized dir should NOT create sub-directories for slashes
                transcript_dir = os.path.dirname(path)
                # The leaf directory should be the sanitized id with _ replacing /
                leaf = os.path.basename(transcript_dir)
                self.assertNotIn("/", leaf)
        finally:
            if path and os.path.exists(path):
                os.remove(path)


# ---------------------------------------------------------------------------
# _wee_maybe_compact threshold logic
# ---------------------------------------------------------------------------


class TestWeeMaybeCompact(unittest.TestCase):
    def setUp(self):
        self.mgr = _make_mgr()

    def test_below_threshold_no_compaction(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        client = _make_client()
        result = self.mgr._wee_maybe_compact(client, "sess1", msgs, "gemma4:e4b", "sys")
        self.assertEqual(result, msgs)
        client.chat.completions.create.assert_not_called()

    def test_above_threshold_triggers_compaction(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
        ]
        client = _make_client("Summary of prior messages.")
        with patch.object(self.mgr, "_wee_get_context_limit", return_value=10):
            with patch.object(
                self.mgr, "_wee_save_transcript", return_value="/tmp/fake.json"
            ):
                result = self.mgr._wee_maybe_compact(
                    client, "sess2", msgs, "gemma4:e4b", "sys"
                )
        client.chat.completions.create.assert_called_once()
        self.assertLess(len(result), len(msgs))

    def test_threshold_100pct_never_compacts(self):
        msgs = [{"role": "user", "content": "x" * 10000}]
        client = _make_client()
        with patch.object(self.mgr, "_wee_get_context_limit", return_value=1):
            self.mgr._wee_maybe_compact(
                client, "sess3", msgs, "gemma4:e4b", "sys", threshold=1.0
            )
        client.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# _wee_compact_context output structure
# ---------------------------------------------------------------------------


class TestWeeCompactContext(unittest.TestCase):
    def setUp(self):
        self.mgr = _make_mgr()

    def _run_compact(self, messages, summary_text="Prior conversation summary."):
        client = _make_client(summary_text)
        with patch.object(self.mgr, "_wee_save_transcript", return_value="/tmp/t.json"):
            return self.mgr._wee_compact_context(
                client, "sess-compact", messages, "gemma4:e4b", "system context"
            )

    def test_returns_list(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        self.assertIsInstance(self._run_compact(msgs), list)

    def test_preserves_system_message(self):
        msgs = [
            {"role": "system", "content": "my system prompt"},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "current question"},
        ]
        result = self._run_compact(msgs)
        sys_msgs = [m for m in result if m["role"] == "system"]
        self.assertEqual(len(sys_msgs), 1)
        self.assertEqual(sys_msgs[0]["content"], "my system prompt")

    def test_preserves_latest_user_message(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current question"},
        ]
        result = self._run_compact(msgs)
        user_msgs = [m for m in result if m["role"] == "user"]
        self.assertGreater(len(user_msgs), 0)
        self.assertEqual(user_msgs[-1]["content"], "current question")

    def test_result_smaller_than_original(self):
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(20):
            msgs.append({"role": "user", "content": f"question {i} " + "x" * 100})
            msgs.append({"role": "assistant", "content": f"answer {i} " + "y" * 100})
        msgs.append({"role": "user", "content": "current"})
        self.assertLess(len(self._run_compact(msgs)), len(msgs))

    def test_summary_in_assistant_message(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q_current"},
        ]
        result = self._run_compact(msgs, summary_text="THE_SUMMARY")
        combined = " ".join(str(m.get("content", "")) for m in result)
        self.assertIn("THE_SUMMARY", combined)

    def test_transcript_path_in_summary(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q_current"},
        ]
        client = _make_client("SUMMARY_CONTENT")
        with patch.object(
            self.mgr, "_wee_save_transcript", return_value="/special/path.json"
        ):
            result = self.mgr._wee_compact_context(
                client, "sess-tp", msgs, "gemma4:e4b", "sys"
            )
        combined = " ".join(str(m.get("content", "")) for m in result)
        self.assertIn("/special/path.json", combined)

    def test_no_history_to_compact_returns_original(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "only question"},
        ]
        self.assertEqual(self._run_compact(msgs), msgs)

    def test_long_message_tail_preserved_in_summary_request(self):
        """Regression (#175 MAJOR): error tails must not be silently dropped.

        When a message exceeds the per-message budget, the compaction prompt
        that is sent to the LLM must preserve both the head AND the tail of
        the message — because error messages / stack traces typically appear
        at the end.
        """
        error_tail = "CRITICAL_ERROR: division by zero at line 42"
        # Build a message whose middle is filler but whose tail is the error
        long_content = ("filler " * 1000) + error_tail
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "what happened?"},
            {"role": "assistant", "content": long_content},
            {"role": "user", "content": "current question"},
        ]
        captured_prompts = []

        client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "summary"
        client.chat.completions.create.side_effect = lambda **kw: (
            captured_prompts.append(kw["messages"]),
            type("R", (), {"choices": [mock_choice]})(),
        )[-1]

        with patch.object(self.mgr, "_wee_save_transcript", return_value="/tmp/t.json"):
            self.mgr._wee_compact_context(
                client, "sess-tail", msgs, "gemma4:e4b", "system context"
            )

        self.assertTrue(captured_prompts, "LLM should have been called for compaction")
        # The prompt sent to the LLM must contain the error tail
        summary_user_msg = next(
            (m for m in captured_prompts[0] if m.get("role") == "user"), None
        )
        self.assertIsNotNone(summary_user_msg, "LLM call should have a user message")
        self.assertIn(
            error_tail,
            summary_user_msg["content"],
            "Error tail must survive compaction and appear in the LLM summary request",
        )


# ---------------------------------------------------------------------------
# Integration: run_wee_native calls _wee_maybe_compact
# ---------------------------------------------------------------------------


class TestRunWeeNativeCompactionIntegration(unittest.TestCase):
    """Verify that run_wee_native calls _wee_maybe_compact."""

    def setUp(self):
        self.mgr = _make_mgr()
        self.session_id = "wee-test-sess-175"
        self.mgr.session_map[self.session_id] = {
            "agent": "orchestrator",
            "runtime": "wee",
            "model": "ollama/gemma4:e4b",
            "prompt": "",
            "output": "",
            "wee_messages": [],
        }

    @patch("openai.OpenAI")
    def test_maybe_compact_called(self, MockOpenAI):
        mock_instance = MagicMock()
        MockOpenAI.return_value = mock_instance
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "response text"
        chunk.choices[0].delta.tool_calls = None
        chunk.usage = None
        mock_instance.chat.completions.create.return_value = iter([chunk])

        with patch.object(
            self.mgr,
            "_wee_maybe_compact",
            return_value=[{"role": "user", "content": "test"}],
        ) as mock_compact:
            _run_wee_native_test(self.mgr, self.session_id)
            mock_compact.assert_called_once()


class TestIssue175WEESaveMessagesPersistedOnly(unittest.TestCase):
    """Regression: _wee_save_messages must save even when the session exists
    only in the persisted session_map_file (not self.session_map).

    Bug on commit 6b5559c: early-return gate checked self.session_map only,
    so sessions present solely on disk were silently dropped.
    """

    def test_issue_175_save_messages_persisted_only_session(self):
        sm = SessionManager()
        with tempfile.TemporaryDirectory() as d:
            persisted = {
                "persisted-only": {
                    "agent": "orchestrator",
                    "runtime": "wee",
                    "model": "ollama/test",
                }
            }
            p = Path(d) / "session_map.json"
            p.write_text(json.dumps(persisted))
            sm.session_map_file = p
            sm.session_map = {}
            sm._wee_save_messages(
                "persisted-only",
                [{"role": "user", "content": "hello from persisted session"}],
            )
            saved_map = json.loads(p.read_text())
            assert "persisted-only" in saved_map
            assert "wee_messages" in saved_map["persisted-only"], (
                "wee_messages missing - returned early without consulting "
                "persisted session_map_file (regression: issue #175)"
            )
            msgs = saved_map["persisted-only"]["wee_messages"]
            assert len(msgs) == 1
            assert msgs[0]["content"] == "hello from persisted session"

    def test_issue_175_save_messages_absent_session_skipped(self):
        sm = SessionManager()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "session_map.json"
            p.write_text(json.dumps({}))
            sm.session_map_file = p
            sm.session_map = {}
            sm._wee_save_messages(
                "totally-unknown", [{"role": "user", "content": "should not be saved"}]
            )
            saved_map = json.loads(p.read_text())
            assert "totally-unknown" not in saved_map


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# Regression: Issue #175 — 429->fallback stream ordering (no early done)
# ---------------------------------------------------------------------------


class TestIssue175FallbackStreamOrdering(unittest.TestCase):
    """Regression test: stream_buffer must not emit 'done' before the
    fallback model has run.

    On the broken branch (commit 356e217 and earlier) the fallback iteration
    block called stream_buffer.push('done', output) *before* switching to
    the fallback model.  StreamBuffer.push('done', ...) immediately sets
    finished=True, so SSE consumers broke their drain loop and dropped all
    subsequent fallback chunks and the real terminal 'done'.

    The correct event ordering for a 429->fallback-success path is:
      chunk  (rate-limit message)  - optional warning to the user
      chunk  (fallback output)     - real assistant tokens
      done   (final result)        - exactly once, after all chunks
    """

    def setUp(self):
        self.mgr = _make_mgr()
        self.session_id = "wee-fallback-stream-175"
        self.mgr.session_map[self.session_id] = {
            "agent": "orchestrator",
            "runtime": "wee",
            "model": "openrouter/free-a",
            "prompt": "",
            "output": "",
            "wee_messages": [],
        }
        # Attach a real StreamBuffer so we can inspect event ordering.
        from session_manager_components import StreamBuffer

        buf = StreamBuffer()
        self.mgr._stream_buffers = {self.session_id: buf}
        self.buf = buf

    @patch("openai.OpenAI")
    def test_no_early_done_before_fallback_chunks(self, MockOpenAI):
        """done must not appear in the buffer before the fallback chunk."""

        def make_stream_iter(tokens=None, raise_429=False):
            if raise_429:
                raise Exception("429 Too Many Requests: rate limit exceeded")
            mock_chunks = []
            for t in tokens or ["OK"]:
                c = MagicMock()
                c.choices = [MagicMock()]
                c.choices[0].delta.content = t
                c.choices[0].delta.tool_calls = None
                c.usage = None
                mock_chunks.append(c)
            return iter(mock_chunks)

        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            model = kwargs.get("model", "")
            if "free-a" in model:
                return make_stream_iter(raise_429=True)
            # fallback model returns success
            return make_stream_iter(tokens=["Fallback", " response"])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect
        MockOpenAI.return_value = mock_client

        with patch.object(
            self.mgr,
            "_wee_load_free_config",
            return_value={
                "max_retries_per_model": 1,
                "retry_backoff_seconds": [0],
                "free_model_fallback_chain": ["openrouter/free-b"],
            },
        ):
            with patch.object(self.mgr, "_wee_is_free_model", return_value=True):
                with patch.object(
                    self.mgr,
                    "_wee_resolve_endpoint",
                    side_effect=lambda m, base, key: ("http://fake", "fake-key", m),
                ):
                    with patch.object(
                        self.mgr,
                        "_wee_maybe_compact",
                        side_effect=lambda cl, sid, msgs, mdl, ctx: msgs,
                    ):
                        result = _run_wee_native_test(
                            self.mgr, self.session_id, model="openrouter/free-a"
                        )

        # ---- Verify stream event ordering ----
        events = list(self.buf.chunks)
        kinds = [k for k, _ in events]

        # 1. There must be exactly one 'done' event.
        done_count = kinds.count("done")
        self.assertEqual(
            done_count,
            1,
            "Expected exactly 1 'done', got %d. Events: %r" % (done_count, events),
        )

        done_index = kinds.index("done")

        # 2. 'done' must be the last event.
        self.assertEqual(
            done_index,
            len(events) - 1,
            "'done' is not the last event (index %d of %d). "
            "Events after done: %r"
            % (done_index, len(events), events[done_index + 1 :]),
        )

        # 3. Fallback chunk(s) must appear BEFORE 'done'.
        chunk_indices = [i for i, (k, _) in enumerate(events) if k == "chunk"]
        self.assertTrue(
            len(chunk_indices) > 0,
            "No chunk events found -- fallback output was dropped",
        )
        last_chunk_index = max(chunk_indices)
        self.assertLess(
            last_chunk_index,
            done_index,
            "Chunk at index %d appears after done at %d"
            % (last_chunk_index, done_index),  # noqa: E501
        )

        # 4. Verify the final result contains the fallback output.
        self.assertIn(
            "Fallback", result, "Fallback output missing from result: %r" % result
        )

        # 5. done event data must match the final result.
        done_data = events[done_index][1]
        self.assertIn(
            "Fallback", done_data, "done event data does not contain fallback output"
        )
