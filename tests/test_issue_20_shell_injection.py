"""Regression tests for Issue #20: shell injection in scheduler execution."""

import importlib.util
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


agent_manager = _load_module("issue20_agent_manager", REPO / "agent_manager.py")
executor_module = _load_module(
    "issue20_scheduler_executor", REPO / "scheduler" / "executor.py"
)
SessionManager = agent_manager.SessionManager
TaskSchedulerExecutor = executor_module.TaskSchedulerExecutor


def _build_manager(agent_dir: Path) -> SessionManager:
    mgr = SessionManager.__new__(SessionManager)
    mgr.command_timeout = 10
    mgr.AGENTS = {
        "orchestrator": {
            "path": str(agent_dir),
            "description": "test agent",
            "name": "orchestrator",
        }
    }
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    return mgr


class TestIssue20ShellInjection(unittest.TestCase):
    """Issue #20: command execution must not rely on shell=True."""

    def test_issue_20_scheduler_command_mode_does_not_execute_injected_follow_on_command(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            executor = TaskSchedulerExecutor()
            job = {
                "id": "issue20-scheduler",
                "name": "Issue 20 Scheduler",
                "task": (
                    'python3 -c "from pathlib import Path; '
                    'Path(\'safe.txt\').write_text(\'ok\')" ; '
                    'python3 -c "from pathlib import Path; '
                    'Path(\'pwned.txt\').write_text(\'bad\')"'
                ),
                "working_dir": str(tmp_path),
                "notify": False,
                "timeout": 10,
            }

            executor._execute_command_mode(job)

            self.assertTrue((tmp_path / "safe.txt").exists())
            self.assertFalse((tmp_path / "pwned.txt").exists())

    def test_issue_20_execute_bash_command_does_not_execute_injected_follow_on_command(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mgr = _build_manager(tmp_path)
            command = (
                'python3 -c "from pathlib import Path; '
                'Path(\'safe.txt\').write_text(\'ok\')" ; '
                'python3 -c "from pathlib import Path; '
                'Path(\'pwned.txt\').write_text(\'bad\')"'
            )

            result = mgr._execute_bash_command(command, "orchestrator")

            self.assertNotIn("Error:", result)
            self.assertTrue((tmp_path / "safe.txt").exists())
            self.assertFalse((tmp_path / "pwned.txt").exists())

    def test_issue_20_rejects_invalid_command_syntax(self):
        mgr = _build_manager(Path("/tmp"))
        result = mgr._execute_bash_command("echo 'unterminated", "orchestrator")
        self.assertIn("Invalid command syntax", result)

    def test_issue_20_wee_bash_tool_preserves_pipe_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mgr = _build_manager(tmp_path)

            result = mgr._wee_execute_tool(
                "bash",
                {"command": "printf 'alpha\\nbeta\\n' | head -n 1"},
                "orchestrator",
            )

            self.assertEqual(result, "alpha")


if __name__ == "__main__":
    unittest.main()
