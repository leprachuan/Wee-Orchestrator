"""Regression test: Claude runtime background task must include --verbose flag.

Bug: Background tasks dispatched with runtime=claude failed immediately with:
  "When using --print, --output-format=stream-json requires --verbose"

Fix: _build_bg_cmd() in agent_manager.py must include --verbose when constructing
the claude CLI command for background tasks.
"""
import sys
import os
import unittest

# Allow import from dev repo
sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


class TestCluadeBackgroundTaskVerboseFlag(unittest.TestCase):
    """Ensure _build_bg_cmd includes --verbose for the claude runtime."""

    def _get_build_bg_cmd_source(self):
        """Extract the _build_bg_cmd function source from agent_manager.py."""
        agent_manager_path = "/opt/n8n-copilot-shim-dev/agent_manager.py"
        with open(agent_manager_path) as f:
            return f.read()

    def test_claude_bg_cmd_contains_verbose_flag(self):
        """The claude runtime branch of _build_bg_cmd must include --verbose."""
        source = self._get_build_bg_cmd_source()

        # Find the _build_bg_cmd function
        start = source.find("def _build_bg_cmd(")
        self.assertGreater(start, 0, "_build_bg_cmd function not found in agent_manager.py")

        # Find the claude runtime branch within the function
        claude_branch_start = source.find('elif eff_runtime == "claude":', start)
        self.assertGreater(claude_branch_start, 0, "claude runtime branch not found in _build_bg_cmd")

        # Find the next elif/else after the claude branch (marks end of clause)
        next_branch = source.find("elif eff_runtime ==", claude_branch_start + 1)
        if next_branch == -1:
            next_branch = source.find("else:", claude_branch_start + 1)

        claude_block = source[claude_branch_start:next_branch]

        # --verbose must be present
        self.assertIn(
            '"--verbose"',
            claude_block,
            "claude runtime background task command missing '--verbose' flag. "
            "This causes: 'When using --print, --output-format=stream-json requires --verbose'",
        )

    def test_claude_bg_cmd_contains_stream_json(self):
        """The claude runtime command must use stream-json output format."""
        source = self._get_build_bg_cmd_source()
        start = source.find("def _build_bg_cmd(")
        claude_branch_start = source.find('elif eff_runtime == "claude":', start)
        next_branch = source.find("elif eff_runtime ==", claude_branch_start + 1)
        claude_block = source[claude_branch_start:next_branch]
        self.assertIn('"stream-json"', claude_block)

    def test_claude_bg_cmd_verbose_before_model(self):
        """--verbose should appear before --model in the claude command list."""
        source = self._get_build_bg_cmd_source()
        start = source.find("def _build_bg_cmd(")
        claude_branch_start = source.find('elif eff_runtime == "claude":', start)
        next_branch = source.find("elif eff_runtime ==", claude_branch_start + 1)
        claude_block = source[claude_branch_start:next_branch]

        verbose_pos = claude_block.find('"--verbose"')
        model_pos = claude_block.find('"--model"')
        self.assertGreater(verbose_pos, 0, "--verbose not found in claude block")
        self.assertGreater(model_pos, 0, "--model not found in claude block")
        self.assertLess(
            verbose_pos,
            model_pos,
            "--verbose should appear before --model in claude command list",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
