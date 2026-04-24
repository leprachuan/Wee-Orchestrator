"""
Regression test for issue #223:
  'str' object has no attribute 'upper()'

Root cause: build_agent_context_prompt() used {channel.upper()} inside the
file_handling template string that was later expanded by str.format(). Python's
format() treats 'upper()' (including the parentheses) as the attribute name,
not as a method call, so it raises:
    AttributeError: 'str' object has no attribute 'upper()'

Additionally, {channel_limit} was missing from the format() kwargs, causing a
secondary KeyError after the above was fixed. A later regression accidentally
turned the inner template into an f-string, which raised UnboundLocalError by
referencing channel_upper before assignment.

Fixed by:
  - Replacing {channel.upper()} with {channel_upper} in the file_handling template
  - Replacing {SCRIPT_BASE_DIR} with {script_base_dir} (matches the kwarg passed)
  - Adding channel_limit= to the render_instruction.format() call
  - Initialising channel_limit before the conditional block so it is always defined

Note: build_agent_context_prompt() returns the outer context as a template string
(not yet expanded) — the inner render_instruction IS expanded by format() as part
of building it, but the outer context.format() is handled by the caller pipeline.
The tests therefore verify that no exception is raised during the inner expansion,
not that the final string contains channel-specific text.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_manager import SessionManager


def _make_manager(tmp_path: Path) -> SessionManager:
    agents_config = {
        "agents": [
            {
                "name": "devops",
                "description": "Test DevOps agent",
                "path": str(tmp_path),
            }
        ]
    }
    config_file = tmp_path / "agents.json"
    config_file.write_text(json.dumps(agents_config))
    return SessionManager(str(config_file))


class TestIssue223StrUpperRuntimeFailure(unittest.TestCase):
    """build_agent_context_prompt must not raise for any channel/render_type combo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = _make_manager(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, channel, render_type):
        return self.manager.build_agent_context_prompt(
            agent="devops",
            prompt="hello",
            n8n_session_id="sess-test",
            render_type=render_type,
            channel=channel,
        )

    # --- Primary regression: must not raise AttributeError ---

    def test_issue_223_str_object_upper_runtime_failure_telegram_markdown(self):
        """Regression: 'str' object has no attribute 'upper()' must not be raised."""
        try:
            self._call("telegram", "markdown")
        except AttributeError as e:
            self.fail(
                f"build_agent_context_prompt raised AttributeError for "
                f"channel=telegram render_type=markdown: {e}"
            )

    def test_issue_223_str_object_upper_runtime_failure_webex_markdown(self):
        try:
            self._call("webex", "markdown")
        except AttributeError as e:
            self.fail(
                f"build_agent_context_prompt raised AttributeError for "
                f"channel=webex render_type=markdown: {e}"
            )

    def test_issue_223_str_object_upper_runtime_failure_webui_markdown(self):
        try:
            self._call("webui", "markdown")
        except (AttributeError, UnboundLocalError) as e:
            self.fail(
                f"build_agent_context_prompt raised {type(e).__name__} for "
                f"channel=webui render_type=markdown: {e}"
            )

    def test_issue_223_str_object_upper_runtime_failure_telegram_html(self):
        try:
            self._call("telegram", "telegram_html")
        except AttributeError as e:
            self.fail(
                f"build_agent_context_prompt raised AttributeError for "
                f"channel=telegram render_type=telegram_html: {e}"
            )

    def test_issue_223_str_object_upper_runtime_failure_text_render(self):
        """render_type=text skips the file-handling block — must not raise either."""
        try:
            self._call("webui", "text")
        except AttributeError as e:
            self.fail(
                f"build_agent_context_prompt raised AttributeError for "
                f"channel=webui render_type=text: {e}"
            )

    def test_issue_223_unknown_channel_uses_default_limit(self):
        """Unknown channel should use the default 100 MB limit without raising."""
        try:
            self._call("unknown_channel", "markdown")
        except (AttributeError, KeyError) as e:
            self.fail(
                f"build_agent_context_prompt raised {type(e).__name__} for "
                f"unknown channel: {e}"
            )

    # --- Verify the inner template no longer contains the broken placeholder ---

    def test_issue_223_channel_upper_call_syntax_absent_from_source(self):
        """{channel.upper()} must not appear in the file_handling template source."""
        import ast
        src_path = Path(__file__).parent.parent / "agent_manager.py"
        src = src_path.read_text()
        self.assertNotIn(
            "{channel.upper()}",
            src,
            "Template still contains {channel.upper()} — format() would raise "
            "AttributeError at runtime",
        )

    def test_issue_223_channel_limit_in_format_kwargs(self):
        """{channel_limit} must be a named kwarg in the render_instruction.format() call."""
        src_path = Path(__file__).parent.parent / "agent_manager.py"
        src = src_path.read_text()
        self.assertIn(
            "channel_limit=channel_limit",
            src,
            "channel_limit= kwarg missing from render_instruction.format() call — "
            "KeyError would follow the upper() fix",
        )

    def test_issue_223_file_handling_template_is_not_an_f_string(self):
        """The file_handling template must stay deferred until format()."""
        src_path = Path(__file__).parent.parent / "agent_manager.py"
        src = src_path.read_text()
        self.assertIn(
            'file_handling = """',
            src,
            "file_handling template became an f-string again — channel_upper "
            "would be evaluated before assignment",
        )


if __name__ == "__main__":
    unittest.main()
