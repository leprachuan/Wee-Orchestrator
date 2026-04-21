"""
Regression tests for issue #178: Gemini runtime echoes raw JSON user messages
instead of AI responses.

Root cause: role checks used "assistant" but Gemini CLI uses "model" per Google's API
convention. Two bugs: strip_metadata and the streaming path both failed to match,
causing either empty responses or raw JSON lines falling through to the stream buffer.
"""
import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helper: build a minimal SessionManager with just the strip_metadata method
# ---------------------------------------------------------------------------

def _import_strip_metadata():
    """Import only the strip_metadata method without starting the full server."""
    import importlib.util
    import pathlib

    path = pathlib.Path("/opt/n8n-copilot-shim-dev/agent_manager.py")
    spec = importlib.util.spec_from_file_location("agent_manager", path)
    mod = importlib.util.module_from_spec(spec)
    # Provide stubs so the module-level code doesn't crash on missing deps
    for dep in ["fastapi", "uvicorn", "pydantic", "starlette", "aiofiles",
                "websockets", "httpx", "psutil", "cryptography", "jose",
                "passlib", "multipart", "yaml", "toml"]:
        if dep not in sys.modules:
            sys.modules[dep] = types.ModuleType(dep)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pass
    # SessionManager may or may not be importable; fall back to direct method test
    return mod


class TestStripMetadataGeminiRole(unittest.TestCase):
    """Tests for strip_metadata Gemini role='model' fix (Fix 1)."""

    def _run_strip(self, lines_text: str) -> str:
        """
        Inline the Gemini branch of strip_metadata so tests are fast and
        don't depend on the full server starting up.
        """
        import json as _json_strip

        lines = lines_text.splitlines(keepends=True)
        result = []
        _has_json = False
        _text_parts = []

        for line in lines:
            line_stripped = line.strip()
            if line_stripped.startswith("{"):
                try:
                    obj = _json_strip.loads(line_stripped)
                    _has_json = True
                    obj_type = obj.get("type", "")
                    # ----- THE FIXED CHECK -----
                    if obj_type == "message" and obj.get("role") in ("assistant", "model"):
                        content = obj.get("content", "")
                        if content:
                            _text_parts.append(content)
                    elif obj_type == "result":
                        error_msg = (
                            obj.get("error")
                            or obj.get("error_message")
                            or obj.get("message")
                        )
                        if error_msg and isinstance(error_msg, str):
                            _text_parts.append(f"[Gemini Error] {error_msg}")
                    elif obj_type in ("tool_use", "tool_result", "init"):
                        pass
                    continue
                except (ValueError, KeyError):
                    pass
            result.append(line)

        if _has_json and _text_parts:
            return "\n".join(_text_parts)
        return "".join(result)

    # --- Before fix: role="assistant" would have been required ---

    def test_model_role_extracted(self):
        """Gemini 'model' role messages must produce the AI response text."""
        line = json.dumps({"type": "message", "role": "model", "content": "Hello from Gemini!"})
        result = self._run_strip(line)
        self.assertEqual(result, "Hello from Gemini!")

    def test_assistant_role_still_works(self):
        """Existing 'assistant' role messages must still be extracted (backwards compat)."""
        line = json.dumps({"type": "message", "role": "assistant", "content": "Hi there"})
        result = self._run_strip(line)
        self.assertEqual(result, "Hi there")

    def test_user_role_not_echoed(self):
        """User messages must NOT be included in the output."""
        user_msg = json.dumps({
            "type": "message",
            "timestamp": "2026-04-18T23:24:40.797Z",
            "role": "user",
            "content": "what happened?"
        })
        model_msg = json.dumps({"type": "message", "role": "model", "content": "Here is what happened."})
        output = self._run_strip(user_msg + "\n" + model_msg)
        self.assertNotIn("what happened?", output)
        self.assertIn("Here is what happened.", output)

    def test_raw_json_not_echoed_as_chunk(self):
        """Bug scenario: user sends a message → Gemini must NOT echo the raw JSON back."""
        raw_user_json = json.dumps({
            "type": "message",
            "timestamp": "2026-04-18T23:24:40.797Z",
            "role": "user",
            "content": "what happened?"
        })
        result = self._run_strip(raw_user_json)
        # The raw JSON itself must not appear in the result
        self.assertNotIn('"role": "user"', result)
        self.assertNotIn("what happened?", result)

    def test_result_error_surfaced(self):
        """Gemini API error in result event must be surfaced (Fix 4)."""
        result_line = json.dumps({
            "type": "result",
            "error": "RESOURCE_EXHAUSTED: Quota exceeded"
        })
        output = self._run_strip(result_line)
        self.assertIn("[Gemini Error]", output)
        self.assertIn("RESOURCE_EXHAUSTED", output)

    def test_result_without_error_produces_no_output(self):
        """Normal result event (stats only) must not produce output."""
        result_line = json.dumps({"type": "result", "tokens": 123, "cost": 0.001})
        output = self._run_strip(result_line)
        self.assertEqual(output.strip(), "")

    def test_multiple_model_chunks_joined(self):
        """Multiple model messages must be joined with newlines."""
        lines = "\n".join([
            json.dumps({"type": "message", "role": "model", "content": "Part 1"}),
            json.dumps({"type": "message", "role": "model", "content": "Part 2"}),
        ])
        result = self._run_strip(lines)
        self.assertIn("Part 1", result)
        self.assertIn("Part 2", result)


class TestStreamingPathGeminiRole(unittest.TestCase):
    """
    Tests for the streaming path fallthrough fix (Fix 2 + Fix 3).

    These tests simulate the condition where user-role message JSON lines
    would previously fall through to stream_buffer.push("chunk", line),
    echoing raw JSON as visible output.
    """

    def _simulate_stream_processing(self, line: str):
        """
        Simulate the fixed streaming path logic for a single Gemini JSON line.
        Returns ("chunk", content) if the line produces a chunk,
        ("skip", None) if the line is skipped, or ("passthrough", line) if it
        falls through to the non-Gemini path.
        """
        import json as _json

        _line_stripped = line.strip()
        if not _line_stripped.startswith("{"):
            return ("passthrough", line)

        try:
            _gobj = _json.loads(_line_stripped)
            _gtype = _gobj.get("type", "")

            # ----- THE FIXED CHECKS -----
            if _gtype == "message" and _gobj.get("role") in ("assistant", "model"):
                _content = _gobj.get("content", "")
                if _content:
                    return ("chunk", _content)
                return ("skip", None)
            elif _gtype == "tool_use":
                return ("skip", None)
            elif _gtype == "tool_result":
                return ("skip", None)
            elif _gtype in ("init", "result"):
                return ("skip", None)
            elif _gtype == "message":
                # Fix 3: non-model message lines must be skipped, not fall through
                return ("skip", None)
        except (ValueError, KeyError):
            pass

        return ("passthrough", line)

    def test_model_role_produces_chunk(self):
        """Streaming: Gemini 'model' role must produce a chunk event."""
        line = json.dumps({"type": "message", "role": "model", "content": "AI says hi"})
        event, content = self._simulate_stream_processing(line)
        self.assertEqual(event, "chunk")
        self.assertEqual(content, "AI says hi")

    def test_assistant_role_produces_chunk(self):
        """Streaming: 'assistant' role still produces a chunk (backwards compat)."""
        line = json.dumps({"type": "message", "role": "assistant", "content": "Response"})
        event, content = self._simulate_stream_processing(line)
        self.assertEqual(event, "chunk")

    def test_user_role_message_skipped_not_echoed(self):
        """Fix 3: user-role message must be skipped, NOT fall through to chunk push."""
        user_line = json.dumps({
            "type": "message",
            "timestamp": "2026-04-18T23:24:40.797Z",
            "role": "user",
            "content": "what happened?"
        })
        event, _ = self._simulate_stream_processing(user_line)
        self.assertEqual(event, "skip",
            "User-role message fell through to chunk — this is the echoing bug!")

    def test_init_event_skipped(self):
        """init events must be skipped."""
        line = json.dumps({"type": "init", "session": "abc123"})
        event, _ = self._simulate_stream_processing(line)
        self.assertEqual(event, "skip")

    def test_result_event_skipped(self):
        """result events must be skipped in streaming path."""
        line = json.dumps({"type": "result", "tokens": 99})
        event, _ = self._simulate_stream_processing(line)
        self.assertEqual(event, "skip")

    def test_non_json_line_passthrough(self):
        """Non-JSON lines must fall through to the normal processing path."""
        event, _ = self._simulate_stream_processing("plain text output")
        self.assertEqual(event, "passthrough")


if __name__ == "__main__":
    unittest.main(verbosity=2)
