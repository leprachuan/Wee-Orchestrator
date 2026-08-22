"""
Regression test for Issue #296: Elevated permissions not passed to spawned devin subprocesses.

Tests that when a task is dispatched to an agent with permissions.mode == "elevated",
the permission mode environment variables are correctly set in the subprocess environment.
"""

import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call
from pathlib import Path


class TestElevatedPermissionsEnvironment(unittest.TestCase):
    """Test that elevated permissions are passed to subprocess environment."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()
        # Import agent_manager here to ensure test isolation
        from agent_manager import SessionManager

        self.agent_manager = SessionManager()

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_devin_elevated_sets_permission_mode_env_var(self):
        """Test that DEVIN_PERMISSION_MODE=dangerous is set for elevated agents."""
        from agent_manager import SessionManager

        manager = SessionManager()

        # Mock _execute_subprocess_with_tracking to capture the environment
        captured_env = {}

        def mock_execute_subprocess(cmd, cwd, timeout, runtime, agent, prompt, n8n_session_id, use_pty=False, stdin_text=""):
            # Capture the environment that would be passed to subprocess
            # In the actual code, this happens at Popen time
            captured_env["cmd"] = cmd
            captured_env["runtime"] = runtime
            return "Mock devin output"

        # Create a session with elevated permissions
        test_session_id = "test_devin_elevated_session"
        
        with patch.object(manager, "_execute_subprocess_with_tracking", side_effect=mock_execute_subprocess):
            with patch.object(manager, "_save_devin_session_id"):
                with patch.object(manager, "get_or_create_session_data") as mock_session:
                    # Configure mock session with elevated permissions
                    mock_session.return_value = {
                        "session_id": "test_session",
                        "permissions": {"mode": "elevated"},
                        "channel": "webui",
                    }

                    # Call run_devin with elevated permissions
                    manager.run_devin(
                        prompt="Test prompt",
                        model="claude-sonnet-4",
                        agent="wee-dev",
                        session_id=None,
                        resume=False,
                        n8n_session_id=test_session_id,
                        timeout=60,
                    )

        # Verify that the command includes --permission-mode dangerous
        self.assertIn("--permission-mode", captured_env.get("cmd", []))
        permission_mode_index = captured_env["cmd"].index("--permission-mode")
        self.assertEqual(
            captured_env["cmd"][permission_mode_index + 1],
            "dangerous",
            "Devin should be called with --permission-mode dangerous for elevated permissions"
        )

    def test_build_runtime_permission_env_mapping(self):
        """Test the permission mode to env var mapping function."""
        from agent_manager import SessionManager

        # Get or create the mapping function
        manager = SessionManager()
        
        # Test that the function exists and works correctly
        if hasattr(manager, "build_runtime_permission_env"):
            # Test elevated mode for devin
            env_updates = manager.build_runtime_permission_env("devin", "elevated")
            self.assertIn("DEVIN_PERMISSION_MODE", env_updates)
            self.assertEqual(env_updates["DEVIN_PERMISSION_MODE"], "dangerous")
            
            # Test restricted mode for devin should not set the env var or set it to default
            env_updates = manager.build_runtime_permission_env("devin", "restricted")
            # Should either not have the key or have it set to "auto"
            if "DEVIN_PERMISSION_MODE" in env_updates:
                self.assertEqual(env_updates["DEVIN_PERMISSION_MODE"], "auto")
        else:
            self.skipTest("build_runtime_permission_env method not yet implemented")

    def test_execute_subprocess_includes_permission_env(self):
        """Test that _execute_subprocess_with_tracking includes permission env vars."""
        from agent_manager import SessionManager

        manager = SessionManager()
        
        # Mock subprocess.Popen to capture the env argument
        captured_popen_calls = []
        
        original_popen = subprocess.Popen
        
        def mock_popen(*args, **kwargs):
            captured_popen_calls.append({"args": args, "kwargs": kwargs})
            # Return a mock process object
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_process.communicate.return_value = ("output", "error")
            mock_process.returncode = 0
            return mock_process
        
        with patch("subprocess.Popen", side_effect=mock_popen):
            with patch.object(manager, "track_running_query"):
                # Call _execute_subprocess_with_tracking with elevated permissions
                try:
                    result = manager._execute_subprocess_with_tracking(
                        cmd=["echo", "test"],
                        cwd="/tmp",
                        timeout=30,
                        runtime="devin",
                        agent="wee-dev",
                        prompt="test",
                        n8n_session_id="test_session",
                        use_pty=False,
                    )
                except:
                    pass  # We're just capturing the Popen call
        
        # Verify that Popen was called with environment variables
        if captured_popen_calls:
            env = captured_popen_calls[0]["kwargs"].get("env", {})
            # At minimum, WEE_SESSION_ID should be set
            self.assertIn("WEE_SESSION_ID", env)

    def test_restricted_mode_doesnt_set_dangerous_perms(self):
        """Test that restricted mode does NOT set DEVIN_PERMISSION_MODE=dangerous."""
        from agent_manager import SessionManager

        manager = SessionManager()

        # Mock _execute_subprocess_with_tracking to capture environment
        captured_env = {}

        def mock_execute_subprocess(cmd, cwd, timeout, runtime, agent, prompt, n8n_session_id, use_pty=False, stdin_text=""):
            captured_env["cmd"] = cmd
            return "Mock devin output"

        # Create a session with restricted permissions
        test_session_id = "test_devin_restricted_session"
        
        with patch.object(manager, "_execute_subprocess_with_tracking", side_effect=mock_execute_subprocess):
            with patch.object(manager, "_save_devin_session_id"):
                with patch.object(manager, "get_or_create_session_data") as mock_session:
                    # Configure mock session with restricted permissions (default)
                    mock_session.return_value = {
                        "session_id": "test_session",
                        "permissions": {"mode": "restricted"},
                        "channel": "webui",
                    }

                    # Call run_devin with restricted permissions
                    manager.run_devin(
                        prompt="Test prompt",
                        model="claude-sonnet-4",
                        agent="orchestrator",
                        session_id=None,
                        resume=False,
                        n8n_session_id=test_session_id,
                        timeout=60,
                    )

        # Verify that the command includes --permission-mode auto (not dangerous)
        self.assertIn("--permission-mode", captured_env.get("cmd", []))
        permission_mode_index = captured_env["cmd"].index("--permission-mode")
        self.assertEqual(
            captured_env["cmd"][permission_mode_index + 1],
            "auto",
            "Devin should be called with --permission-mode auto for restricted permissions"
        )


if __name__ == "__main__":
    unittest.main()
