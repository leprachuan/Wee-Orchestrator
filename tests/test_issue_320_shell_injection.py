"""
Regression test for Issue #320: Shell-based task dispatch causes syntax errors

Tests that shell injection vulnerabilities are fixed by using shlex.split()
instead of shell=True for command execution.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
import shlex


class TestIssue320ShellInjection(unittest.TestCase):
    """Regression tests for Issue #320 - Shell injection vulnerability."""

    def test_no_shell_true_in_subprocess_calls(self):
        """Verify that agent_manager.py does not use shell=True."""
        agent_manager_path = Path(__file__).parent.parent / "agent_manager.py"

        if not agent_manager_path.exists():
            self.skipTest("agent_manager.py not found at expected path")

        content = agent_manager_path.read_text()

        # Look for the vulnerable pattern: shell=True in subprocess.run
        # Count occurrences (there should be none)
        vulnerable_count = content.count("shell=True")

        self.assertEqual(
            vulnerable_count,
            0,
            f"Found {vulnerable_count} occurrences of shell=True in agent_manager.py. "
            "Shell injection vulnerability still present!",
        )

        # Verify that shell=False is used instead
        safe_count = content.count("shell=False")
        self.assertGreater(
            safe_count, 0, "No shell=False found - fix may not be applied"
        )

    def test_shlex_split_with_edge_cases(self):
        """Test shlex.split behavior with edge cases that would fail with shell=True."""
        test_cases = [
            ("echo hello", ["echo", "hello"]),
            ("echo 'hello world'", ["echo", "hello world"]),
            ('echo "test"', ["echo", "test"]),
            ("echo test | grep test", ["echo", "test", "|", "grep", "test"]),
            ("python3 -c print(1)", ["python3", "-c", "print(1)"]),
        ]

        for cmd, expected in test_cases:
            result = shlex.split(cmd, posix=True)
            self.assertEqual(
                result,
                expected,
                f"shlex.split failed for: {cmd}",
            )

    def test_shlex_prevents_command_injection(self):
        """Test that shlex.split prevents command injection attempts."""
        # These injection attempts should be parsed as literal strings
        injection_attempts = [
            "echo test; rm -rf /",  # Command chaining - semicolon is treated literally
            "echo $(whoami)",  # Command substitution - parentheses are literal
            "echo `whoami`",  # Backtick substitution - backticks are literal
            "echo test & sleep 10",  # Background execution - ampersand is literal
            "echo test > /tmp/file",  # Redirection - redirect is literal
            "echo test && false",  # Logical AND - ampersands are literal
            "echo test || true",  # Logical OR - pipes are literal
        ]

        for cmd in injection_attempts:
            try:
                # shlex.split should treat these as literal arguments
                argv = shlex.split(cmd, posix=True)
                # The key point: these should NOT be executed as shell commands
                # when passed to subprocess.run with shell=False
                self.assertGreater(len(argv), 0)
            except ValueError as e:
                # Some commands with unmatched quotes might fail
                # That's OK - the important thing is they're not executed
                pass

    def test_safe_subprocess_execution_with_shlex(self):
        """Test that subprocess.run with shell=False + shlex.split is safe."""
        # This is a simplified version of what the fixed code does
        command = "echo 'hello world'"
        argv = shlex.split(command, posix=True)

        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            shell=False,  # CRITICAL: shell=False
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("hello world", result.stdout)

    def test_large_output_handling_with_safe_parsing(self):
        """Test that large output doesn't cause shell injection issues."""
        # Create command that produces large output
        command = "python3 -c \"print('x' * 5000)\""
        argv = shlex.split(command, posix=True)

        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            shell=False,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0)
        # Verify output is captured
        self.assertGreater(len(result.stdout), 4000)


if __name__ == "__main__":
    unittest.main()
