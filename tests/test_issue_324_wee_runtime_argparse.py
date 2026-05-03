#!/usr/bin/env python3
"""
Regression test for Issue #324: Bug: heartbeat-fosterbot scheduler fails instantly when runtime=wee

Issue: The scheduled heartbeat job heartbeat-fosterbot started failing instantly after its scheduler
config was switched to runtime: "wee" with model ollama/qwen3.5-64k:latest.

Root cause: agent_manager.py CLI argparse --runtime choices omitted "wee", even though the code
supported it elsewhere, causing scheduler invocations to fail with "invalid choice: 'wee'".

Fix: Add "wee" to the runtime choices in argparse and help text.

This regression test verifies:
1. argparse accepts "wee" as a valid --runtime choice
2. No "invalid choice" error is raised when using --runtime wee
3. The command exits successfully with --runtime wee and --list-runtimes
"""

import subprocess
import sys
import unittest
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import agent_manager


class TestIssue324WeeRuntimeArgparse(unittest.TestCase):
    """Test that issue #324 (wee runtime argparse) is fixed"""

    def test_wee_runtime_in_argparse_choices(self):
        """Verify that 'wee' is in the argparse runtime choices"""
        # This test checks the actual argparse parser in agent_manager
        # by importing it and checking the parser configuration
        
        # The parser is created in main() so we need to check the code directly
        with open(str(Path(__file__).parent.parent / "agent_manager.py"), "r") as f:
            content = f.read()
        
        # Check that "wee" is in the runtime choices list
        self.assertIn('"wee"', content, 
                     "wee should be in the argparse choices list")
        
        # Check that the help text includes wee
        self.assertIn('Set the runtime to use (choices: copilot, copilot-sdk, opencode, claude, claude-sdk, gemini, codex, devin, cursor, wee)',
                     content,
                     "Help text should include wee in the available choices")

    def test_agent_manager_accepts_wee_runtime_cli(self):
        """Test that agent_manager.py CLI accepts --runtime wee without argparse error"""
        # This is the actual command that was failing in the issue
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent.parent / "agent_manager.py"),
                "--runtime", "wee",
                "--list-runtimes"
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent)
        )
        
        # The command should not fail with an argparse error
        # (exit code 2 is the argparse error code)
        self.assertNotEqual(result.returncode, 2,
                           f"agent_manager.py should accept --runtime wee. stderr: {result.stderr}")
        
        # Check that it didn't say "invalid choice"
        self.assertNotIn("invalid choice", result.stderr,
                        "Should not have argparse error about invalid choice: 'wee'")
        
        # Check that wee appears in the output
        self.assertIn("wee", result.stdout,
                     "wee should be in the list of available runtimes")

    def test_scheduler_can_invoke_agent_manager_with_wee_runtime(self):
        """Test that scheduler-like invocation with --runtime wee works"""
        # Simulate what the scheduler does: call agent_manager.py with --runtime wee
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent.parent / "agent_manager.py"),
                "--agent", "orchestrator",
                "--runtime", "wee",
                "--model", "ollama/qwen3.5-64k:latest",
                "--config", str(Path(__file__).parent.parent / "agents.json"),
                "--list-agents"
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
            timeout=10
        )
        
        # Should not fail on argparse
        if result.returncode == 2:
            # argparse error
            self.fail(f"Scheduler-like invocation failed with argparse error: {result.stderr}")
        
        # Check that it didn't say "invalid choice"
        self.assertNotIn("invalid choice", result.stderr,
                        "Should not have argparse error about invalid choice: 'wee'")


if __name__ == "__main__":
    unittest.main()
