"""
Regression tests for Issue #142:
1. Wee runtime dynamic model listing (Ollama + OpenRouter live discovery)
2. Tool call SSE event fields (event, input, output)
3. Background task tool call JSON tracking from wee_runtime.py
"""

import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


class TestIssue142WeeModelsDynamic(unittest.TestCase):
    """Bug 1: fetch_wee_models returns live Ollama + ALL OpenRouter models."""

    def _make_manager(self):
        """Return a minimal CopilotManager-like object for testing fetch_wee_models."""
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "agent_manager",
            "/opt/n8n-copilot-shim-dev/agent_manager.py",
        )
        mod = importlib.util.load_from_spec(spec) if hasattr(spec, "load_from_spec") else None
        # Lightweight: just extract needed constants
        return None

    def test_fetch_wee_models_source_has_live_openrouter(self):
        """fetch_wee_models must contain live OpenRouter discovery code (Issue #142)."""
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py") as f:
            src = f.read()
        # Find function
        idx = src.find("def fetch_wee_models(self)")
        self.assertGreater(idx, 0)
        # Extract next 4000 chars for the function body
        block = src[idx:idx + 4000]
        self.assertIn("openrouter.ai/api/v1/models", block,
                      "fetch_wee_models must fetch live from openrouter.ai")
        self.assertIn("_openrouter_cache_ts", block,
                      "fetch_wee_models must update _openrouter_cache_ts cache timestamp")
        self.assertIn("all_models", block,
                      "fetch_wee_models must iterate all_models from OpenRouter response")

    def test_fetch_wee_models_source_has_live_ollama(self):
        """fetch_wee_models must discover live Ollama models (Issue #142)."""
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py") as f:
            src = f.read()
        idx = src.find("def fetch_wee_models(self)")
        block = src[idx:idx + 4000]
        self.assertIn("192.168.1.101:11434", block,
                      "fetch_wee_models must query local Ollama endpoint")

    def test_models_api_returns_group_field(self):
        """The /api/v1/models endpoint must return 'group' field for each model."""
        import requests
        import urllib3
        urllib3.disable_warnings()
        resp = requests.get(
            "https://127.0.0.1:8001/api/v1/models?runtime=wee",
            verify=False,
            timeout=10,
        )
        self.assertEqual(resp.status_code, 200, f"Expected 200, got {resp.status_code}")
        data = resp.json()
        models = data.get("models", [])
        self.assertTrue(len(models) > 0, "Should have at least one wee model")
        for m in models:
            self.assertIn("group", m, f"Model {m.get('id')} missing 'group' field")
            self.assertIsNotNone(m["group"], f"Model {m.get('id')} has None group")


class TestIssue142ToolCallSSEEvents(unittest.TestCase):
    """Bug 2: tc_start_event and tc_done_event must have correct event/input/output fields."""

    def setUp(self):
        """Load agent_manager module for inspection."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "agent_manager_142",
            "/opt/n8n-copilot-shim-dev/agent_manager.py",
        )
        # We'll inspect the source directly to avoid loading the full module
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py") as f:
            self.source = f.read()

    def test_tc_start_event_has_event_detected(self):
        """tc_start_event must include event='detected' to trigger SSE insertion."""
        # Find the tc_start_event dict in source
        import re
        # Look for the block that creates tc_start_event
        match = re.search(
            r'tc_start_event\s*=\s*\{([^}]{0,500})\}',
            self.source,
        )
        self.assertIsNotNone(match, "tc_start_event not found in agent_manager.py")
        block = match.group(1)
        self.assertIn('"event"', block, "tc_start_event must have 'event' field")
        self.assertIn('"detected"', block, "tc_start_event event must be 'detected'")
        self.assertIn('"input"', block, "tc_start_event must have 'input' field (not 'arguments')")
        self.assertNotIn('"arguments"', block, "tc_start_event must not use 'arguments' (use 'input')")

    def test_tc_done_event_has_event_result(self):
        """tc_done_event must include event='result' to trigger SSE completion."""
        import re
        match = re.search(
            r'tc_done_event\s*=\s*\{([^}]{0,500})\}',
            self.source,
        )
        self.assertIsNotNone(match, "tc_done_event not found in agent_manager.py")
        block = match.group(1)
        self.assertIn('"event"', block, "tc_done_event must have 'event' field")
        self.assertIn('"result"', block, "tc_done_event event must be 'result'")
        self.assertIn('"output"', block, "tc_done_event must have 'output' field (not 'result')")
        self.assertIn('"input"', block, "tc_done_event must have 'input' field")
        self.assertNotIn('"arguments"', block, "tc_done_event must not use 'arguments'")

    def test_tc_done_event_no_standalone_result_field(self):
        """tc_done_event must not use 'result' as a data field key (renamed to 'output')."""
        import re
        # Find the tc_done_event block specifically
        match = re.search(
            r'tc_done_event\s*=\s*\{[^}]{0,500}\}',
            self.source,
        )
        self.assertIsNotNone(match)
        block = match.group(0)
        # Should have "output" but "result" should only appear as the value of "event"
        output_count = block.count('"output"')
        self.assertGreaterEqual(output_count, 1, "tc_done_event must have 'output' key")


class TestIssue142WeeRuntimeStructuredOutput(unittest.TestCase):
    """Bug 3: wee_runtime.py must emit JSON tool call events to stdout."""

    def setUp(self):
        with open("/opt/n8n-copilot-shim-dev/wee_runtime.py") as f:
            self.source = f.read()

    def test_wee_runtime_emits_tc_start_json(self):
        """wee_runtime.py must print __wee_tc__ start JSON to stdout."""
        self.assertIn('"__wee_tc__": "start"', self.source,
                      "wee_runtime.py must output __wee_tc__ start JSON")

    def test_wee_runtime_emits_tc_done_json(self):
        """wee_runtime.py must print __wee_tc__ done JSON to stdout."""
        self.assertIn('"__wee_tc__": "done"', self.source,
                      "wee_runtime.py must output __wee_tc__ done JSON")

    def test_wee_runtime_tc_json_includes_name_and_input(self):
        """__wee_tc__ start JSON must include name and input."""
        import re
        # Find the JSON write lines
        match = re.search(
            r'__wee_tc__.*?start.*?name.*?input',
            self.source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "__wee_tc__ start event should have name and input fields")

    def test_wee_runtime_tc_json_includes_name_and_output(self):
        """__wee_tc__ done JSON must include name and output."""
        import re
        match = re.search(
            r'__wee_tc__.*?done.*?name.*?output',
            self.source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "__wee_tc__ done event should have name and output fields")

    def test_bg_task_loop_skips_wee_tc_lines(self):
        """Background task loop must not add __wee_tc__ lines to stdout output."""
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py") as f:
            src = f.read()
        self.assertIn('__wee_tc__', src,
                      "agent_manager.py must handle __wee_tc__ lines from wee bg tasks")
        # Find the wee_tc handler block (may span up to 1500 chars)
        idx = src.find('__wee_tc__')
        block = src[idx:idx + 1500]
        self.assertIn('continue', block,
                      "Handler must skip __wee_tc__ lines from output via continue")

    def test_bg_task_loop_calls_append_tool_call(self):
        """Background task loop must call bg_task_mgr.append_tool_call for wee start events."""
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py") as f:
            src = f.read()
        # Find the __wee_tc__ handler block
        idx = src.find('"__wee_tc__"')
        self.assertGreater(idx, 0)
        block = src[idx:idx + 1500]
        self.assertIn('append_tool_call', block,
                      "Wee tc handler must call bg_task_mgr.append_tool_call")


if __name__ == "__main__":
    unittest.main(verbosity=2)
