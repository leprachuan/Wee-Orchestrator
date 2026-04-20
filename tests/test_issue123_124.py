"""
Tests for Issues #123 and #124:
  #123: wee runtime tool calling agentic loop works end-to-end
  #124: /api/v1/models returns live Ollama models from kubuntu (not hardcoded 3)
"""

import json
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


def make_manager():
    from agent_manager import SessionManager
    return SessionManager("/opt/n8n-copilot-shim-dev/agents.json")


# ---------------------------------------------------------------------------
# Issue #124 — Live Ollama model discovery
# ---------------------------------------------------------------------------

class TestLiveOllamaDiscovery(unittest.TestCase):

    def test_fetch_ollama_live_returns_list(self):
        """_fetch_ollama_models_live returns a list (possibly empty if offline)."""
        mgr = make_manager()
        result = mgr._fetch_ollama_models_live()
        self.assertIsInstance(result, list)

    def test_fetch_ollama_live_respects_60s_ttl(self):
        """Repeated calls within 60s return cached result without hitting network."""
        mgr = make_manager()
        # Prime cache
        mgr._ollama_models_cache = ["gemma4:e4b", "qwen3:8b"]
        mgr._ollama_cache_ts = time.time()  # just set — within TTL
        result = mgr._fetch_ollama_models_live()
        self.assertEqual(result, ["gemma4:e4b", "qwen3:8b"])

    def test_fetch_ollama_live_refreshes_after_ttl(self):
        """Cache is refreshed after 60s TTL expires."""
        mgr = make_manager()
        mgr._ollama_models_cache = ["old_model"]
        mgr._ollama_cache_ts = time.time() - 61  # expired

        fake_tags = json.dumps({"models": [{"name": "gemma4:e4b"}, {"name": "qwen3:8b"}]}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_tags

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = mgr._fetch_ollama_models_live()

        self.assertIn("gemma4:e4b", result)
        self.assertIn("qwen3:8b", result)
        self.assertNotIn("old_model", result)

    def test_fetch_ollama_live_returns_stale_cache_on_error(self):
        """Returns stale cache on network error instead of crashing."""
        mgr = make_manager()
        mgr._ollama_models_cache = ["gemma4:e4b"]
        mgr._ollama_cache_ts = time.time() - 120  # expired

        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            result = mgr._fetch_ollama_models_live()

        self.assertEqual(result, ["gemma4:e4b"])

    def test_fetch_wee_models_includes_live_ollama(self):
        """fetch_wee_models() includes live Ollama models under 'Wee Native (Ollama)' group."""
        mgr = make_manager()
        # Inject fake Ollama discovery
        mgr._ollama_models_cache = ["gemma4:e4b", "qwen3:8b", "mistral:7b"]
        mgr._ollama_cache_ts = time.time()
        # Force OpenRouter cache miss so fetch rebuilds result
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0

        fake_tags = json.dumps({"models": []}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_tags

        # Mock OpenRouter to fail to test fallback path
        with patch("urllib.request.urlopen", side_effect=Exception("no openrouter")):
            result = mgr.fetch_wee_models()

        ollama_group = result.get("Wee Native (Ollama)", [])
        self.assertIn("ollama/gemma4:e4b", ollama_group)
        self.assertIn("ollama/qwen3:8b", ollama_group)
        self.assertIn("ollama/mistral:7b", ollama_group)

    def test_fetch_wee_models_ollama_format(self):
        """All live Ollama models are formatted as 'ollama/<name>'."""
        mgr = make_manager()
        mgr._ollama_models_cache = ["gemma4:latest", "qwen3.5:latest"]
        mgr._ollama_cache_ts = time.time()
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0

        with patch("urllib.request.urlopen", side_effect=Exception("no openrouter")):
            result = mgr.fetch_wee_models()

        ollama_group = result.get("Wee Native (Ollama)", [])
        for mid in ollama_group:
            self.assertTrue(mid.startswith("ollama/"), f"{mid!r} should start with 'ollama/'")

    def test_fetch_wee_models_fallback_on_ollama_failure(self):
        """Falls back to static WEE_MODELS Ollama entries when live discovery fails."""
        mgr = make_manager()
        mgr._ollama_models_cache = []
        mgr._ollama_cache_ts = 0
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0

        with patch("urllib.request.urlopen", side_effect=Exception("offline")):
            result = mgr.fetch_wee_models()

        # Should still have the static Ollama entries
        ollama_group = result.get("Wee Native (Ollama)", [])
        self.assertGreater(len(ollama_group), 0)

    def test_fetch_wee_models_openrouter_group_preserved(self):
        """OpenRouter group is preserved even when Ollama discovery succeeds."""
        mgr = make_manager()
        mgr._ollama_models_cache = ["gemma4:e4b"]
        mgr._ollama_cache_ts = time.time()
        mgr._env_wee_models = None
        mgr._openrouter_cache_ts = 0

        with patch("urllib.request.urlopen", side_effect=Exception("no openrouter")):
            result = mgr.fetch_wee_models()

        # OpenRouter group should still exist (static fallback)
        or_groups = [k for k in result.keys() if "OpenRouter" in k]
        self.assertGreater(len(or_groups), 0)

    def test_fetch_wee_models_cache_valid_ollama_refreshed(self):
        """When OR cache is valid, Ollama section is still refreshed if its TTL expired."""
        mgr = make_manager()
        old_entries = [("ollama/old_model", "Old Model", [])]
        mgr._env_wee_models = {
            "Wee Native (Ollama)": old_entries,
            "Wee Native (OpenRouter)": [],
        }
        mgr._openrouter_cache_ts = time.time()  # OR cache valid
        mgr._ollama_cache_ts = time.time() - 70  # Ollama TTL expired
        mgr._ollama_models_cache = []

        fake_tags = json.dumps({"models": [{"name": "gemma4:e4b"}, {"name": "qwen3:8b"}]}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_tags

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = mgr.fetch_wee_models()

        ollama_group = result.get("Wee Native (Ollama)", [])
        self.assertIn("ollama/gemma4:e4b", ollama_group)
        self.assertNotIn("ollama/old_model", ollama_group)

    def test_get_models_for_runtime_wee_uses_fetch_wee(self):
        """get_models_for_runtime('wee') delegates to fetch_wee_models."""
        mgr = make_manager()
        mock_models = {"Wee Native (Ollama)": ["ollama/gemma4:e4b"]}
        with patch.object(mgr, "fetch_wee_models", return_value=mock_models) as mock_fn:
            result = mgr.get_models_for_runtime("wee")
        mock_fn.assert_called_once()
        self.assertEqual(result, mock_models)

    def test_api_models_wee_returns_flat_ids(self):
        """/api/v1/models?runtime=wee returns flat model ID strings (not tuples)."""
        mgr = make_manager()
        # Inject known models
        mgr._ollama_models_cache = ["gemma4:e4b"]
        mgr._ollama_cache_ts = time.time()
        with patch.object(mgr, "_fetch_ollama_models_live", return_value=["gemma4:e4b"]):
            with patch("urllib.request.urlopen", side_effect=Exception("no or")):
                models = mgr.get_models_for_runtime("wee")

        ollama = models.get("Wee Native (Ollama)", [])
        self.assertTrue(len(ollama) > 0)
        for m in ollama:
            self.assertIsInstance(m, str, f"Expected str, got {type(m)}: {m!r}")


# ---------------------------------------------------------------------------
# Issue #123 — Tool call agentic loop
# ---------------------------------------------------------------------------

class TestWeeRuntimeToolLoop(unittest.TestCase):

    def test_run_wee_native_exists(self):
        """run_wee_native method exists on SessionManager."""
        mgr = make_manager()
        self.assertTrue(hasattr(mgr, "run_wee_native"))
        self.assertTrue(callable(mgr.run_wee_native))

    def test_wee_execute_tool_bash(self):
        """_wee_execute_tool('bash', ...) executes a shell command and returns output."""
        mgr = make_manager()
        result = mgr._wee_execute_tool("bash", {"command": "echo 'test123'"}, "orchestrator")
        self.assertIn("test123", result)

    def test_wee_execute_tool_unknown(self):
        """_wee_execute_tool returns error for unknown tool names."""
        mgr = make_manager()
        result = mgr._wee_execute_tool("unknown_tool", {}, "orchestrator")
        self.assertIn("Error", result)
        self.assertIn("unknown_tool", result)

    def test_wee_execute_tool_python(self):
        """_wee_execute_tool('python', ...) executes python code and returns output."""
        mgr = make_manager()
        result = mgr._wee_execute_tool("python", {"code": "print('hello_py')"}, "orchestrator")
        self.assertIn("hello_py", result)

    def _make_streaming_mock(self, tool_calls_chunks=None, content_chunks=None):
        """Build a mock OpenAI streaming response."""
        chunks = []

        if tool_calls_chunks:
            # First chunk: tool call delta
            for i, tc in enumerate(tool_calls_chunks):
                delta = MagicMock()
                delta.content = None
                tc_delta = MagicMock()
                tc_delta.index = 0
                tc_delta.id = tc.get("id", f"tc_{i}")
                tc_delta.function = MagicMock()
                tc_delta.function.name = tc.get("name", "bash")
                tc_delta.function.arguments = tc.get("arguments", '{"command":"echo hi"}')
                delta.tool_calls = [tc_delta]
                choice = MagicMock()
                choice.delta = delta
                chunk = MagicMock()
                chunk.choices = [choice]
                chunks.append(chunk)

        # Final content chunk (after tool execution)
        if content_chunks:
            for text in content_chunks:
                delta = MagicMock()
                delta.content = text
                delta.tool_calls = None
                choice = MagicMock()
                choice.delta = delta
                chunk = MagicMock()
                chunk.choices = [choice]
                chunks.append(chunk)

        return iter(chunks)

    def test_tool_calls_loop_detected_in_run_wee_native(self):
        """When model returns tool_calls, run_wee_native executes them and loops."""
        mgr = make_manager()
        n8n_sid = "test-sid-123"
        mgr.get_or_create_session_data(n8n_sid)
        mgr.update_session_field(n8n_sid, "runtime", "wee")
        mgr.update_session_field(n8n_sid, "model", "ollama/gemma4:e4b")
        mgr.update_session_field(n8n_sid, "channel", "webui")

        # Round 1: model calls bash
        round1_chunks = self._make_streaming_mock(
            tool_calls_chunks=[{"id": "tc_1", "name": "bash", "arguments": '{"command":"echo hello"}'}]
        )
        # Round 2: model returns final text
        round2_chunks = self._make_streaming_mock(
            content_chunks=["Disk usage is fine."]
        )

        call_count = [0]
        def mock_create(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return round1_chunks
            return round2_chunks

        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.side_effect = mock_create

            result = mgr.run_wee_native(
                prompt="check disk usage",
                model="ollama/gemma4:e4b",
                agent="orchestrator",
                session_id=None,
                resume=False,
                n8n_session_id=n8n_sid,
            )

        self.assertIn("Disk usage is fine.", result)
        self.assertEqual(call_count[0], 2, "Expected exactly 2 LLM calls (1 tool round + 1 final)")

    def test_tools_passed_to_llm(self):
        """run_wee_native passes 'tools' parameter to the LLM on first call."""
        mgr = make_manager()
        n8n_sid = "test-sid-tools"
        mgr.get_or_create_session_data(n8n_sid)
        mgr.update_session_field(n8n_sid, "runtime", "wee")
        mgr.update_session_field(n8n_sid, "model", "ollama/gemma4:e4b")
        mgr.update_session_field(n8n_sid, "channel", "webui")

        content_chunks = self._make_streaming_mock(content_chunks=["Hi there."])

        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = content_chunks

            mgr.run_wee_native(
                prompt="hello",
                model="ollama/gemma4:e4b",
                agent="orchestrator",
                session_id=None,
                resume=False,
                n8n_session_id=n8n_sid,
            )

        call_kwargs = mock_client.chat.completions.create.call_args
        self.assertIn("tools", call_kwargs.kwargs)
        tools = call_kwargs.kwargs["tools"]
        tool_names = [t["function"]["name"] for t in tools]
        self.assertIn("bash", tool_names)
        self.assertIn("python", tool_names)

    def test_no_tool_calls_returns_content(self):
        """When model returns plain text with no tool calls, that text is returned."""
        mgr = make_manager()
        n8n_sid = "test-sid-notools"
        mgr.get_or_create_session_data(n8n_sid)
        mgr.update_session_field(n8n_sid, "runtime", "wee")
        mgr.update_session_field(n8n_sid, "model", "ollama/gemma4:e4b")
        mgr.update_session_field(n8n_sid, "channel", "webui")

        chunks = self._make_streaming_mock(content_chunks=["The answer is 42."])

        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = chunks

            result = mgr.run_wee_native(
                prompt="what is 6 times 7",
                model="ollama/gemma4:e4b",
                agent="orchestrator",
                session_id=None,
                resume=False,
                n8n_session_id=n8n_sid,
            )

        self.assertEqual(result, "The answer is 42.")

    def test_augment_system_prompt_includes_tools(self):
        """_wee_augment_system_prompt_with_tools injects bash/python tool docs."""
        mgr = make_manager()
        result = mgr._wee_augment_system_prompt_with_tools("You are a helpful AI.")
        self.assertIn("bash", result)
        self.assertIn("python", result)
        self.assertIn("[Available Tools]", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
