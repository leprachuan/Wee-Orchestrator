"""Regression tests for issue #302: Codex WebUI should prefer done.response
over raw streamed JSON transport frames."""

from pathlib import Path


class TestIssue302CodexDoneHandler:
    def setup_method(self):
        self.app_js = Path("/opt/n8n-copilot-shim-dev/webui/dist/app.js").read_text()

    def test_codex_transport_frame_detector_exists(self):
        assert "function looksLikeCodexTransportFrames(" in self.app_js
        assert "function normalizeCodexStreamText(" in self.app_js
        assert '"type":"thread.started"' in self.app_js
        assert '"type":"item.completed"' in self.app_js

    def test_done_handler_uses_clean_response_for_codex_jsonl(self):
        assert "const doneResponse = evt.response || '(no response)';" in self.app_js
        assert (
            "const normalizedRawText = normalizeCodexStreamText(rawText);"
            in self.app_js
        )
        assert ": doneResponse;" in self.app_js

    def test_streaming_chunks_are_normalized_before_render(self):
        assert "const chunkText = normalizeCodexStreamText(evt.text);" in self.app_js
        assert "if (!chunkText) continue;" in self.app_js
        assert "rawText += chunkText;" in self.app_js

    def test_reconnect_done_handler_uses_clean_response_for_codex_jsonl(self):
        reconnect_done = """const doneResponse = evt.response || '(no response)';
              const normalizedRawText = normalizeCodexStreamText(rawText);
              const finalContent = normalizedRawText.trim()
                ? normalizedRawText
                : doneResponse;"""
        assert reconnect_done in self.app_js
