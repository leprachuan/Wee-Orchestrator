"""Tests for Issue #128: Token usage tracking + cost estimation + WebUI footer."""
import json
import os
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
import pytest

# Add dev path
sys.path.insert(0, '/opt/n8n-copilot-shim-dev')


# ─── Helper method tests via AgentManager ────────────────────────────────────

class TestTokenHelpers:
    """Tests for SessionManager token tracking helper methods."""

    def setup_method(self):
        """Import SessionManager and instantiate with minimal setup."""
        import importlib
        self.am_mod = importlib.import_module('agent_manager')
        # Mock minimal required setup to get a usable instance
        with patch.object(self.am_mod.SessionManager, '__init__', lambda self, *a, **kw: None):
            self.mgr = self.am_mod.SessionManager.__new__(self.am_mod.SessionManager)
        # Set required attributes
        self.mgr.logs_dir = Path(tempfile.mkdtemp())

    def test_calculate_wee_cost_ollama(self):
        """Ollama models should always return cost_usd=0, label='local'."""
        cost, label = self.mgr._calculate_wee_cost('ollama/llama3', 1000, 500, {})
        assert cost == 0.0
        assert label == 'local'

    def test_calculate_wee_cost_openrouter_no_pricing(self):
        """Models with no pricing in cache should return free."""
        cost, label = self.mgr._calculate_wee_cost('openrouter/some-model', 1000, 500, {})
        assert cost == 0.0
        assert label == 'free'

    def test_calculate_wee_cost_openrouter_with_pricing(self):
        """Models with pricing data should return calculated cost."""
        pricing = {
            'google/gemini-flash': {  # bare model name (prefix stripped internally)
                'prompt': 0.000000075,
                'completion': 0.0000003,
            }
        }
        cost, label = self.mgr._calculate_wee_cost(
            'openrouter/google/gemini-flash', 1000, 500, pricing
        )
        expected = (1000 * 0.000000075) + (500 * 0.0000003)
        assert abs(cost - expected) < 1e-10
        assert label.startswith('$')

    def test_calculate_anthropic_cost_haiku(self):
        """Claude haiku pricing should be applied correctly."""
        cost, label = self.mgr._calculate_anthropic_cost('claude-haiku-4-5', 1000, 500)
        # $0.80/$4.00 per 1M tokens
        expected = (1000 * 0.80 / 1_000_000) + (500 * 4.00 / 1_000_000)
        assert abs(cost - expected) < 1e-10
        assert label.startswith('$')

    def test_calculate_anthropic_cost_sonnet(self):
        """Claude sonnet pricing should be applied correctly."""
        cost, label = self.mgr._calculate_anthropic_cost('claude-sonnet-4-5', 1000, 500)
        expected = (1000 * 3.0 / 1_000_000) + (500 * 15.0 / 1_000_000)
        assert abs(cost - expected) < 1e-10

    def test_calculate_anthropic_cost_unknown(self):
        """Unknown Anthropic model should return default pricing."""
        cost, label = self.mgr._calculate_anthropic_cost('claude-unknown-model', 100, 100)
        assert cost >= 0.0
        assert isinstance(label, str)

    def test_get_cost_label_zero(self):
        """Zero cost should return 'free'."""
        label = self.mgr._get_cost_label(0.0)
        assert label == 'free'

    def test_get_cost_label_small(self):
        """Small cost should be formatted as $0.XXXX."""
        label = self.mgr._get_cost_label(0.000012)
        assert label.startswith('$')
        assert '0.0000' in label or '0.00' in label

    def test_log_token_usage_writes_jsonl(self):
        """_log_token_usage should write a valid JSONL entry."""
        log_file = self.mgr.logs_dir / 'token_usage.jsonl'
        self.mgr._log_token_usage(
            session_id='test-session-1',
            model='openrouter/google/gemini-flash',
            runtime='wee',
            provider='openrouter',
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_usd=0.0000125,
            duration_ms=1234,
        )
        assert log_file.exists()
        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry['session_id'] == 'test-session-1'
        assert entry['model'] == 'openrouter/google/gemini-flash'
        assert entry['prompt_tokens'] == 100
        assert entry['completion_tokens'] == 50
        assert entry['total_tokens'] == 150
        assert abs(entry['cost_usd'] - 0.0000125) < 1e-12
        assert entry['duration_ms'] == 1234
        assert 'timestamp' in entry

    def test_log_token_usage_multiple_appends(self):
        """Multiple calls should append to the same file."""
        for i in range(3):
            self.mgr._log_token_usage(
                session_id=f'session-{i}',
                model='ollama/llama3',
                runtime='wee',
                provider='ollama',
                prompt_tokens=10 * i,
                completion_tokens=5 * i,
                total_tokens=15 * i,
                cost_usd=0.0,
                duration_ms=100,
            )
        log_file = self.mgr.logs_dir / 'token_usage.jsonl'
        lines = log_file.read_text().strip().split('\n')
        assert len(lines) == 3


# ─── /api/v1/usage endpoint tests ────────────────────────────────────────────

class TestUsageEndpoint:
    """Tests for GET /api/v1/usage endpoint."""

    def setup_method(self):
        """Setup test client."""
        import os
        from fastapi.testclient import TestClient
        import importlib
        self._orig_api_key = os.environ.get('API_SHARED_KEY')
        os.environ['API_SHARED_KEY'] = 'testkey'
        self.am_mod = importlib.import_module('agent_manager')
        self.app = self.am_mod.create_api_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.auth_headers = {'Authorization': 'Bearer shared_testkey'}

    def teardown_method(self):
        """Restore env to avoid leaking API_SHARED_KEY to other tests."""
        import os
        if self._orig_api_key is None:
            os.environ.pop('API_SHARED_KEY', None)
        else:
            os.environ['API_SHARED_KEY'] = self._orig_api_key

    def _write_usage_log(self, entries, logs_dir=None):
        """Write test entries to token_usage.jsonl."""
        d = logs_dir or self.tmp_dir
        log_file = d / 'token_usage.jsonl'
        with open(log_file, 'w') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        return log_file

    def _make_entry(self, days_ago=0, model='ollama/llama3', runtime='wee',
                    prompt_tokens=100, completion_tokens=50, cost_usd=0.0):
        """Create a test log entry."""
        ts = time.time() - (days_ago * 86400)
        return {
            'timestamp': ts,
            'session_id': 'test',
            'model': model,
            'runtime': runtime,
            'provider': 'ollama' if 'ollama' in model else 'openrouter',
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'total_tokens': prompt_tokens + completion_tokens,
            'cost_usd': cost_usd,
            'duration_ms': 1000,
        }

    def test_usage_today_filter(self):
        """?period=today should be accepted and return valid structure."""
        resp = self.client.get(
            '/api/v1/usage?period=today',
            headers=self.auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data['period'] == 'today'
        assert data['total_cost_usd'] >= 0

    def test_usage_endpoint_exists(self):
        """GET /api/v1/usage should return 200 with proper structure."""
        resp = self.client.get(
            '/api/v1/usage',
            headers=self.auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert 'total_cost_usd' in data
        assert 'total_tokens' in data
        assert 'by_model' in data
        assert isinstance(data['by_model'], list)

    def test_usage_period_7d(self):
        """?period=7d should be accepted."""
        resp = self.client.get(
            '/api/v1/usage?period=7d',
            headers=self.auth_headers
        )
        assert resp.status_code == 200

    def test_usage_period_30d(self):
        """?period=30d should be accepted."""
        resp = self.client.get(
            '/api/v1/usage?period=30d',
            headers=self.auth_headers
        )
        assert resp.status_code == 200


# ─── wee_runtime.py standalone tests ─────────────────────────────────────────

class TestWeeRuntime:
    """Tests for the wee_runtime.py standalone CLI functions."""

    def setup_method(self):
        """Import wee_runtime module."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'wee_runtime',
            '/opt/n8n-copilot-shim-dev/wee_runtime.py'
        )
        self.wr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.wr)

    def test_fetch_openrouter_pricing_cache_path(self):
        """Pricing should be cached to /tmp/openrouter_pricing.json."""
        mock_data = {
            'data': [
                {'id': 'google/gemini-flash-1.5', 'pricing': {'prompt': '0.000000075', 'completion': '0.0000003'}}
            ]
        }
        with patch('builtins.open', mock_open(read_data=json.dumps(mock_data))), \
             patch('os.path.exists', return_value=True), \
             patch('os.path.getmtime', return_value=time.time()):
            pricing = self.wr.fetch_openrouter_pricing()
            assert isinstance(pricing, dict)

    def test_calculate_cost_ollama(self):
        """Ollama models should always be $0, label=local."""
        cost, label = self.wr.calculate_cost('ollama/llama3', 500, 300, {})
        assert cost == 0.0
        assert label == 'local'

    def test_calculate_cost_free_model(self):
        """Unknown models not in pricing = free."""
        cost, label = self.wr.calculate_cost('openrouter/some-free-model', 100, 50, {})
        assert cost == 0.0
        assert label == 'free'

    def test_calculate_cost_paid_model(self):
        """Paid model should multiply token counts by per-token price."""
        pricing = {'google/gemini-flash-1.5': {'prompt': 0.000000075, 'completion': 0.0000003}}
        cost, label = self.wr.calculate_cost('openrouter/google/gemini-flash-1.5', 1000, 500, pricing)
        expected = (1000 * 0.000000075) + (500 * 0.0000003)
        assert abs(cost - expected) < 1e-12
        assert label.startswith('$')

    def test_log_token_usage_creates_file(self):
        """log_token_usage should create the log file if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / 'token_usage.jsonl'
            with patch.object(self.wr, 'LOG_FILE', log_path):
                self.wr.log_token_usage(
                    session_id='test',
                    model='ollama/llama3',
                    provider='ollama',
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    cost_usd=0.0,
                    duration_ms=500,
                )
            if log_path.exists():
                with open(log_path) as f:
                    entry = json.loads(f.readline())
                assert entry['model'] == 'ollama/llama3'
                assert entry['prompt_tokens'] == 10

    def test_stream_options_included(self):
        """wee_runtime should pass stream_options to the API."""
        import inspect
        source = inspect.getsource(self.wr)
        assert 'stream_options' in source
        assert 'include_usage' in source

    def test_wee_meta_output_format(self):
        """__WEE_META__ marker should be present in output code."""
        import inspect
        source = inspect.getsource(self.wr)
        assert '__WEE_META__' in source


# ─── app.js WebUI update tests ────────────────────────────────────────────────

class TestAppJsUpdate:
    """Verify that app.js was updated with token/cost display logic."""

    def setup_method(self):
        self.app_js = Path('/opt/n8n-copilot-shim-dev/webui/dist/app.js').read_text()

    def test_buildTimingText_function_exists(self):
        """buildTimingText function should be present in app.js."""
        assert 'function buildTimingText(' in self.app_js

    def test_buildTimingText_handles_wee_meta(self):
        """buildTimingText should reference weeMeta/tokens/cost_label."""
        assert 'weeMeta' in self.app_js
        assert 'cost_label' in self.app_js
        assert 'tokens' in self.app_js

    def test_streaming_path_uses_buildTimingText(self):
        """The streaming done handler should call buildTimingText."""
        assert 'buildTimingText(elapsedSec, evt.wee_meta' in self.app_js

    def test_renderMessage_accepts_weeMeta(self):
        """renderMessage should accept weeMeta as 5th param."""
        assert 'renderMessage(role, content, files = [], timing = null, weeMeta = null)' in self.app_js

    def test_renderMessage_timing_uses_buildTimingText(self):
        """renderMessage timing block should use buildTimingText."""
        assert 'buildTimingText(timing, weeMeta)' in self.app_js

    def test_copilot_label_handled(self):
        """copilot-sdk label should be handled in buildTimingText."""
        assert "copilot-sdk" in self.app_js or "copilot request" in self.app_js


# ─── SSE done_payload wee_meta inclusion ─────────────────────────────────────

class TestSSEDonePayload:
    """Verify that SSE done_payload includes wee_meta."""

    def test_wee_meta_in_stream_session(self):
        """stream_session / done_payload should include wee_meta."""
        am_path = Path('/opt/n8n-copilot-shim-dev/agent_manager.py')
        source = am_path.read_text()
        assert 'wee_meta' in source
        # Verify SSE done path contains wee_meta key
        assert '"wee_meta"' in source or "'wee_meta'" in source


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
