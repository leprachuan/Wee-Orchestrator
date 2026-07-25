"""
Regression tests for wee-orchestrator-ios issue #8: "Native chat fails for agent
workdir while WebUI succeeds".

The reported symptom was a backend error surfacing in iOS chat:

    Failed to execute command: [Errno 2] No such file or directory:
    '/mnt/nas/Agents/research'

It looked like a client difference because a session on another agent worked.
It was not. On the API host, `research` is configured with a path that does not
exist while every other agent's path does:

    orchestrator      /mnt/nas/Agents/                  exists=True
    email-triage      /mnt/nas/Agents/email_triage      exists=True
    family-knowledge  /mnt/nas/Agents/family_knowledge  exists=True
    research          /mnt/nas/Agents/research          exists=False   <-
    devops            /mnt/nas/Agents/MyHomeDevops      exists=True
    wee-dev           /mnt/nas/Agents/wee-dev           exists=True
    smarthome         /mnt/nas/Agents/smarthome         exists=True

Every runtime hands that path to a subprocess as its cwd, so the same opaque
error came from all of them with nothing naming the agent or the fix.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_ios8")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9408")

from agent_manager import SessionManager  # noqa: E402


def _make_sm():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"agents": []}, tmp)
    tmp.close()
    return SessionManager(tmp.name)


class TestAgentWorkspaceValidation(unittest.TestCase):
    def setUp(self):
        self.sm = _make_sm()
        self.existing = tempfile.mkdtemp()

    def test_missing_workspace_is_reported_with_agent_and_path(self):
        self.sm.AGENTS = {
            "orchestrator": {"path": self.existing},
            "research": {"path": "/mnt/nas/Agents/research"},
        }

        message = self.sm.agent_workspace_error("research")

        self.assertIsNotNone(message, "a missing workspace must be reported")
        self.assertIn("research", message, "must name the agent")
        self.assertIn("/mnt/nas/Agents/research", message, "must name the path")
        self.assertIn("agents.json", message, "must say how to fix it")
        # The old failure was a bare OSError; the new one must not read like one.
        self.assertNotIn("Errno", message)

    def test_existing_workspace_is_accepted(self):
        self.sm.AGENTS = {"orchestrator": {"path": self.existing}}
        self.assertIsNone(self.sm.agent_workspace_error("orchestrator"))

    def test_blank_path_is_left_alone(self):
        """Some deployments rely on the process cwd rather than a per-agent dir."""
        self.sm.AGENTS = {"orchestrator": {"path": ""}}
        self.assertIsNone(self.sm.agent_workspace_error("orchestrator"))
        self.sm.AGENTS = {"orchestrator": {}}
        self.assertIsNone(self.sm.agent_workspace_error("orchestrator"))

    def test_unknown_agent_falls_back_to_orchestrator(self):
        """Matches how every dispatch site resolves an unknown agent."""
        self.sm.AGENTS = {"orchestrator": {"path": self.existing}}
        self.assertIsNone(self.sm.agent_workspace_error("does-not-exist"))

        self.sm.AGENTS = {"orchestrator": {"path": "/definitely/not/here"}}
        message = self.sm.agent_workspace_error("does-not-exist")
        self.assertIsNotNone(message)
        self.assertIn("/definitely/not/here", message)

    def test_only_the_broken_agent_is_affected(self):
        """The report said other agents worked; that must stay true."""
        self.sm.AGENTS = {
            "orchestrator": {"path": self.existing},
            "research": {"path": "/mnt/nas/Agents/research"},
            "devops": {"path": self.existing},
        }
        self.assertIsNotNone(self.sm.agent_workspace_error("research"))
        self.assertIsNone(self.sm.agent_workspace_error("devops"))
        self.assertIsNone(self.sm.agent_workspace_error("orchestrator"))
        self.assertIn("Other agents are unaffected", self.sm.agent_workspace_error("research"))

    def test_check_runs_once_at_the_shared_dispatch_boundary(self):
        """Ten sites resolve agent_dir; checking each would drift apart."""
        import inspect

        source = inspect.getsource(self.sm._dispatch_single_runtime)
        self.assertIn("agent_workspace_error", source)

        check_at = source.index("agent_workspace_error")
        first_runtime_at = source.index('if runtime == "copilot"')
        self.assertLess(
            check_at, first_runtime_at, "must run before any runtime is dispatched"
        )

    def test_message_is_returned_rather_than_raised(self):
        """Callers render dispatch output as chat text; an exception would 500."""
        import inspect

        source = inspect.getsource(self.sm._dispatch_single_runtime)
        self.assertIn("return workspace_error", source)
        self.assertNotIn("raise workspace_error", source)


if __name__ == "__main__":
    unittest.main()
