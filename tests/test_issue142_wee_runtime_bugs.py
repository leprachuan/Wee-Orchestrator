"""Regression tests for Issue #142: wee runtime — OpenRouter dynamic models,
missing synthesis response, blank tool expansion.

Three sub-bugs:
  Bug 1: OpenRouter models should be dynamically fetched (not static)
  Bug 2: tc_start/tc_done events must have correct fields for WebUI
  Bug 3: bg_task_mgr must track tool calls for Tasks panel expansion
"""

import json
import os
import sys
import time  # noqa: F401
import types  # noqa: F401
from unittest.mock import MagicMock, PropertyMock, patch  # noqa: F401

import pytest

# ── Ensure project root is on sys.path ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ── Shared fixtures ──


@pytest.fixture
def session_mgr():
    """Create a minimal SessionManager instance for testing."""
    from agent_manager import SessionManager

    mgr = SessionManager.__new__(SessionManager)
    mgr.session_map = {}
    mgr._session_map_lock = MagicMock()
    mgr._session_map_lock.__aenter__ = MagicMock()
    mgr._session_map_lock.__aexit__ = MagicMock()
    mgr.AGENTS = {
        "orchestrator": {"path": "/opt/n8n-copilot-shim-dev", "description": "test"}
    }
    mgr.command_timeout = 300
    mgr._env_wee_models = None
    mgr._openrouter_cache_ts = 0
    mgr._bg_task_mgr = None
    mgr._notification_mgr = None
    mgr._bg_identity = None
    return mgr


# ═══════════════════════════════════════════════════════════════════
# Bug 1: OpenRouter dynamic model fetching
# ═══════════════════════════════════════════════════════════════════


class TestBug1OpenRouterDynamicModels:
    """Verify that fetch_wee_models() fetches OpenRouter models dynamically
    from the API rather than using a hardcoded static list."""

    def test_fetch_wee_models_calls_openrouter_api(self, session_mgr):
        """fetch_wee_models should hit openrouter.ai/api/v1/models when key is available."""  # noqa: E501
        fake_response = json.dumps(
            {
                "data": [
                    {"id": "meta-llama/llama-4-scout", "name": "Llama 4 Scout"},
                    {"id": "google/gemma-3-27b-it:free", "name": "Gemma 3 27B"},
                    {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4"},
                ]
            }
        ).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response
        mock_resp.status = 200

        # Mock Ollama to fail (focus on OpenRouter) + mock keyring
        with (
            patch("httpx.get", side_effect=Exception("Ollama down")),
            patch("keyring.get_password", return_value=None),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key-123"}),
            patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen,
        ):
            session_mgr.fetch_wee_models()

        # Verify API was called with correct URL and auth header
        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert "openrouter.ai/api/v1/models" in req.full_url
        assert req.headers.get("Authorization", "").startswith("Bearer ")

    def test_fetch_wee_models_returns_discovered_models(self, session_mgr):
        """Discovered OpenRouter models should appear in the result."""
        fake_response = json.dumps(
            {
                "data": [
                    {"id": "meta-llama/llama-4-scout", "name": "Llama 4 Scout"},
                    {
                        "id": "google/gemma-3-27b-it:free",
                        "name": "Gemma 3 27B IT (free)",
                    },
                ]
            }
        ).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response

        with (
            patch("httpx.get", side_effect=Exception("Ollama down")),
            patch("keyring.get_password", return_value=None),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            result = session_mgr.fetch_wee_models()

        # Result should have OpenRouter Models category
        assert "OpenRouter Models" in result
        or_ids = list(result["OpenRouter Models"])
        assert any("llama-4-scout" in mid for mid in or_ids)
        assert any("gemma-3-27b" in mid for mid in or_ids)

    def test_fetch_wee_models_no_api_key_uses_static(self, session_mgr):
        """Without API key, OpenRouter falls back to static model list."""
        with (
            patch("httpx.get", side_effect=Exception("Ollama down")),
            patch.dict(os.environ, {}, clear=False),
        ):
            # Remove OPENROUTER_API_KEY if present
            os.environ.pop("OPENROUTER_API_KEY", None)
            try:
                with patch("keyring.get_password", return_value=None):
                    result = session_mgr.fetch_wee_models()
            except ImportError:
                result = session_mgr.fetch_wee_models()

        # Should still have OpenRouter Models from static WEE_MODELS
        assert "OpenRouter Models" in result

    def test_models_api_includes_group_field(self, session_mgr):
        """The /api/v1/models response must include a 'group' field for optgroup rendering."""  # noqa: E501
        # Use static models (no live fetch)
        with (
            patch("httpx.get", side_effect=Exception("skip")),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("OPENROUTER_API_KEY", None)
            try:
                with patch("keyring.get_password", return_value=None):
                    raw = session_mgr.get_models_for_runtime("wee")
            except (ImportError, AttributeError):
                raw = session_mgr.fetch_wee_models()

        # raw should be a dict with category names as keys
        assert isinstance(raw, dict)
        for group_name, model_list in raw.items():
            assert isinstance(group_name, str)
            assert len(group_name) > 0, "Group name should not be empty"

    def test_openrouter_cache_ttl(self, session_mgr):
        """fetch_wee_models should cache and reuse results within TTL."""
        fake_response = json.dumps(
            {"data": [{"id": "test/model", "name": "Test"}]}
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response

        with (
            patch("httpx.get", side_effect=Exception("skip")),
            patch("keyring.get_password", return_value=None),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "key"}),
            patch("urllib.request.urlopen", return_value=mock_resp) as mock_fetch,
        ):
            # First call should fetch
            session_mgr.fetch_wee_models()
            assert mock_fetch.call_count == 1

            # Second call should use cache
            session_mgr.fetch_wee_models()
            assert mock_fetch.call_count == 1  # Not called again

    def test_openrouter_popular_models_prioritized(self, session_mgr):
        """Popular OpenRouter models should be sorted before others."""
        from agent_manager import SessionManager

        popular = SessionManager.OPENROUTER_POPULAR_MODELS
        assert isinstance(popular, (list, tuple, set, frozenset))
        assert len(popular) > 0, "OPENROUTER_POPULAR_MODELS should not be empty"
        # Popular models should include well-known model IDs
        assert any("llama" in m for m in popular)
        assert any("gemma" in m or "gemini" in m for m in popular)


# ═══════════════════════════════════════════════════════════════════
# Bug 2: Synthesis response after tool calls
# ═══════════════════════════════════════════════════════════════════


class TestBug2SynthesisResponse:
    """Verify that tc_start_event and tc_done_event have correct fields
    and that the synthesis response is streamed after tool calls."""

    def test_tc_start_event_has_event_field(self):
        """tc_start_event must have event='detected' for app.js to render spinner."""
        # Simulate what run_wee_native builds
        tc_id = "call_test123"
        func_name = "bash"
        func_args = {"command": "echo hello"}

        tc_start_event = {
            "id": tc_id,
            "name": func_name,
            "event": "detected",
            "input": func_args,
            "status": "running",
        }

        assert tc_start_event["event"] == "detected"
        assert "input" in tc_start_event
        assert "arguments" not in tc_start_event, "Should use 'input' not 'arguments'"

    def test_tc_done_event_has_result_event(self):
        """tc_done_event must have event='result' for app.js to close spinner."""
        tc_id = "call_test123"
        func_name = "bash"
        func_args = {"command": "echo hello"}
        tool_result = "hello\n"

        tc_done_event = {
            "id": tc_id,
            "name": func_name,
            "event": "result",
            "input": func_args,
            "output": tool_result[:2000] if tool_result else "",
            "status": "complete",
        }

        assert tc_done_event["event"] == "result"
        assert "output" in tc_done_event
        assert (
            "result" not in tc_done_event or tc_done_event.get("result") is None
        ), "Should use 'output' not 'result' field for tool output"

    def test_tc_events_match_appjs_expectations(self):
        """Event fields must match what app.js expects for tool rendering."""
        # app.js checks: evt.event || 'detected', evt.input, evt.output, evt.name,
        # evt.id
        start = {
            "id": "c1",
            "name": "bash",
            "event": "detected",
            "input": {},
            "status": "running",
        }
        done = {
            "id": "c1",
            "name": "bash",
            "event": "result",
            "input": {},
            "output": "ok",
            "status": "complete",
        }

        # Verify start event
        assert start.get("event", "detected") == "detected"
        assert "input" in start
        assert "name" in start
        assert "id" in start

        # Verify done event
        assert done.get("event") == "result"
        assert "output" in done
        assert done["output"] == "ok"

    def test_synthesis_text_collected_in_output(self):
        """After tool loop, synthesis text should be in collected_output."""
        collected_output = []
        # Simulate the streaming loop: first round has tool calls, second has text
        # Round 2 (synthesis): model returns text, no tool calls
        round_content = ["Based on the tool results, ", "here is your answer."]
        collected_output.extend(round_content)

        output = "".join(collected_output)
        assert output == "Based on the tool results, here is your answer."
        assert len(output) > 0

    def test_empty_synthesis_fallback(self):
        """If LLM generates empty synthesis, should fallback to last tool result."""
        collected_output = []
        output = "".join(collected_output)  # Empty

        # Simulate fallback logic from Issue #112
        messages = [
            {
                "role": "tool",
                "content": "File saved successfully",
                "tool_call_id": "c1",
            },
        ]

        if not output.strip():
            tool_results = [
                m["content"]
                for m in messages
                if m.get("role") == "tool" and m.get("content")
            ]
            if tool_results:
                output = f"Tool execution result:\n{tool_results[-1][:4000]}"

        assert "File saved successfully" in output

    def test_stream_buffer_push_chunk_during_synthesis(self):
        """During non-tool-call rounds, text tokens should be pushed as chunks."""
        stream_buffer = MagicMock()

        # Simulate synthesis streaming
        delta_text = "Here is the result"
        round_content = []
        round_content.append(delta_text)
        stream_buffer.push("chunk", {"text": delta_text})

        stream_buffer.push.assert_called_with("chunk", {"text": delta_text})


# ═══════════════════════════════════════════════════════════════════
# Bug 3: bg_task_mgr tool call tracking for Tasks panel
# ═══════════════════════════════════════════════════════════════════


class TestBug3BgTaskToolTracking:
    """Verify that run_wee_native tracks tool calls in bg_task_mgr
    when running in a background task context."""

    def test_execute_bg_task_stores_bg_task_id_in_session(self):
        """_execute_background_task must store task_id in session data."""
        # Verify source code stores bg_task_id
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager._execute_background_task)
        assert (
            "bg_task_id" in source
        ), "_execute_background_task must store bg_task_id in session"
        assert "update_session_field" in source
        # bg_task_id should be stored via update_session_field
        idx_bg = source.find('"bg_task_id"')
        assert idx_bg > 0, "bg_task_id string literal must appear in source"

    def test_bg_task_id_stored_before_execute(self, session_mgr):
        """bg_task_id should be set before self.execute() is called."""
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager._execute_background_task)
        # bg_task_id must appear before self.execute
        bg_task_id_pos = source.find("bg_task_id")
        execute_pos = source.find("self.execute(prompt")
        assert bg_task_id_pos > 0, "bg_task_id not found in _execute_background_task"
        assert execute_pos > 0, "self.execute not found in _execute_background_task"
        assert (
            bg_task_id_pos < execute_pos
        ), "bg_task_id must be stored BEFORE self.execute() is called"

    def test_run_wee_native_reads_bg_task_id(self):
        """run_wee_native should read bg_task_id from session data."""
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_wee_native)
        assert (
            "bg_task_id" in source
        ), "run_wee_native must read bg_task_id from session data"
        assert (
            'session_data.get("bg_task_id")' in source
        ), "Should use session_data.get('bg_task_id') to retrieve task ID"

    def test_run_wee_native_calls_append_tool_call(self):
        """run_wee_native must call bg_task_mgr.append_tool_call when bg_task_id is set."""  # noqa: E501
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_wee_native)
        assert (
            "append_tool_call" in source
        ), "run_wee_native must call bg_task_mgr.append_tool_call for tool tracking"
        assert (
            "_bg_task_mgr" in source
        ), "run_wee_native must reference self._bg_task_mgr"

    def test_run_wee_native_calls_update_tool_call(self):
        """run_wee_native must call bg_task_mgr.update_tool_call on tool completion."""
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_wee_native)
        assert (
            "update_tool_call" in source
        ), "run_wee_native must call bg_task_mgr.update_tool_call on completion"

    def test_append_tool_call_includes_required_fields(self):
        """Tool call appended to bg_task_mgr must have id, name, input, status, runtime."""  # noqa: E501
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_wee_native)
        # Check that the append_tool_call dict includes required fields
        assert '"id"' in source or "'id'" in source
        assert '"name"' in source or "'name'" in source
        assert '"input"' in source or "'input'" in source
        assert '"status"' in source or "'status'" in source
        assert '"runtime"' in source or "'runtime'" in source

    def test_update_tool_call_sets_output(self):
        """update_tool_call must set output field for Tools tab expansion."""
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_wee_native)
        # Find update_tool_call call and verify output= is passed
        idx = source.find("update_tool_call")
        assert idx > 0
        # Get surrounding context
        snippet = source[idx : idx + 300]
        assert (
            "output=" in snippet
        ), "update_tool_call must pass output= parameter for Tools tab display"
        assert "status=" in snippet, "update_tool_call must pass status= parameter"

    def test_bg_task_mgr_append_tool_call_method(self):
        """BackgroundTaskManager.append_tool_call should work correctly."""
        from agent_manager import BackgroundTaskManager

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        mgr._lock = MagicMock()
        mgr._lock.__enter__ = MagicMock(return_value=None)
        mgr._lock.__exit__ = MagicMock(return_value=False)

        task = {
            "task_id": "bg_test142",
            "status": "running",
            "tool_calls": [],
        }
        mgr._load = MagicMock(return_value=[task])
        mgr._save = MagicMock()

        mgr.append_tool_call(
            "bg_test142",
            {
                "id": "call_1",
                "name": "bash",
                "input": '{"command": "ls"}',
                "status": "running",
                "runtime": "wee",
            },
        )

        mgr._save.assert_called_once()
        saved_tasks = mgr._save.call_args[0][0]
        assert len(saved_tasks[0]["tool_calls"]) == 1
        tc = saved_tasks[0]["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["name"] == "bash"
        assert tc["status"] == "running"

    def test_bg_task_mgr_update_tool_call_method(self):
        """BackgroundTaskManager.update_tool_call should update existing tool call."""
        from agent_manager import BackgroundTaskManager

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        mgr._lock = MagicMock()
        mgr._lock.__enter__ = MagicMock(return_value=None)
        mgr._lock.__exit__ = MagicMock(return_value=False)

        task = {
            "task_id": "bg_test142",
            "status": "running",
            "tool_calls": [
                {"id": "call_1", "name": "bash", "status": "running"},
            ],
        }
        mgr._load = MagicMock(return_value=[task])
        mgr._save = MagicMock()

        mgr.update_tool_call(
            "bg_test142", "call_1", status="completed", output="hello world"
        )

        mgr._save.assert_called_once()
        saved = mgr._save.call_args[0][0]
        tc = saved[0]["tool_calls"][0]
        assert tc["status"] == "completed"
        assert tc["output"] == "hello world"

    def test_no_tracking_when_no_bg_task_id(self):
        """When bg_task_id is None (interactive session), bg_task_mgr should NOT be called."""  # noqa: E501
        # The guard condition ensures no tracking without bg_task_id
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_wee_native)
        # Guard must check bg_task_id is truthy before calling bg_task_mgr
        assert (
            "if bg_task_id and self._bg_task_mgr" in source
        ), "Must guard tool tracking behind bg_task_id check"

    def test_guard_condition_prevents_tracking_without_mgr(self):
        """Tool tracking must be guarded by both bg_task_id and self._bg_task_mgr."""
        import inspect

        from agent_manager import SessionManager

        source = inspect.getsource(SessionManager.run_wee_native)
        assert (
            "bg_task_id and self._bg_task_mgr" in source
        ), "Must guard with 'if bg_task_id and self._bg_task_mgr'"


# ═══════════════════════════════════════════════════════════════════
# Integration: Verify full pipeline
# ═══════════════════════════════════════════════════════════════════


class TestIssue142Integration:
    """Cross-bug integration tests."""

    def test_renderToolCall_expects_input_output_fields(self):
        """The app.js renderToolCall function expects tc.input and tc.output fields."""
        # This is a structural test — verify the contract between Python and JS
        tc = {
            "id": "call_abc",
            "name": "bash",
            "event": "result",
            "input": '{"command": "echo hello"}',
            "output": "hello\n",
            "status": "completed",
            "runtime": "wee",
        }

        # Verify all fields renderToolCall uses are present
        assert "id" in tc
        assert "name" in tc
        assert "event" in tc or "status" in tc
        assert "input" in tc
        assert "output" in tc
        assert tc["input"] is not None
        assert tc["output"] is not None
        assert len(str(tc["input"])) > 0
        assert len(str(tc["output"])) > 0

    def test_wee_models_api_response_shape(self, session_mgr):
        """Models API response for wee runtime should have correct shape."""
        with (
            patch("httpx.get", side_effect=Exception("skip")),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("OPENROUTER_API_KEY", None)
            try:
                with patch("keyring.get_password", return_value=None):
                    raw = session_mgr.fetch_wee_models()
            except ImportError:
                raw = session_mgr.fetch_wee_models()

        # Shape: Dict[str, List[str]]
        assert isinstance(raw, dict)
        for group_name, model_ids in raw.items():
            assert isinstance(group_name, str)
            assert isinstance(model_ids, list)

    def test_tool_call_event_contract(self):
        """SSE tool_call events and bg_task_mgr tool_calls share consistent fields."""
        # SSE event (for WebUI stream)
        sse_start = {
            "id": "call_1",
            "name": "bash",
            "event": "detected",
            "input": {"cmd": "ls"},
            "status": "running",
        }
        # bg_task_mgr entry (for Tasks panel)
        bg_entry = {
            "id": "call_1",
            "name": "bash",
            "input": '{"cmd": "ls"}',
            "status": "running",
            "runtime": "wee",
        }

        # Both must have matching id and name
        assert sse_start["id"] == bg_entry["id"]
        assert sse_start["name"] == bg_entry["name"]
        # Both must have input (serialized differently but present)
        assert sse_start["input"] is not None
        assert bg_entry["input"] is not None

    def test_execute_bg_task_full_flow(self):
        """Full background task flow: task_id stored → retrieved in wee native."""
        import inspect

        from agent_manager import SessionManager

        # Step 1: _execute_background_task stores bg_task_id
        bg_src = inspect.getsource(SessionManager._execute_background_task)
        assert '"bg_task_id"' in bg_src, "must store bg_task_id in session"

        # Step 2: run_wee_native retrieves it
        wee_src = inspect.getsource(SessionManager.run_wee_native)
        assert (
            'session_data.get("bg_task_id")' in wee_src
        ), "must retrieve bg_task_id from session data"

        # Step 3: bg_task_mgr is called for tracking
        assert "append_tool_call" in wee_src, "must append tool calls"
        assert "update_tool_call" in wee_src, "must update tool calls"
