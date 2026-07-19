"""Tests for issue #400: wee-runtime system prompt overwhelms small local models.

The wee runtime (OpenAI-compatible API to Ollama/OpenRouter) was sending the
same verbose, agentic-tooling system prompt built for frontier models to
small local models, burying them in a skill-repository-config tutorial, a
lettered image-retrieval walkthrough, an irrelevant workspace file listing,
and a real embedded bearer token — none of which helps an 8B local model
answer "call me purple people eater". build_agent_context_prompt now accepts
a `compact` flag (used unconditionally for runtime="wee") that keeps every
capability referenced but drops the tutorial-style bloat.
"""

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("API_SHARED_KEY", "test_key_123")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import SessionManager  # noqa: E402


def _make_mgr(tmp_path="/tmp/wee_test_issue_400_session_map.json"):
    """Minimal SessionManager sufficient to call build_agent_context_prompt."""
    mgr = SessionManager.__new__(SessionManager)
    mgr._session_map_lock = threading.Lock()
    mgr.session_map_file = Path(tmp_path)
    mgr.command_timeout = 300
    mgr._bg_identity = None
    mgr._stream_buffers = {}
    mgr.skill_repositories = [
        {
            "name": "Anthropic Official",
            "url": "https://github.com/anthropics/skills.git",
            "description": "Official Anthropic skills repository",
        }
    ]
    mgr.AGENTS = {
        "orchestrator": {
            "path": "/tmp/wee_test_issue_400_agent_path",
            "description": "test orchestrator agent",
            "name": "orchestrator",
        }
    }
    return mgr


class TestCompactWeePrompt(unittest.TestCase):
    def setUp(self):
        self.mgr = _make_mgr()

    def _build(self, compact):
        return self.mgr.build_agent_context_prompt(
            "orchestrator",
            "call me purple people eater",
            "test-session-400",
            render_type="markdown",
            timeout=300,
            runtime="wee",
            model="ollama/gemma4:e4b",
            channel="webui",
            compact=compact,
        )

    def test_issue_400_compact_is_meaningfully_smaller(self):
        """Compact mode must shrink the prompt substantially (bloat removed)."""
        full = self._build(compact=False)
        compact = self._build(compact=True)
        self.assertLess(
            len(compact),
            len(full) * 0.7,
            "compact prompt should be at least 30% smaller than the full prompt",
        )

    def test_issue_400_compact_is_now_the_default_for_every_runtime(self):
        """compact now defaults to True regardless of runtime — non-wee runtimes
        (e.g. claude) get the trimmed prompt too unless they opt out."""
        default_call = self.mgr.build_agent_context_prompt(
            "orchestrator",
            "hello",
            "test-session-400b",
            render_type="markdown",
            timeout=300,
            runtime="claude",
            model="haiku",
            channel="webui",
        )
        self.assertNotIn("[Configuring Custom Skill Repositories]", default_call)
        self.assertNotIn("Option A — Direct external URL", default_call)

    def test_issue_400_compact_false_still_available_for_rollback(self):
        """The original full walkthrough form must still be reachable via an
        explicit compact=False, for comparison or emergency rollback."""
        full = self.mgr.build_agent_context_prompt(
            "orchestrator",
            "hello",
            "test-session-400d",
            render_type="markdown",
            timeout=300,
            runtime="claude",
            model="haiku",
            channel="webui",
            compact=False,
        )
        self.assertIn("[Skills Discovery & Management]", full)
        self.assertIn("[Configuring Custom Skill Repositories]", full)
        self.assertIn("Option A — Direct external URL", full)

    def test_issue_400_compact_drops_tutorial_bloat(self):
        """Compact mode must not include the verbose walkthroughs."""
        compact = self._build(compact=True)
        self.assertNotIn("[Configuring Custom Skill Repositories]", compact)
        self.assertNotIn("Option A — Direct external URL", compact)
        self.assertNotIn("Available resources in this agent's workspace", compact)

    def test_issue_400_compact_preserves_every_capability_reference(self):
        """No functionality should be lost — every capability must still be
        discoverable in the compact prompt, just stated tersely."""
        compact = self._build(compact=True)
        self.assertIn("/discover-skills", compact)
        self.assertIn("/load-skill", compact)
        self.assertIn("/background", compact)
        self.assertIn("/schedule", compact)
        self.assertIn("skill_repositories.json", compact)
        self.assertIn("[Wee Canvas]", compact)
        self.assertIn("[Wee Executor]", compact)
        self.assertIn("[Image Retrieval", compact)

    def test_issue_400_run_wee_native_does_not_opt_out_of_compact(self):
        """compact defaults to True now, so run_wee_native doesn't need to pass
        it explicitly — but it must not regress by passing compact=False."""
        with patch.object(
            self.mgr,
            "build_agent_context_prompt",
            return_value="stubbed system prompt",
        ) as mock_build:
            with patch.object(
                self.mgr,
                "get_or_create_session_data",
                return_value={"runtime": "wee", "model": "ollama/gemma4:e4b", "channel": "api"},
            ):
                with patch.object(self.mgr, "load_session_map", return_value={}):
                    with patch.object(self.mgr, "save_session_map"):
                        with patch("openai.OpenAI") as mock_openai_cls:
                            mock_client = mock_openai_cls.return_value
                            mock_client.chat.completions.create.side_effect = Exception(
                                "stop before network call"
                            )
                            try:
                                self.mgr.run_wee_native(
                                    prompt="test",
                                    model="ollama/gemma4:e4b",
                                    agent="orchestrator",
                                    session_id=None,
                                    resume=False,
                                    n8n_session_id="test-session-400c",
                                    timeout=30,
                                    render_type="text",
                                )
                            except Exception:
                                pass  # we only care that build_agent_context_prompt was called correctly

        self.assertTrue(mock_build.called)
        _, kwargs = mock_build.call_args
        self.assertIsNot(
            kwargs.get("compact"),
            False,
            "run_wee_native must not override the compact=True default with False",
        )


if __name__ == "__main__":
    unittest.main()
