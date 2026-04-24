"""Tests for Issue #96: Run Now ignores job mode - command-mode jobs
incorrectly dispatched as AI tasks.

Verifies that the Run Now endpoint (/api/v1/scheduler/jobs/{job_id}/run)
correctly routes command-mode jobs to direct shell execution instead of
dispatching them through the LLM pipeline.
"""


import os
import sys

import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TestRunNowCommandMode(unittest.TestCase):
    """Test that Run Now correctly routes command-mode vs AI-mode jobs."""

    def test_run_command_task_function_exists(self):
        """_run_command_task function should be defined in the API scope."""
        # Parse agent_manager.py and verify _run_command_task is defined
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()
        self.assertIn(
            "def _run_command_task(",
            content,
            "_run_command_task function should be defined",
        )

    def test_run_command_task_does_not_use_llm(self):
        """_run_command_task should use subprocess, not agent_manager CLI."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        # Find the _run_command_task function body
        start = content.find("def _run_command_task(")
        self.assertNotEqual(start, -1)

        # Find the next function definition at the same indent level
        # _run_command_task is at 4-space indent
        end = content.find("\n    def ", start + 1)
        func_body = content[start:end]

        # Should use subprocess.run or _sp.run
        self.assertTrue(
            "subprocess" in func_body or "_sp.run" in func_body,
            "_run_command_task should use subprocess for shell execution",
        )

        # Should NOT reference agent_manager.py CLI invocation
        self.assertNotIn(
            "agent_manager.py",
            func_body,
            "_run_command_task should not invoke agent_manager.py CLI",
        )

    def test_run_now_endpoint_branches_on_mode(self):
        """Run Now endpoint should branch command-mode vs AI-mode."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        # Find the run_scheduler_job_now function
        start = content.find("async def run_scheduler_job_now(")
        self.assertNotEqual(start, -1)

        # Find the next endpoint decorator (marks end of this handler)
        end = content.find("\n        @app.", start + 1)
        handler_body = content[start:end]

        # Should check mode == "command"
        self.assertIn(
            'mode == "command"',
            handler_body,
            "Run Now should check for command mode",
        )

        # Should reference _run_command_task for command mode
        self.assertIn(
            "_run_command_task",
            handler_body,
            "Run Now should dispatch to _run_command_task for command mode",
        )

        # Should still reference _run_background_task for AI mode
        self.assertIn(
            "_run_background_task",
            handler_body,
            "Run Now should still use _run_background_task for AI mode",
        )

    def test_command_mode_does_not_wrap_prompt(self):
        """Command mode should NOT wrap task as '[Scheduled command] ...'."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        start = content.find("async def run_scheduler_job_now(")
        end = content.find("\n        @app.", start + 1)
        handler_body = content[start:end]

        self.assertNotIn(
            "[Scheduled command]",
            handler_body,
            "Run Now should not wrap command-mode tasks as LLM prompts",
        )

    def test_command_mode_response_includes_mode(self):
        """Command-mode Run Now response should indicate mode='command'."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        start = content.find("async def run_scheduler_job_now(")
        end = content.find("\n        @app.", start + 1)
        handler_body = content[start:end]

        # The command-mode return block should include mode indicator
        self.assertIn(
            '"mode": "command"',
            handler_body,
            "Command-mode response should indicate mode=command",
        )

    def test_command_mode_task_created_with_shell_runtime(self):
        """Command-mode bg task should be created with shell runtime, not LLM."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        start = content.find("async def run_scheduler_job_now(")
        end = content.find("\n        @app.", start + 1)
        handler_body = content[start:end]

        # In the command branch, runtime should be "shell" not an LLM runtime
        self.assertIn(
            'runtime="shell"',
            handler_body,
            "Command-mode task should use shell runtime",
        )


class TestRunCommandTaskExecution(unittest.TestCase):
    """Test the _run_command_task function behavior directly."""

    def _get_run_command_task(self):
        """Import the _run_command_task function from the API module scope.

        Since it's defined inside create_api_app(), we need to create the app
        first to access it.
        """

        # Create the app which defines _run_command_task in its scope
        # We'll test via the API endpoint instead
        return None

    def test_command_mode_subprocess_run_params(self):
        """Verify _run_command_task uses correct subprocess.run parameters."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        start = content.find("def _run_command_task(")
        end = content.find("\n    def ", start + 1)
        func_body = content[start:end]

        # Should parse argv safely instead of using shell=True
        self.assertIn(
            "_split_command_args(command)",
            func_body,
            "Should parse commands into argv before execution",
        )
        self.assertNotIn("shell=True", func_body, "Should not use shell=True")

        # Should use capture_output=True
        self.assertIn(
            "capture_output=True", func_body, "Should capture output"
        )

        # Should use text=True
        self.assertIn("text=True", func_body, "Should use text mode")

        # Should handle timeouts
        self.assertIn("TimeoutExpired", func_body, "Should handle timeouts")

    def test_command_mode_updates_bg_task_status(self):
        """_run_command_task should update bg task manager on completion."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        start = content.find("def _run_command_task(")
        end = content.find("\n    def ", start + 1)
        func_body = content[start:end]

        # Should call complete_task on success
        self.assertIn(
            "complete_task",
            func_body,
            "Should call complete_task on success",
        )

        # Should call fail_task on failure
        self.assertIn(
            "fail_task",
            func_body,
            "Should call fail_task on failure",
        )

    def test_command_mode_saves_scheduler_results(self):
        """_run_command_task should save results to scheduler for audit."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        start = content.find("def _run_command_task(")
        end = content.find("\n    def ", start + 1)
        func_body = content[start:end]

        # Should save results to scheduler
        self.assertIn(
            "_save_result",
            func_body,
            "Should save results to scheduler",
        )

        # Should log to scheduler logs
        self.assertIn(
            "_log_job",
            func_body,
            "Should log execution to scheduler",
        )


class TestRunNowAPIIntegration(unittest.TestCase):
    """Integration test: Run Now with command-mode job via test client."""

    @classmethod
    def setUpClass(cls):
        """Create a test FastAPI app."""
        os.environ.setdefault("SCHEDULER_ALLOWED_TELEGRAM", "testuser")
        os.environ.setdefault("API_SHARED_KEY", "test_key_123")

    def _get_test_client(self):
        """Get a test client for the API."""
        try:
            from agent_manager import create_api_app
            from httpx import ASGITransport, AsyncClient

            app = create_api_app()
            transport = ASGITransport(app=app)
            return AsyncClient(transport=transport, base_url="https://test")
        except ImportError:
            self.skipTest("httpx not available for integration test")

    def test_run_now_command_mode_via_api(self):
        """POST /api/v1/scheduler/jobs/{id}/run should execute command directly."""
        try:
            from httpx import ASGITransport, AsyncClient
        except ImportError:
            self.skipTest("httpx not available")

        agent_manager = _load_module("issue96_agent_manager_api", REPO / "agent_manager.py")
        create_api_app = agent_manager.create_api_app
        app = create_api_app()

        # Mock the scheduler to return a command-mode job
        mock_job = {  # noqa: F841
            "id": "test_cmd_job",
            "name": "Test Command",
            "task": "echo hello",
            "mode": "command",
            "timeout": 30,
            "working_dir": "/tmp",
            "notify": False,
        }
        mock_scheduler = MagicMock()
        mock_scheduler.get_job.return_value = {"success": True, "result": mock_job}
        mock_scheduler.run_job.return_value = {"success": True}
        fake_loop = MagicMock()

        with patch.object(
            app.state.bg_task_mgr, "create_task", return_value={}
        ) as mock_create:  # noqa: F841

            async def _run():
                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport, base_url="https://test"
                ) as client:
                    # We need to mock _get_scheduler
                    with patch(
                        "agent_manager._get_scheduler_for_test",
                        return_value=None,
                    ):
                        response = await client.post(
                            "/api/v1/scheduler/jobs/test_cmd_job/run",
                            headers={
                                "Authorization": "Bearer test_key_123",
                            },
                        )
                        return response

            response = asyncio.run(_run())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["job_id"], "test_cmd_job")
        self.assertEqual(body["mode"], "command")
        self.assertEqual(body["status"], "running")
        mock_scheduler.get_job.assert_called_once_with("test_cmd_job")
        mock_scheduler.run_job.assert_called_once_with("test_cmd_job")
        mock_create.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs["runtime"], "shell")
        self.assertEqual(mock_create.call_args.kwargs["agent"], "command")
        fake_loop.run_in_executor.assert_called_once()

    def test_ai_mode_still_uses_background_task(self):
        """AI mode jobs should still be dispatched via _run_background_task."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        start = content.find("async def run_scheduler_job_now(")
        end = content.find("\n        @app.", start + 1)
        handler_body = content[start:end]

        # After the command-mode branch, there should be an else branch
        # that still uses _run_background_task
        lines = handler_body.split("\n")
        found_else = False
        found_bg_task_after_else = False
        for i, line in enumerate(lines):
            if "else:" in line and found_else is False:
                found_else = True
            if found_else and "_run_background_task" in line:
                found_bg_task_after_else = True
                break

        self.assertTrue(
            found_bg_task_after_else,
            "AI mode should still dispatch to _run_background_task in else branch",
        )


class TestRunCommandTaskCwd(unittest.TestCase):
    """Test working_dir handling for command-mode tasks."""

    def test_command_mode_uses_working_dir_from_job(self):
        """Command mode should use working_dir from job config."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        # Check in run_scheduler_job_now
        start = content.find("async def run_scheduler_job_now(")
        end = content.find("\n        @app.", start + 1)
        handler_body = content[start:end]

        self.assertIn(
            "working_dir",
            handler_body,
            "Command-mode Run Now should pass working_dir",
        )

    def test_command_task_passes_cwd_to_subprocess(self):
        """_run_command_task should pass working_dir as cwd to subprocess."""
        source = Path(__file__).resolve().parent.parent / "agent_manager.py"
        content = source.read_text()

        start = content.find("def _run_command_task(")
        end = content.find("\n    def ", start + 1)
        func_body = content[start:end]

        self.assertIn(
            "cwd=working_dir",
            func_body,
            "Should pass working_dir as cwd",
        )


if __name__ == "__main__":
    unittest.main()
