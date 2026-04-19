"""Regression tests for Issue #29: SessionManager refactoring.

Verifies that CliCommandHandler, RuntimeExecutor, and StreamingManager
are properly integrated into SessionManager and behave correctly.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_29")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")

AGENTS_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "agents.json",
)


class TestCliCommandHandler(unittest.TestCase):
    """Unit tests for CliCommandHandler in isolation."""

    @classmethod
    def setUpClass(cls):
        from session_manager_components import CliCommandHandler

        cls.CliCommandHandler = CliCommandHandler

    def setUp(self):
        self.handler = self.CliCommandHandler()

    def test_register_and_has_command(self):
        self.handler.register("/test", lambda a, s, n: "ok", "Test cmd")
        self.assertTrue(self.handler.has_command("/test"))

    def test_unknown_command_returns_false(self):
        self.assertFalse(self.handler.has_command("/unknown"))

    def test_dispatch_calls_handler(self):
        results = []
        self.handler.register(
            "/probe",
            lambda arg, sd, nsid: results.append((arg, nsid)) or "done",
            "Probe",
        )
        out = self.handler.dispatch("/probe", "arg1", {}, "sess-1")
        self.assertEqual(out, "done")
        self.assertEqual(results, [("arg1", "sess-1")])

    def test_dispatch_unknown_returns_none(self):
        result = self.handler.dispatch("/nope", None, {}, "sess-1")
        self.assertIsNone(result)

    def test_list_commands_returns_descriptions(self):
        self.handler.register("/a", lambda *a: None, "Desc A")
        self.handler.register("/b", lambda *a: None, "Desc B")
        cmds = self.handler.list_commands()
        self.assertEqual(cmds["/a"], "Desc A")
        self.assertEqual(cmds["/b"], "Desc B")

    def test_len(self):
        self.assertEqual(len(self.handler), 0)
        self.handler.register("/x", lambda *a: None, "X")
        self.assertEqual(len(self.handler), 1)


class TestRuntimeExecutor(unittest.TestCase):
    """Unit tests for RuntimeExecutor in isolation."""

    @classmethod
    def setUpClass(cls):
        from session_manager_components import RuntimeExecutor

        cls.RuntimeExecutor = RuntimeExecutor

    def setUp(self):
        self.executor = self.RuntimeExecutor()

    def test_register_and_get(self):
        fn = lambda *a: "result"  # noqa: E731
        self.executor.register("copilot", fn)
        self.assertIs(self.executor.get("copilot"), fn)

    def test_get_unknown_returns_none(self):
        self.assertIsNone(self.executor.get("unknown"))

    def test_is_registered(self):
        self.assertFalse(self.executor.is_registered("wee"))
        self.executor.register("wee", lambda *a: None)
        self.assertTrue(self.executor.is_registered("wee"))

    def test_supported_runtimes(self):
        self.executor.register("a", lambda *a: None)
        self.executor.register("b", lambda *a: None)
        self.assertIn("a", self.executor.supported_runtimes())
        self.assertIn("b", self.executor.supported_runtimes())

    def test_len(self):
        self.assertEqual(len(self.executor), 0)
        self.executor.register("r", lambda *a: None)
        self.assertEqual(len(self.executor), 1)


class TestStreamBuffer(unittest.TestCase):
    """Unit tests for StreamBuffer in isolation."""

    @classmethod
    def setUpClass(cls):
        from session_manager_components import StreamBuffer

        cls.StreamBuffer = StreamBuffer

    def setUp(self):
        self.buf = self.StreamBuffer()

    def test_push_appends_chunks(self):
        self.buf.push("chunk", "hello")
        self.buf.push("chunk", "world")
        self.assertEqual(len(self.buf.chunks), 2)

    def test_push_done_sets_finished(self):
        self.assertFalse(self.buf.finished)
        self.buf.push("done", "result")
        self.assertTrue(self.buf.finished)
        self.assertEqual(self.buf.done_result, "result")

    def test_add_consumer_returns_replay_index(self):
        self.buf.push("chunk", "pre")
        import queue as _queue

        q = _queue.Queue()

        class FakeLoop:
            def call_soon_threadsafe(self, fn, *args):
                fn(*args)

        loop = FakeLoop()
        idx = self.buf.add_consumer(q, loop)
        self.assertEqual(idx, 1)
        self.assertTrue(self.buf.has_consumers())

    def test_remove_consumer(self):
        import queue as _queue

        q = _queue.Queue()

        class FakeLoop:
            def call_soon_threadsafe(self, fn, *args):
                fn(*args)

        self.buf.add_consumer(q, FakeLoop())
        self.assertTrue(self.buf.has_consumers())
        self.buf.remove_consumer(q)
        self.assertFalse(self.buf.has_consumers())

    def test_get_replay_chunks(self):
        self.buf.push("chunk", "a")
        self.buf.push("chunk", "b")
        chunks = self.buf.get_replay_chunks(1)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], ("chunk", "a"))

    def test_thread_safe_push(self):
        errors = []
        threads = []

        def _pusher(n):
            try:
                for i in range(50):
                    self.buf.push("chunk", f"{n}-{i}")
            except Exception as e:
                errors.append(e)

        for i in range(4):
            t = threading.Thread(target=_pusher, args=(i,))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(self.buf.chunks), 200)


class TestStreamingManager(unittest.TestCase):
    """Unit tests for StreamingManager in isolation."""

    @classmethod
    def setUpClass(cls):
        from session_manager_components import StreamingManager

        cls.StreamingManager = StreamingManager

    def setUp(self):
        self.mgr = self.StreamingManager()

    def test_get_or_create_buffer_creates_once(self):
        buf1 = self.mgr.get_or_create_buffer("sess-a")
        buf2 = self.mgr.get_or_create_buffer("sess-a")
        self.assertIs(buf1, buf2)

    def test_get_buffer_none_for_unknown(self):
        self.assertIsNone(self.mgr.get_buffer("no-such-session"))

    def test_get_queue_none_for_unknown(self):
        self.assertIsNone(self.mgr.get_queue("no-such-session"))

    def test_cleanup_buffer_removes_entry(self):
        self.mgr.get_or_create_buffer("sess-b")
        self.assertIsNotNone(self.mgr.get_buffer("sess-b"))
        self.mgr.cleanup_buffer("sess-b")
        self.assertIsNone(self.mgr.get_buffer("sess-b"))

    def test_cleanup_stale_removes_finished_old_buffers(self):
        buf = self.mgr.get_or_create_buffer("sess-c")
        buf.push("done", "result")
        buf.created_at = time.time() - 700
        self.mgr.cleanup_stale_buffers(max_age=600.0)
        self.assertIsNone(self.mgr.get_buffer("sess-c"))

    def test_cleanup_stale_keeps_active_buffers(self):
        buf = self.mgr.get_or_create_buffer("sess-d")
        buf.push("chunk", "data")
        buf.created_at = time.time() - 700
        self.mgr.cleanup_stale_buffers(max_age=600.0)
        self.assertIsNotNone(self.mgr.get_buffer("sess-d"))


class TestSessionManagerIntegration(unittest.TestCase):
    """Integration tests: verify SessionManager delegates to new components."""

    @classmethod
    def setUpClass(cls):
        import agent_manager
        from session_manager_components import (
            CliCommandHandler,
            RuntimeExecutor,
            StreamingManager,
        )

        cls.sm = agent_manager.SessionManager(
            config_file=AGENTS_JSON,
            app_env="DEV",
        )
        cls.CliCommandHandler = CliCommandHandler
        cls.RuntimeExecutor = RuntimeExecutor
        cls.StreamingManager = StreamingManager

    def test_cli_handler_attribute_exists(self):
        self.assertIsInstance(self.sm.cli_handler, self.CliCommandHandler)

    def test_runtime_executor_attribute_exists(self):
        self.assertIsInstance(self.sm.runtime_executor, self.RuntimeExecutor)

    def test_streaming_manager_attribute_exists(self):
        self.assertIsInstance(self.sm.streaming_manager, self.StreamingManager)

    def test_slash_registry_is_cli_handler_registry(self):
        """_slash_command_registry must alias cli_handler._registry."""
        self.assertIs(
            self.sm._slash_command_registry,
            self.sm.cli_handler._registry,
        )

    def test_stream_buffers_is_streaming_manager_buffers(self):
        """_stream_buffers must alias streaming_manager._buffers."""
        self.assertIs(
            self.sm._stream_buffers,
            self.sm.streaming_manager._buffers,
        )

    def test_stream_queues_is_streaming_manager_queues(self):
        """_stream_queues must alias streaming_manager._queues."""
        self.assertIs(
            self.sm._stream_queues,
            self.sm.streaming_manager._queues,
        )

    def test_all_standard_slash_commands_registered(self):
        commands = self.sm.cli_handler.list_commands()
        for cmd in ["/help", "/status", "/cancel", "/runtime", "/session", "/update"]:
            self.assertIn(cmd, commands, f"Missing slash command: {cmd}")

    def test_get_slash_commands_delegates_to_cli_handler(self):
        direct = self.sm.cli_handler.list_commands()
        via_method = self.sm.get_slash_commands()
        self.assertEqual(direct, via_method)

    def test_runtime_executor_has_all_runtimes(self):
        expected = {
            "copilot",
            "copilot-sdk",
            "opencode",
            "claude",
            "claude-sdk",
            "gemini",
            "codex",
            "devin",
            "cursor",
            "wee",
        }
        registered = set(self.sm.runtime_executor.supported_runtimes())
        self.assertEqual(expected, registered)

    def test_register_stream_delegates_to_streaming_manager(self):
        import queue as _queue

        q = _queue.Queue()

        class FakeLoop:
            def call_soon_threadsafe(self, fn, *args):
                fn(*args)

        sid = "test-sess-29-register"
        self.sm._register_stream(sid, q, FakeLoop())
        buf = self.sm.streaming_manager.get_buffer(sid)
        self.assertIsNotNone(buf)
        self.sm._unregister_stream(sid, q)
        self.sm._cleanup_stream_buffer(sid)

    def test_get_or_create_stream_buffer_delegates(self):
        sid = "test-sess-29-getbuf"
        buf = self.sm._get_or_create_stream_buffer(sid)
        self.assertIsNotNone(buf)
        self.assertIs(buf, self.sm.streaming_manager.get_buffer(sid))
        self.sm._cleanup_stream_buffer(sid)

    def test_cleanup_stale_stream_buffers_delegates(self):
        sid = "test-sess-29-stale"
        buf = self.sm.streaming_manager.get_or_create_buffer(sid)
        buf.push("done", "x")
        buf.created_at = time.time() - 700
        self.sm._cleanup_stale_stream_buffers(max_age=600.0)
        self.assertIsNone(self.sm.streaming_manager.get_buffer(sid))


if __name__ == "__main__":
    unittest.main()
