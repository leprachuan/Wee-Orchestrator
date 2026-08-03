#!/usr/bin/env python3
"""Additional regression tests for Issue #111: wee runtime tool & skill execution audit.

Issue #443 removed the hand-rolled bash/python tool loop, SSH-sanitization
wrapper, and _WEE_TOOL_CAPABILITY_PROMPT this file originally covered — the
Copilot SDK is now the only execution path. What's still directly testable:

11. Skills context loaded via build_agent_context_prompt for wee runtime
12. _wee_augment_system_prompt_with_tools includes CRITICAL directive about not refusing
13. Anti-hallucination prompt covers all key rules
20. Skills are surfaced (or correctly absent) in agent context
"""

import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import SessionManager  # noqa: E402


def _make_mgr():
    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = threading.Lock()
    mgr.command_timeout = 300
    mgr.AGENTS = {
        "orchestrator": {
            "path": "/opt",
            "description": "Orchestrator agent",
            "name": "orchestrator",
        }
    }
    mgr._stream_buffers = {}
    mgr.session_map_file = Path("/tmp/wee111_session_map.json")
    return mgr


def _make_text_chunk(content_text):
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = content_text
    chunk.choices[0].delta.tool_calls = None
    return chunk


class TestIssue111SystemPrompt(unittest.TestCase):
    """Issue #111: System prompt completeness checks."""

    def test_augmented_prompt_includes_critical_directive(self):
        """Issue #111: Augmented system prompt must include CRITICAL about not refusing."""  # noqa: E501
        mgr = _make_mgr()
        result = mgr._wee_augment_system_prompt_with_tools("base")
        self.assertIn("CRITICAL", result, "Must include CRITICAL directive")
        self.assertIn(
            "NEVER refuse", result, "Must tell model to never refuse tool use"
        )

    def test_anti_hallucination_prompt_includes_key_rules(self):
        """Issue #111: Anti-hallucination prompt covers all key rules."""
        result = SessionManager._wee_anti_hallucination_prompt()
        self.assertIn("fabricate", result.lower(), "Must forbid fabrication")
        self.assertIn("placeholder", result.lower(), "Must forbid placeholder output")
        self.assertIn("error", result.lower(), "Must require relaying errors verbatim")
        self.assertIn(
            "StrictHostKeyChecking", result, "Must mention SSH flag requirement"
        )

class TestIssue111SkillsLoading(unittest.TestCase):
    """Issue #111: Skills and AGENTS.md context loaded for wee runtime."""

    @patch("openai.OpenAI")
    def test_build_agent_context_prompt_called_with_wee_runtime(self, mock_openai_cls):
        """Issue #111: build_agent_context_prompt is called with runtime='wee'."""
        mgr = _make_mgr()
        sid = "test_111_skills_runtime"
        session_data = {"runtime": "wee", "model": "ollama/qwen3:8b", "channel": "api"}

        context_calls = []

        def capture_context(*args, **kwargs):
            context_calls.append({"args": args, "kwargs": kwargs})
            return "context prompt"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter(
            [_make_text_chunk("ok")]
        )
        mock_openai_cls.return_value = mock_client

        with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
            with patch.object(
                mgr, "build_agent_context_prompt", side_effect=capture_context
            ):
                with patch.object(
                    mgr, "load_session_map", return_value={sid: session_data}
                ):
                    with patch.object(mgr, "save_session_map"):
                        mgr.run_wee_native(
                            prompt="test",
                            model="ollama/qwen3:8b",
                            agent="orchestrator",
                            session_id=None,
                            resume=False,
                            n8n_session_id=sid,
                            timeout=30,
                        )

        self.assertEqual(len(context_calls), 1)
        kwargs = context_calls[0]["kwargs"]
        self.assertEqual(
            kwargs.get("runtime"),
            "wee",
            "build_agent_context_prompt must be called with runtime='wee'",
        )

    def test_load_agent_skills_returns_skills_when_present(self):
        """Issue #111: load_agent_skills returns skill context for agents with skills."""  # noqa: E501
        mgr = _make_mgr()
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / ".github" / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(
                "---\nname: test-skill\ndescription: A test skill\n---\n# Test Skill\n"
            )

            result = mgr.load_agent_skills(tmpdir)
            self.assertIn(
                "test-skill", result, "Skills context must include skill name"
            )
            self.assertIn(
                "A test skill", result, "Skills context must include description"
            )

    def test_load_agent_skills_returns_no_skill_entries_when_no_skills(self):
        """Issue #111: load_agent_skills returns no skill entries when no skills directory."""  # noqa: E501
        mgr = _make_mgr()
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            result = mgr.load_agent_skills(tmpdir)
            self.assertNotIn(
                "[Agent Skills - Available]",
                result,
                "No skills means no Available Skills section",
            )


if __name__ == "__main__":
    unittest.main()
