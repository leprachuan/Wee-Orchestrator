import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_manager import SessionManager
from session_manager_components import (
    CliCommandHandler,
    RuntimeExecutionRequest,
    StreamingManager,
)


class TestIssue29SessionManagerRefactor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.config_file = self.temp_path / "agents.json"
        self.config_file.write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "name": "test_devops",
                            "description": "Test DevOps",
                            "path": "/tmp/test-devops",
                        }
                    ]
                }
            )
        )

        self.home_patch = patch("agent_manager.Path.home", return_value=self.temp_path)
        self.home_patch.start()
        self.manager = SessionManager(str(self.config_file))

    def tearDown(self):
        self.home_patch.stop()
        self.temp_dir.cleanup()

    def test_session_manager_uses_extracted_collaborators(self):
        self.assertIsInstance(self.manager.cli_commands, CliCommandHandler)
        self.assertIsInstance(self.manager.streaming, StreamingManager)
        self.assertIs(self.manager._slash_command_registry, self.manager.cli_commands.registry)
        self.assertIs(self.manager._stream_queues, self.manager.streaming.stream_queues)
        self.assertIs(self.manager._stream_buffers, self.manager.streaming.stream_buffers)

    def test_parse_slash_command_delegates_to_cli_handler(self):
        cmd, arg = self.manager.parse_slash_command("/model set gpt-5.4")
        self.assertEqual(cmd, "/model")
        self.assertEqual(arg, "set gpt-5.4")
        self.assertEqual(
            self.manager.get_slash_commands()["/background"],
            "Manage background tasks",
        )

    def test_runtime_executor_registry_dispatches_mode_aware_executor(self):
        with patch.object(
            self.manager,
            "run_claude_sdk",
            return_value="ok",
        ) as run_claude_sdk:
            request = RuntimeExecutionRequest(
                runtime="claude-sdk",
                prompt="inspect",
                model="sonnet",
                agent="orchestrator",
                session_id="sid-1",
                can_resume=True,
                n8n_session_id="n8n-1",
                effective_timeout=30,
                render_type="text",
                mode="elevated",
            )
            result = self.manager.runtime_executors.execute(request)

        self.assertEqual(result, "ok")
        run_claude_sdk.assert_called_once_with(
            "inspect",
            "sonnet",
            "orchestrator",
            "sid-1",
            True,
            "n8n-1",
            30,
            "text",
            "elevated",
        )

    def test_stream_registration_delegates_to_streaming_manager(self):
        queue = object()
        loop = object()

        self.manager._register_stream("stream-1", queue, loop)
        self.assertIn("stream-1", self.manager.streaming.stream_queues)
        self.assertIn("stream-1", self.manager.streaming.stream_buffers)

        self.manager._unregister_stream("stream-1", queue=queue)
        self.assertNotIn("stream-1", self.manager.streaming.stream_queues)

        self.manager._cleanup_stream_buffer("stream-1")
        self.assertNotIn("stream-1", self.manager.streaming.stream_buffers)
