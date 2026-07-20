"""Tests for Issue #113: wee runtime anti-hallucination prompt.

Issue #443 removed the hand-rolled bash/python tool loop (and the
SSH-flag-injection wrapper around it, `sanitize_bash_command` /
`_wee_sanitize_bash_command`) along with it — the Copilot SDK is now the
only execution path and owns its own shell tool directly. What remains
worth covering here:

1. Anti-hallucination prompt injection (still used by the SDK-only path)
2. Known_hosts infrastructure (informational, dev-host connectivity)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wee_runtime import _ANTI_HALLUCINATION_PROMPT  # noqa: E402


class TestAntiHallucinationPrompt(unittest.TestCase):
    """Test the anti-hallucination prompt constant and injection."""

    def test_prompt_contains_never_fabricate(self):
        self.assertIn("NEVER fabricate", _ANTI_HALLUCINATION_PROMPT)

    def test_prompt_contains_exact_error(self):
        self.assertIn("EXACT error message", _ANTI_HALLUCINATION_PROMPT)

    def test_prompt_contains_ssh_instruction(self):
        self.assertIn("StrictHostKeyChecking", _ANTI_HALLUCINATION_PROMPT)

    def test_prompt_contains_example_labeling(self):
        self.assertIn("EXAMPLE (not real output)", _ANTI_HALLUCINATION_PROMPT)

    def test_prompt_is_nonempty_string(self):
        self.assertIsInstance(_ANTI_HALLUCINATION_PROMPT, str)
        self.assertGreater(len(_ANTI_HALLUCINATION_PROMPT), 100)

    def test_prompt_starts_with_section_header(self):
        self.assertIn("[CRITICAL", _ANTI_HALLUCINATION_PROMPT)


class TestKnownHostsInfrastructure(unittest.TestCase):
    """Informational: verify SSH known_hosts was fixed on dev host."""

    def test_known_hosts_has_dev_host_key(self):
        """Verify 192.168.1.100 is in root's known_hosts on the dev host."""
        import subprocess

        result = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "BatchMode=yes",
                "root@192.168.1.100",
                "grep -c '192.168.1.100' ~/.ssh/known_hosts",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        count = int(result.stdout.strip() or "0")
        self.assertGreater(
            count,
            0,
            "192.168.1.100 not in known_hosts on dev host — "
            "run: ssh-keyscan 192.168.1.100 >> ~/.ssh/known_hosts",
        )

    def test_loopback_ssh_works(self):
        """Verify SSH from dev host to itself works."""
        import subprocess

        result = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "BatchMode=yes",
                "root@192.168.1.100",
                "ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes root@192.168.1.100 echo SSH_LOOPBACK_OK",
            ],  # noqa: E501
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertIn(
            "SSH_LOOPBACK_OK", result.stdout, f"Loopback SSH failed: {result.stderr}"
        )  # noqa: E127


if __name__ == "__main__":
    unittest.main()
