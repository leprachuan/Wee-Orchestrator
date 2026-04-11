"""Wee Model Discovery — auto-discover models from Ollama & OpenAI-compatible hosts.

Queries configured API hosts to discover available models dynamically.
Results are cached with a configurable TTL to avoid hammering APIs.
Thread-safe for use from both sync and async contexts.

Issue #114: https://github.com/leprachuan/Wee-Orchestrator/issues/114
"""

import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError

# Default discovery hosts (override via WEE_DISCOVERY_HOSTS env var as JSON)
_DEFAULT_HOSTS = [
    {
        "name": "kubuntu",
        "type": "ollama",
        "url": "http://192.168.1.101:11434",
        "prefix": "ollama",
    },
]


def _load_hosts() -> List[Dict[str, str]]:
    """Load discovery host config from env or defaults."""
    env = os.environ.get("WEE_DISCOVERY_HOSTS")
    if env:
        try:
            hosts = json.loads(env)
            if isinstance(hosts, list):
                return hosts
        except (json.JSONDecodeError, TypeError):
            print(
                f"[WeeModelDiscovery] Invalid WEE_DISCOVERY_HOSTS JSON, using defaults",
                file=sys.stderr,
            )
    return _DEFAULT_HOSTS


class WeeModelDiscovery:
    """Thread-safe model discovery with TTL caching."""

    def __init__(self, ttl: int = 60, timeout: int = 5):
        """
        Args:
            ttl: Cache time-to-live in seconds (default 60).
            timeout: HTTP request timeout in seconds (default 5).
        """
        self.ttl = ttl
        self.timeout = timeout
        self._cache: Dict[str, Tuple[float, List[str]]] = {}  # host_url -> (timestamp, models)
        self._cache_status: Dict[str, str] = {}  # host_url -> "online" | "offline"
        self._lock = threading.Lock()

    def _fetch_json(self, url: str) -> Optional[Any]:
        """Fetch JSON from a URL with timeout. Returns None on error."""
        try:
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, OSError, json.JSONDecodeError, ValueError) as exc:
            print(
                f"[WeeModelDiscovery] Failed to fetch {url}: {exc}",
                file=sys.stderr,
            )
            return None

    def discover_ollama(self, base_url: str) -> List[str]:
        """Query Ollama /api/tags endpoint for available models.

        Returns list of model name strings (e.g. ["gemma4:latest", "qwen3:8b"]).
        """
        url = f"{base_url.rstrip('/')}/api/tags"
        data = self._fetch_json(url)
        if data is None or not isinstance(data, dict):
            return []
        models = data.get("models", [])
        return [m["name"] for m in models if isinstance(m, dict) and "name" in m]

    def discover_openai_compat(self, base_url: str) -> List[str]:
        """Query OpenAI-compatible /v1/models endpoint.

        Returns list of model ID strings.
        """
        url = f"{base_url.rstrip('/')}/v1/models"
        data = self._fetch_json(url)
        if data is None or not isinstance(data, dict):
            return []
        models = data.get("data", [])
        return [m["id"] for m in models if isinstance(m, dict) and "id" in m]

    def _discover_host(self, host: Dict[str, str]) -> Tuple[str, List[str], str]:
        """Discover models for a single host.

        Returns (label, model_ids_with_prefix, status).
        """
        host_type = host.get("type", "openai-compat")
        base_url = host.get("url", "")
        prefix = host.get("prefix", "")
        name = host.get("name", base_url)

        if host_type == "ollama":
            raw = self.discover_ollama(base_url)
        else:
            raw = self.discover_openai_compat(base_url)

        if raw:
            # Add provider prefix to each model name
            if prefix:
                models = [f"{prefix}/{m}" for m in raw]
            else:
                models = raw
            status = "online"
        else:
            models = []
            status = "offline"

        label = f"{name} ({host_type})"
        return label, models, status

    def discover_all(self, force: bool = False) -> Dict[str, List[str]]:
        """Discover models from all configured hosts.

        Returns {group_label: [model_id, ...]} suitable for get_models_for_runtime.
        Uses cached results when within TTL unless force=True.
        """
        hosts = _load_hosts()
        result: Dict[str, List[str]] = {}

        with self._lock:
            for host in hosts:
                cache_key = host.get("url", "")
                now = time.time()

                # Check cache
                if not force and cache_key in self._cache:
                    cached_ts, cached_models = self._cache[cache_key]
                    if now - cached_ts < self.ttl:
                        label = f"{host.get('name', cache_key)} ({host.get('type', 'unknown')})"
                        if cached_models:
                            result[label] = cached_models
                        elif self._cache_status.get(cache_key) == "offline":
                            result[f"{label} ⚠️ offline"] = []
                        continue

                # Discover fresh
                label, models, status = self._discover_host(host)
                self._cache[cache_key] = (now, models)
                self._cache_status[cache_key] = status

                if models:
                    result[label] = models
                elif status == "offline":
                    # Fall back to last known models if available
                    if cache_key in self._cache:
                        _, old_models = self._cache[cache_key]
                        if old_models:
                            result[f"{label} (cached)"] = old_models
                            continue
                    result[f"{label} ⚠️ offline"] = []

        return result

    def discover_all_enriched(self, force: bool = False) -> Dict[str, Any]:
        """Discover with enriched metadata (sizes, timestamps).

        Returns {group_label: [{id, name, size, modified_at, provider, status}]}.
        """
        hosts = _load_hosts()
        result: Dict[str, List[Dict[str, Any]]] = {}

        with self._lock:
            for host in hosts:
                host_type = host.get("type", "openai-compat")
                base_url = host.get("url", "")
                prefix = host.get("prefix", "")
                name = host.get("name", base_url)
                label = f"{name} ({host_type})"

                if host_type == "ollama":
                    url = f"{base_url.rstrip('/')}/api/tags"
                    data = self._fetch_json(url)
                    if data and isinstance(data, dict):
                        models = []
                        for m in data.get("models", []):
                            if isinstance(m, dict) and "name" in m:
                                model_id = f"{prefix}/{m['name']}" if prefix else m["name"]
                                models.append({
                                    "id": model_id,
                                    "name": m["name"],
                                    "size": m.get("size"),
                                    "modified_at": m.get("modified_at"),
                                    "provider": prefix or host_type,
                                    "status": "available",
                                })
                        result[label] = models
                    else:
                        result[f"{label} ⚠️ offline"] = []
                else:
                    url = f"{base_url.rstrip('/')}/v1/models"
                    data = self._fetch_json(url)
                    if data and isinstance(data, dict):
                        models = []
                        for m in data.get("data", []):
                            if isinstance(m, dict) and "id" in m:
                                model_id = f"{prefix}/{m['id']}" if prefix else m["id"]
                                models.append({
                                    "id": model_id,
                                    "name": m["id"],
                                    "provider": prefix or host_type,
                                    "status": "available",
                                })
                        result[label] = models
                    else:
                        result[f"{label} ⚠️ offline"] = []

        return result

    def invalidate_cache(self):
        """Clear all cached results."""
        with self._lock:
            self._cache.clear()
            self._cache_status.clear()

    def get_host_status(self) -> Dict[str, str]:
        """Return {host_url: "online"|"offline"} for all configured hosts."""
        with self._lock:
            return dict(self._cache_status)


# Module-level singleton
_discovery_instance: Optional[WeeModelDiscovery] = None
_discovery_lock = threading.Lock()


def get_discovery() -> WeeModelDiscovery:
    """Get or create the module-level discovery singleton."""
    global _discovery_instance
    if _discovery_instance is None:
        with _discovery_lock:
            if _discovery_instance is None:
                ttl = int(os.environ.get("WEE_DISCOVERY_TTL", "60"))
                timeout = int(os.environ.get("WEE_DISCOVERY_TIMEOUT", "5"))
                _discovery_instance = WeeModelDiscovery(ttl=ttl, timeout=timeout)
    return _discovery_instance


def discover_wee_models(force: bool = False) -> Dict[str, List[str]]:
    """Convenience: discover all wee models using the singleton.

    Returns {group_label: [model_id, ...]} for get_models_for_runtime("wee").
    """
    return get_discovery().discover_all(force=force)
