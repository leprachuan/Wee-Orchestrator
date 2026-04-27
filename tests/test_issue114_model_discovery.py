"""Tests for Issue #114: Wee runtime auto-discover models from API hosts.

Covers:
- WeeModelDiscovery class functionality
- Ollama /api/tags discovery
- OpenAI-compat /v1/models discovery
- TTL caching behavior
- Graceful degradation on host offline
- /api/v1/models endpoint includes wee
- /api/v1/wee/models enriched endpoint
- _fetch_wee_models fallback behavior
- Model format handling (strings vs tuples) in API
"""

import json
import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

# Ensure the dev codebase is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── WeeModelDiscovery unit tests ──────────────────────────────────────


class TestWeeModelDiscoveryInit(unittest.TestCase):
    """Test WeeModelDiscovery initialization and config."""

    def test_import(self):
        """wee_model_discovery module is importable."""
        import wee_model_discovery

        self.assertTrue(hasattr(wee_model_discovery, "WeeModelDiscovery"))
        self.assertTrue(hasattr(wee_model_discovery, "discover_wee_models"))
        self.assertTrue(hasattr(wee_model_discovery, "get_discovery"))

    def test_default_hosts(self):
        """Default hosts include Ollama on kubuntu."""
        from wee_model_discovery import _DEFAULT_HOSTS

        self.assertTrue(len(_DEFAULT_HOSTS) >= 1)
        self.assertEqual(_DEFAULT_HOSTS[0]["type"], "ollama")
        self.assertIn("192.168.1.101", _DEFAULT_HOSTS[0]["url"])

    def test_custom_ttl(self):
        """Custom TTL and timeout are respected."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=120, timeout=10)
        self.assertEqual(d.ttl, 120)
        self.assertEqual(d.timeout, 10)

    def test_env_hosts_override(self):
        """WEE_DISCOVERY_HOSTS env var overrides default hosts."""
        from wee_model_discovery import _load_hosts

        custom = [
            {
                "name": "test",
                "type": "ollama",
                "url": "http://localhost:11434",
                "prefix": "test",
            }
        ]
        with patch.dict(os.environ, {"WEE_DISCOVERY_HOSTS": json.dumps(custom)}):
            hosts = _load_hosts()
            self.assertEqual(len(hosts), 1)
            self.assertEqual(hosts[0]["name"], "test")

    def test_env_hosts_invalid_json_falls_back(self):
        """Invalid JSON in WEE_DISCOVERY_HOSTS falls back to defaults."""
        from wee_model_discovery import _DEFAULT_HOSTS, _load_hosts

        with patch.dict(os.environ, {"WEE_DISCOVERY_HOSTS": "not-json"}):
            hosts = _load_hosts()
            self.assertEqual(hosts, _DEFAULT_HOSTS)

    def test_singleton_pattern(self):
        """get_discovery() returns the same instance on repeated calls."""
        import wee_model_discovery

        # Reset singleton for clean test
        wee_model_discovery._discovery_instance = None
        d1 = wee_model_discovery.get_discovery()
        d2 = wee_model_discovery.get_discovery()
        self.assertIs(d1, d2)
        # Cleanup
        wee_model_discovery._discovery_instance = None


class TestOllamaDiscovery(unittest.TestCase):
    """Test Ollama-specific discovery logic."""

    def setUp(self):
        from wee_model_discovery import WeeModelDiscovery

        self.discovery = WeeModelDiscovery(ttl=60, timeout=5)

    def test_discover_ollama_success(self):
        """Ollama /api/tags discovery parses model names correctly."""
        mock_response = json.dumps(
            {
                "models": [
                    {
                        "name": "gemma4:latest",
                        "size": 5000000000,
                        "modified_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "name": "qwen3:8b",
                        "size": 4000000000,
                        "modified_at": "2026-01-02T00:00:00Z",
                    },
                ]
            }
        ).encode()
        with patch("wee_model_discovery.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = mock_response
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            models = self.discovery.discover_ollama("http://localhost:11434")
            self.assertEqual(models, ["gemma4:latest", "qwen3:8b"])

    def test_discover_ollama_empty(self):
        """Ollama with no models returns empty list."""
        mock_response = json.dumps({"models": []}).encode()
        with patch("wee_model_discovery.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = mock_response
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            models = self.discovery.discover_ollama("http://localhost:11434")
            self.assertEqual(models, [])

    def test_discover_ollama_unreachable(self):
        """Unreachable Ollama host returns empty list (no crash)."""
        from urllib.error import URLError

        with patch(
            "wee_model_discovery.urlopen", side_effect=URLError("Connection refused")
        ):
            models = self.discovery.discover_ollama("http://unreachable:11434")
            self.assertEqual(models, [])


class TestOpenAICompatDiscovery(unittest.TestCase):
    """Test OpenAI-compatible /v1/models discovery."""

    def setUp(self):
        from wee_model_discovery import WeeModelDiscovery

        self.discovery = WeeModelDiscovery(ttl=60, timeout=5)

    def test_discover_openai_compat_success(self):
        """OpenAI-compat /v1/models returns model IDs."""
        mock_response = json.dumps(
            {
                "data": [
                    {"id": "gpt-4", "object": "model"},
                    {"id": "gpt-3.5-turbo", "object": "model"},
                ]
            }
        ).encode()
        with patch("wee_model_discovery.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = mock_response
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            models = self.discovery.discover_openai_compat("http://localhost:1234")
            self.assertEqual(models, ["gpt-4", "gpt-3.5-turbo"])

    def test_discover_openai_compat_unreachable(self):
        """Unreachable OpenAI-compat host returns empty list."""
        from urllib.error import URLError

        with patch(
            "wee_model_discovery.urlopen", side_effect=URLError("Connection refused")
        ):
            models = self.discovery.discover_openai_compat("http://unreachable:1234")
            self.assertEqual(models, [])


class TestDiscoverAll(unittest.TestCase):
    """Test the discover_all orchestration method."""

    def test_discover_all_with_prefix(self):
        """Models are prefixed with provider name."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=60, timeout=5)

        mock_response = json.dumps(
            {
                "models": [
                    {"name": "gemma4:latest"},
                    {"name": "qwen3:8b"},
                ]
            }
        ).encode()

        hosts = [
            {
                "name": "kubuntu",
                "type": "ollama",
                "url": "http://test:11434",
                "prefix": "ollama",
            }
        ]
        with patch("wee_model_discovery._load_hosts", return_value=hosts):
            with patch("wee_model_discovery.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = mock_response
                mock_resp.__enter__ = lambda s: s
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_resp

                result = d.discover_all()
                self.assertIn("kubuntu (ollama)", result)
                models = result["kubuntu (ollama)"]
                self.assertEqual(models, ["ollama/gemma4:latest", "ollama/qwen3:8b"])

    def test_discover_all_offline_host(self):
        """Offline host shows offline indicator."""
        from urllib.error import URLError

        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=60, timeout=5)

        hosts = [
            {
                "name": "down-host",
                "type": "ollama",
                "url": "http://down:11434",
                "prefix": "ollama",
            }
        ]
        with patch("wee_model_discovery._load_hosts", return_value=hosts):
            with patch("wee_model_discovery.urlopen", side_effect=URLError("refused")):
                result = d.discover_all()
                # Should have an offline indicator key
                self.assertTrue(any("offline" in k for k in result.keys()))

    def test_discover_all_multiple_hosts(self):
        """Multiple hosts are all queried and grouped."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=60, timeout=5)

        hosts = [
            {
                "name": "host1",
                "type": "ollama",
                "url": "http://h1:11434",
                "prefix": "ollama",
            },
            {
                "name": "host2",
                "type": "openai-compat",
                "url": "http://h2:1234",
                "prefix": "lmstudio",
            },
        ]

        def mock_fetch(url):
            if "h1" in url:
                return {"models": [{"name": "gemma4:latest"}]}
            elif "h2" in url:
                return {"data": [{"id": "local-model"}]}
            return None

        with patch("wee_model_discovery._load_hosts", return_value=hosts):
            with patch.object(d, "_fetch_json", side_effect=mock_fetch):
                result = d.discover_all()
                self.assertIn("host1 (ollama)", result)
                self.assertIn("host2 (openai-compat)", result)
                self.assertEqual(result["host1 (ollama)"], ["ollama/gemma4:latest"])
                self.assertEqual(
                    result["host2 (openai-compat)"], ["lmstudio/local-model"]
                )


class TestCaching(unittest.TestCase):
    """Test TTL cache behavior."""

    def test_cache_hit_within_ttl(self):
        """Second call within TTL uses cached results (no network call)."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=60, timeout=5)

        hosts = [
            {
                "name": "h1",
                "type": "ollama",
                "url": "http://h1:11434",
                "prefix": "ollama",
            }
        ]
        mock_response = {"models": [{"name": "model1"}]}

        call_count = 0

        def counting_fetch(url):
            nonlocal call_count
            call_count += 1
            return mock_response

        with patch("wee_model_discovery._load_hosts", return_value=hosts):
            with patch.object(d, "_fetch_json", side_effect=counting_fetch):
                r1 = d.discover_all()
                r2 = d.discover_all()
                self.assertEqual(call_count, 1)  # Only one network call
                self.assertEqual(r1, r2)

    def test_cache_miss_after_ttl(self):
        """Cache expires after TTL, triggers fresh discovery."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=1, timeout=5)  # 1-second TTL

        hosts = [
            {
                "name": "h1",
                "type": "ollama",
                "url": "http://h1:11434",
                "prefix": "ollama",
            }
        ]
        mock_response = {"models": [{"name": "model1"}]}

        call_count = 0

        def counting_fetch(url):
            nonlocal call_count
            call_count += 1
            return mock_response

        with patch("wee_model_discovery._load_hosts", return_value=hosts):
            with patch.object(d, "_fetch_json", side_effect=counting_fetch):
                d.discover_all()
                time.sleep(1.1)
                d.discover_all()
                self.assertEqual(call_count, 2)  # Two network calls

    def test_force_bypasses_cache(self):
        """force=True always makes a network call."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=600, timeout=5)

        hosts = [
            {
                "name": "h1",
                "type": "ollama",
                "url": "http://h1:11434",
                "prefix": "ollama",
            }
        ]
        mock_response = {"models": [{"name": "model1"}]}

        call_count = 0

        def counting_fetch(url):
            nonlocal call_count
            call_count += 1
            return mock_response

        with patch("wee_model_discovery._load_hosts", return_value=hosts):
            with patch.object(d, "_fetch_json", side_effect=counting_fetch):
                d.discover_all()
                d.discover_all(force=True)
                self.assertEqual(call_count, 2)

    def test_invalidate_cache(self):
        """invalidate_cache clears all cached data."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=600, timeout=5)
        d._cache["test"] = (time.time(), ["model1"])
        d._cache_status["test"] = "online"
        d.invalidate_cache()
        self.assertEqual(len(d._cache), 0)
        self.assertEqual(len(d._cache_status), 0)

    def test_offline_fallback_returns_cached_models(self):
        """Regression: host online → TTL expires → host goes offline → result has cached models.

        Verifies that the offline fallback actually returns previously-seen models
        rather than an empty offline indicator. Bug: cache was overwritten before
        the old value was read, so fallback always returned [].
        """
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=1, timeout=5)  # 1-second TTL

        hosts = [
            {"name": "h", "type": "ollama", "url": "http://h:11434", "prefix": "ollama"}
        ]
        online_response = {"models": [{"name": "gemma4:latest"}]}

        with patch("wee_model_discovery._load_hosts", return_value=hosts):
            # First call: host is online — models are fetched and cached
            with patch.object(d, "_fetch_json", return_value=online_response):
                result1 = d.discover_all()
            self.assertIn("h (ollama)", result1)
            self.assertIn("ollama/gemma4:latest", result1["h (ollama)"])

            # Wait for TTL to expire
            time.sleep(1.1)

            # Second call: host goes offline — should fall back to cached models
            with patch.object(d, "_fetch_json", return_value=None):
                result2 = d.discover_all()

        # Must NOT show empty offline indicator
        self.assertFalse(
            any("offline" in k for k in result2.keys()),
            f"Expected cached fallback, got offline indicator. Keys: {list(result2.keys())}",
        )
        # Must show the cached group with the previously-discovered models
        cached_key = "h (ollama) (cached)"
        self.assertIn(
            cached_key,
            result2,
            f"Expected '{cached_key}' in result. Got: {list(result2.keys())}",
        )
        self.assertIn("ollama/gemma4:latest", result2[cached_key])


class TestHostStatus(unittest.TestCase):
    """Test host status tracking."""

    def test_host_status_online(self):
        """Online host is tracked as 'online'."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=60, timeout=5)

        hosts = [
            {
                "name": "h1",
                "type": "ollama",
                "url": "http://h1:11434",
                "prefix": "ollama",
            }
        ]
        mock_response = {"models": [{"name": "model1"}]}

        with patch("wee_model_discovery._load_hosts", return_value=hosts):
            with patch.object(d, "_fetch_json", return_value=mock_response):
                d.discover_all()
                status = d.get_host_status()
                self.assertEqual(status.get("http://h1:11434"), "online")

    def test_host_status_offline(self):
        """Offline host is tracked as 'offline'."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=60, timeout=5)

        hosts = [
            {
                "name": "h1",
                "type": "ollama",
                "url": "http://h1:11434",
                "prefix": "ollama",
            }
        ]

        with patch("wee_model_discovery._load_hosts", return_value=hosts):
            with patch.object(d, "_fetch_json", return_value=None):
                d.discover_all()
                status = d.get_host_status()
                self.assertEqual(status.get("http://h1:11434"), "offline")


# ── agent_manager.py integration tests ───────────────────────────────


class TestFetchWeeModels(unittest.TestCase):
    """Test _fetch_wee_models method in SessionManager."""

    def _get_session_mgr(self):
        """Get a SessionManager instance for testing."""
        from agent_manager import SessionManager

        return SessionManager.__new__(SessionManager)

    def test_fetch_wee_models_calls_discovery(self):
        """_fetch_wee_models uses wee_model_discovery module."""
        mgr = self._get_session_mgr()
        mock_result = {"kubuntu (ollama)": ["ollama/gemma4:latest", "ollama/qwen3:8b"]}
        with patch("agent_manager.discover_wee_models", mock_result, create=True):
            with patch(
                "wee_model_discovery.discover_wee_models", return_value=mock_result
            ):
                result = mgr._fetch_wee_models()
                self.assertIn("kubuntu (ollama)", result)

    def test_fetch_wee_models_fallback_on_error(self):
        """_fetch_wee_models falls back to static list on exception."""
        mgr = self._get_session_mgr()
        with patch(
            "wee_model_discovery.discover_wee_models", side_effect=RuntimeError("boom")
        ):
            result = mgr._fetch_wee_models()
            self.assertIn("Wee Native (static)", result)
            models = result["Wee Native (static)"]
            self.assertTrue(len(models) >= 1)
            self.assertTrue(all(isinstance(m, str) for m in models))

    def test_fetch_wee_models_returns_flat_strings(self):
        """_fetch_wee_models returns flat strings (not tuples) for compatibility."""
        mgr = self._get_session_mgr()
        mock_result = {"test": ["ollama/model1", "ollama/model2"]}
        with patch("wee_model_discovery.discover_wee_models", return_value=mock_result):
            result = mgr._fetch_wee_models()
            for group, models in result.items():
                for m in models:
                    self.assertIsInstance(
                        m, str, f"Model {m!r} in group {group!r} is not a string"
                    )


class TestGetModelsForRuntimeWee(unittest.TestCase):
    """Test get_models_for_runtime dispatches to wee discovery."""

    def _get_session_mgr(self):
        from agent_manager import SessionManager

        return SessionManager.__new__(SessionManager)

    def test_wee_in_dispatch(self):
        """'wee' is in get_models_for_runtime dispatch table."""
        mgr = self._get_session_mgr()
        mock_result = {"test": ["ollama/model1"]}
        with patch.object(mgr, "_fetch_wee_models", return_value=mock_result):
            result = mgr.get_models_for_runtime("wee")
            self.assertEqual(result, mock_result)


class TestApiModelsEndpointWee(unittest.TestCase):
    """Test /api/v1/models?runtime=wee endpoint accepts wee."""

    def test_wee_in_known_runtimes(self):
        """'wee' is in the known_runtimes set in the API endpoint."""
        # Read the source to verify (static analysis)
        import agent_manager

        source = open(agent_manager.__file__).read()
        # Find the known_runtimes block in the endpoint
        import re

        match = re.search(r"known_runtimes\s*=\s*\{([^}]+)\}", source)
        self.assertIsNotNone(match, "Could not find known_runtimes set")
        runtimes_block = match.group(1)
        self.assertIn('"wee"', runtimes_block)


class TestApiModelsEndpointTupleHandling(unittest.TestCase):
    """Test that /api/v1/models handles both tuple and string model formats."""

    def test_string_models_have_id_and_label(self):
        """String model IDs are returned with id and label fields."""
        # Simulate what the endpoint does with flat string models
        raw = {"Ollama": ["ollama/gemma4:latest", "ollama/qwen3:8b"]}
        models = []
        for _group, model_ids in raw.items():
            for entry in model_ids:
                if isinstance(entry, (list, tuple)):
                    model_id = entry[0]
                    label = entry[1] if len(entry) > 1 else model_id
                else:
                    model_id = entry
                    label = model_id
                models.append({"id": model_id, "label": label, "group": _group})
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0]["id"], "ollama/gemma4:latest")
        self.assertEqual(models[0]["group"], "Ollama")

    def test_tuple_models_have_id_and_label(self):
        """Tuple model entries (id, desc, aliases) are handled correctly."""
        raw = {"Claude": [("claude-sonnet-4", "Claude Sonnet 4", ["sonnet"])]}
        models = []
        for _group, model_ids in raw.items():
            for entry in model_ids:
                if isinstance(entry, (list, tuple)):
                    model_id = entry[0]
                    label = entry[1] if len(entry) > 1 else model_id
                else:
                    model_id = entry
                    label = model_id
                models.append({"id": model_id, "label": label, "group": _group})
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["id"], "claude-sonnet-4")
        self.assertEqual(models[0]["label"], "Claude Sonnet 4")


class TestThreadSafety(unittest.TestCase):
    """Test thread safety of discovery."""

    def test_concurrent_discover_all(self):
        """Multiple threads calling discover_all don't crash."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=60, timeout=5)

        hosts = [
            {
                "name": "h1",
                "type": "ollama",
                "url": "http://h1:11434",
                "prefix": "ollama",
            }
        ]
        mock_response = {"models": [{"name": "model1"}, {"name": "model2"}]}

        errors = []

        def worker():
            try:
                with patch("wee_model_discovery._load_hosts", return_value=hosts):
                    with patch.object(d, "_fetch_json", return_value=mock_response):
                        result = d.discover_all(force=True)
                        assert len(result) > 0
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread errors: {errors}")


class TestDiscoverAllEnriched(unittest.TestCase):
    """Test enriched discovery with metadata."""

    def test_enriched_includes_size_and_timestamp(self):
        """Enriched Ollama discovery includes size and modified_at."""
        from wee_model_discovery import WeeModelDiscovery

        d = WeeModelDiscovery(ttl=60, timeout=5)

        hosts = [
            {
                "name": "kubuntu",
                "type": "ollama",
                "url": "http://h1:11434",
                "prefix": "ollama",
            }
        ]
        mock_response = {
            "models": [
                {
                    "name": "gemma4:latest",
                    "size": 5000000000,
                    "modified_at": "2026-01-01T00:00:00Z",
                }
            ]
        }

        with patch("wee_model_discovery._load_hosts", return_value=hosts):
            with patch.object(d, "_fetch_json", return_value=mock_response):
                result = d.discover_all_enriched()
                self.assertIn("kubuntu (ollama)", result)
                models = result["kubuntu (ollama)"]
                self.assertEqual(len(models), 1)
                m = models[0]
                self.assertEqual(m["id"], "ollama/gemma4:latest")
                self.assertEqual(m["size"], 5000000000)
                self.assertEqual(m["modified_at"], "2026-01-01T00:00:00Z")
                self.assertEqual(m["provider"], "ollama")
                self.assertEqual(m["status"], "available")


if __name__ == "__main__":
    unittest.main()
