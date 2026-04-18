#!/usr/bin/env python3
"""Additional regression tests for Issue #111: wee runtime tool & skill execution audit.

Tests added by Issue #111 implementation:
 8. SSH sanitization wired into _wee_execute_tool (bash commands with ssh get flags injected)
 9. SSH sanitization wired into wee_runtime.py execute_tool (standalone CLI)
10. SSH sanitization NOT applied when StrictHostKeyChecking already present
11. Skills context loaded via build_agent_context_prompt for wee runtime
12. _wee_augment_system_prompt_with_tools includes CRITICAL directive about not refusing
13. System prompt includes both tool declaration AND anti-hallucination rules
14. Tool fallback: when tools not supported, retry without tools (no crash)
15. Multi-round tool loop: model can issue multiple rounds of tool calls
16. wee_runtime.py _WEE_TOOL_CAPABILITY_PROMPT constant exists
17. wee_runtime.py effective_system_prompt includes tool capability when --tools flag used
18. _wee_execute_tool handles unknown tool names gracefully
19. _wee_execute_tool handles empty command/code gracefully
20. Tool result appended to conversation history for context persistence
"""
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_manager import SessionManager


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


def _make_tool_call_chunks(tool_id, func_name, arguments_json):
    chunks = []
    c1 = MagicMock()
    c1.choices = [MagicMock()]
    c1.choices[0].delta.content = None
    tc1 = MagicMock()
    tc1.index = 0
    tc1.id = tool_id
    tc1.function = MagicMock()
    tc1.function.name = func_name
    tc1.function.arguments = ""
    c1.choices[0].delta.tool_calls = [tc1]
    chunks.append(c1)

    c2 = MagicMock()
    c2.choices = [MagicMock()]
    c2.choices[0].delta.content = None
    tc2 = MagicMock()
    tc2.index = 0
    tc2.id = None
    tc2.function = MagicMock()
    tc2.function.name = None
    tc2.function.arguments = arguments_json
    c2.choices[0].delta.tool_calls = [tc2]
    chunks.append(c2)

    c3 = MagicMock()
    c3.choices = []
    chunks.append(c3)
    return chunks


class TestIssue111SSHSanitization(unittest.TestCase):
    """Issue #111: SSH sanitization wired into tool execution."""

    def test_ssh_sanitization_wired_into_wee_execute_tool(self):
        """Issue #111: _wee_execute_tool must call _wee_sanitize_bash_command for bash tools."""
        mgr = _make_mgr()
        sanitize_calls = []

        original_sanitize = mgr._wee_sanitize_bash_command

        def track_sanitize(command):
            sanitize_calls.append(command)
            return original_sanitize(command)

        with patch.object(mgr, "_wee_sanitize_bash_command", side_effect=track_sanitize):
            with patch.object(mgr, "_execute_bash_command", return_value="ok"):
                mgr._wee_execute_tool("bash", {"command": "ssh root@192.168.1.100 uptime"}, "orchestrator")

        self.assertEqual(len(sanitize_calls), 1, "Sanitize must be called once for bash tool")
        self.assertIn("ssh", sanitize_calls[0], "Command with ssh must be passed to sanitize")

    def test_ssh_sanitization_injects_flags(self):
        """Issue #111: SSH command gets StrictHostKeyChecking flag injected."""
        mgr = _make_mgr()
        result = mgr._wee_sanitize_bash_command("ssh root@192.168.1.100 uptime")
        self.assertIn("StrictHostKeyChecking=accept-new", result,
                       "SSH command must get StrictHostKeyChecking injected")

    def test_ssh_sanitization_no_double_inject(self):
        """Issue #111: Don't inject StrictHostKeyChecking if already present."""
        mgr = _make_mgr()
        cmd = "ssh -o StrictHostKeyChecking=no root@192.168.1.100 uptime"
        result = mgr._wee_sanitize_bash_command(cmd)
        self.assertEqual(result, cmd, "Must not modify command that already has StrictHostKeyChecking")

    def test_ssh_sanitization_not_applied_to_non_ssh(self):
        """Issue #111: Non-SSH bash commands pass through unchanged."""
        mgr = _make_mgr()
        cmd = "ls /opt"
        result = mgr._wee_sanitize_bash_command(cmd)
        self.assertEqual(result, cmd, "Non-SSH commands must pass through unchanged")

    def test_ssh_sanitization_handles_scp(self):
        """Issue #111: SCP commands also get sanitized."""
        mgr = _make_mgr()
        result = mgr._wee_sanitize_bash_command("scp file.txt root@192.168.1.100:/tmp/")
        self.assertIn("StrictHostKeyChecking=accept-new", result,
                       "SCP command must get StrictHostKeyChecking injected")

    def test_ssh_sanitization_handles_sftp(self):
        """Issue #111: SFTP commands also get sanitized."""
        mgr = _make_mgr()
        result = mgr._wee_sanitize_bash_command("sftp root@192.168.1.100")
        self.assertIn("StrictHostKeyChecking=accept-new", result,
                       "SFTP command must get StrictHostKeyChecking injected")


class TestIssue111WeeRuntimeCLI(unittest.TestCase):
    """Issue #111: wee_runtime.py standalone CLI tool capability prompt."""

    def test_wee_tool_capability_prompt_constant_exists(self):
        """Issue #111: _WEE_TOOL_CAPABILITY_PROMPT must exist in wee_runtime.py."""
        import wee_runtime
        self.assertTrue(
            hasattr(wee_runtime, "_WEE_TOOL_CAPABILITY_PROMPT"),
            "wee_runtime must have _WEE_TOOL_CAPABILITY_PROMPT constant",
        )
        prompt = wee_runtime._WEE_TOOL_CAPABILITY_PROMPT
        self.assertIn("bash", prompt.lower(), "Tool prompt must mention bash")
        self.assertIn("python", prompt.lower(), "Tool prompt must mention python")
        self.assertIn("IMPORTANT", prompt, "Tool prompt must include CRITICAL directive")

    def test_wee_runtime_execute_tool_calls_sanitize(self):
        """Issue #111: wee_runtime.py execute_tool calls sanitize_bash_command for bash."""
        import wee_runtime
        with patch.object(wee_runtime, "sanitize_bash_command", wraps=wee_runtime.sanitize_bash_command) as mock_sanitize:
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
                wee_runtime.execute_tool("bash", {"command": "ssh root@host ls"})
                mock_sanitize.assert_called_once()

    def test_wee_runtime_anti_hallucination_prompt_exists(self):
        """Issue #111: _ANTI_HALLUCINATION_PROMPT must exist in wee_runtime.py."""
        import wee_runtime
        self.assertTrue(
            hasattr(wee_runtime, "_ANTI_HALLUCINATION_PROMPT"),
            "wee_runtime must have _ANTI_HALLUCINATION_PROMPT constant",
        )
        prompt = wee_runtime._ANTI_HALLUCINATION_PROMPT
        self.assertIn("fabricate", prompt.lower(), "Anti-hallucination prompt must forbid fabrication")


class TestIssue111ToolExecution(unittest.TestCase):
    """Issue #111: Tool execution robustness."""

    def test_unknown_tool_name_returns_error(self):
        """Issue #111: Unknown tool names return descriptive error, not crash."""
        mgr = _make_mgr()
        result = mgr._wee_execute_tool("unknown_tool", {"arg": "value"}, "orchestrator")
        self.assertIn("Error", result, "Unknown tool must return error")
        self.assertIn("unknown_tool", result, "Error must mention the unknown tool name")

    def test_empty_bash_command_returns_error(self):
        """Issue #111: Empty bash command returns error."""
        mgr = _make_mgr()
        result = mgr._wee_execute_tool("bash", {"command": ""}, "orchestrator")
        self.assertIn("Error", result, "Empty command must return error")

    def test_empty_python_code_returns_error(self):
        """Issue #111: Empty python code returns error."""
        mgr = _make_mgr()
        result = mgr._wee_execute_tool("python", {"code": ""}, "orchestrator")
        self.assertIn("Error", result, "Empty code must return error")

    @patch("openai.OpenAI")
    def test_tool_fallback_when_not_supported(self, mock_openai_cls):
        """Issue #111: When tools not supported, retry without tools (no crash)."""
        mgr = _make_mgr()
        sid = "test_111_fallback"
        session_data = {"runtime": "wee", "model": "ollama/qwen3:8b", "channel": "api"}

        call_count = [0]
        def mock_create(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1 and "tools" in kwargs:
                raise Exception("tools parameter is not supported")
            return iter([_make_text_chunk("Fallback response")])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_create
        mock_openai_cls.return_value = mock_client

        with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
            with patch.object(mgr, "build_agent_context_prompt", return_value="sys"):
                with patch.object(mgr, "load_session_map", return_value={sid: session_data}):
                    with patch.object(mgr, "save_session_map"):
                        result = mgr.run_wee_native(
                            prompt="test",
                            model="ollama/qwen3:8b",
                            agent="orchestrator",
                            session_id=None,
                            resume=False,
                            n8n_session_id=sid,
                            timeout=30,
                        )

        self.assertIn("Fallback response", result, "Must return response even when tools not supported")

    @patch("openai.OpenAI")
    def test_multi_round_tool_calls(self, mock_openai_cls):
        """Issue #111: Model can issue multiple rounds of tool calls."""
        mgr = _make_mgr()
        sid = "test_111_multi_round"
        session_data = {"runtime": "wee", "model": "ollama/qwen3:8b", "channel": "api"}

        call_count = [0]
        def mock_create(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return iter(_make_tool_call_chunks("tc1", "bash", json.dumps({"command": "whoami"})))
            elif call_count[0] == 2:
                return iter(_make_tool_call_chunks("tc2", "bash", json.dumps({"command": "hostname"})))
            return iter([_make_text_chunk("You are root on myhost")])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_create
        mock_openai_cls.return_value = mock_client

        tool_calls = []
        def mock_execute(fn, args, agent):
            tool_calls.append(fn)
            if "whoami" in args.get("command", ""):
                return "root"
            return "myhost"

        with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
            with patch.object(mgr, "build_agent_context_prompt", return_value="sys"):
                with patch.object(mgr, "load_session_map", return_value={sid: session_data}):
                    with patch.object(mgr, "save_session_map"):
                        with patch.object(mgr, "_wee_execute_tool", side_effect=mock_execute):
                            result = mgr.run_wee_native(
                                prompt="Who am I and what host?",
                                model="ollama/qwen3:8b",
                                agent="orchestrator",
                                session_id=None,
                                resume=False,
                                n8n_session_id=sid,
                                timeout=30,
                            )

        self.assertEqual(len(tool_calls), 2, "Two tool calls should be made in two rounds")

    @patch("openai.OpenAI")
    def test_tool_results_in_conversation_history(self, mock_openai_cls):
        """Issue #111: Tool results are appended to messages for context persistence."""
        mgr = _make_mgr()
        sid = "test_111_history"
        session_data = {"runtime": "wee", "model": "ollama/qwen3:8b", "channel": "api"}
        mgr.session_map[sid] = session_data

        call_count = [0]
        captured_messages = []
        def mock_create(**kwargs):
            call_count[0] += 1
            captured_messages.append(list(kwargs.get("messages", [])))
            if call_count[0] == 1:
                return iter(_make_tool_call_chunks("tc1", "bash", json.dumps({"command": "ls"})))
            return iter([_make_text_chunk("done")])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_create
        mock_openai_cls.return_value = mock_client

        with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
            with patch.object(mgr, "build_agent_context_prompt", return_value="sys"):
                with patch.object(mgr, "load_session_map", return_value={sid: session_data}):
                    with patch.object(mgr, "save_session_map"):
                        with patch.object(mgr, "_wee_execute_tool", return_value="file1\nfile2"):
                            mgr.run_wee_native(
                                prompt="ls",
                                model="ollama/qwen3:8b",
                                agent="orchestrator",
                                session_id=None,
                                resume=False,
                                n8n_session_id=sid,
                                timeout=30,
                            )

        # Second API call should have tool result in messages
        self.assertGreater(len(captured_messages), 1, "Must have at least 2 API calls")
        second_call_msgs = captured_messages[1]
        tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
        self.assertGreater(len(tool_msgs), 0, "Second call must include tool result message")
        self.assertIn("file1", tool_msgs[0]["content"], "Tool result must contain execution output")


class TestIssue111SystemPrompt(unittest.TestCase):
    """Issue #111: System prompt completeness checks."""

    def test_augmented_prompt_includes_critical_directive(self):
        """Issue #111: Augmented system prompt must include CRITICAL about not refusing."""
        mgr = _make_mgr()
        result = mgr._wee_augment_system_prompt_with_tools("base")
        self.assertIn("CRITICAL", result, "Must include CRITICAL directive")
        self.assertIn("NEVER refuse", result, "Must tell model to never refuse tool use")

    def test_anti_hallucination_prompt_includes_key_rules(self):
        """Issue #111: Anti-hallucination prompt covers all key rules."""
        result = SessionManager._wee_anti_hallucination_prompt()
        self.assertIn("fabricate", result.lower(), "Must forbid fabrication")
        self.assertIn("placeholder", result.lower(), "Must forbid placeholder output")
        self.assertIn("error", result.lower(), "Must require relaying errors verbatim")
        self.assertIn("StrictHostKeyChecking", result, "Must mention SSH flag requirement")

    @patch("openai.OpenAI")
    def test_full_system_prompt_includes_both_tool_and_anti_hallucination(self, mock_openai_cls):
        """Issue #111: Combined system prompt has tool declaration + anti-hallucination."""
        mgr = _make_mgr()
        sid = "test_111_full_prompt"
        session_data = {"runtime": "wee", "model": "ollama/qwen3:8b", "channel": "api"}

        captured_messages = []
        def mock_create(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return iter([_make_text_chunk("ok")])

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_create
        mock_openai_cls.return_value = mock_client

        with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
            with patch.object(mgr, "build_agent_context_prompt", return_value="You are Wee."):
                with patch.object(mgr, "load_session_map", return_value={sid: session_data}):
                    with patch.object(mgr, "save_session_map"):
                        mgr.run_wee_native(
                            prompt="hi",
                            model="ollama/qwen3:8b",
                            agent="orchestrator",
                            session_id=None,
                            resume=False,
                            n8n_session_id=sid,
                            timeout=30,
                        )

        sys_msgs = [m for m in captured_messages if m.get("role") == "system"]
        self.assertGreater(len(sys_msgs), 0, "Must have system message")
        sys_content = sys_msgs[0]["content"]
        # Tool declaration
        self.assertIn("bash", sys_content.lower(), "System must declare bash tool")
        self.assertIn("python", sys_content.lower(), "System must declare python tool")
        # Anti-hallucination
        self.assertIn("fabricate", sys_content.lower(), "System must include anti-hallucination rules")
        # Original context preserved
        self.assertIn("You are Wee.", sys_content, "Original context must be preserved")


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
        mock_client.chat.completions.create.return_value = iter([_make_text_chunk("ok")])
        mock_openai_cls.return_value = mock_client

        with patch.object(mgr, "get_or_create_session_data", return_value=session_data):
            with patch.object(mgr, "build_agent_context_prompt", side_effect=capture_context):
                with patch.object(mgr, "load_session_map", return_value={sid: session_data}):
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
        self.assertEqual(kwargs.get("runtime"), "wee",
                         "build_agent_context_prompt must be called with runtime='wee'")

    def test_load_agent_skills_returns_skills_when_present(self):
        """Issue #111: load_agent_skills returns skill context for agents with skills."""
        mgr = _make_mgr()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / ".github" / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text("---\nname: test-skill\ndescription: A test skill\n---\n# Test Skill\n")

            result = mgr.load_agent_skills(tmpdir)
            self.assertIn("test-skill", result, "Skills context must include skill name")
            self.assertIn("A test skill", result, "Skills context must include description")

    def test_load_agent_skills_returns_no_skill_entries_when_no_skills(self):
        """Issue #111: load_agent_skills returns no skill entries when no skills directory."""
        mgr = _make_mgr()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            result = mgr.load_agent_skills(tmpdir)
            self.assertNotIn("[Agent Skills - Available]", result, "No skills means no Available Skills section")


if __name__ == "__main__":
    unittest.main()
