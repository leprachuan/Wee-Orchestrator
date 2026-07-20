#!/usr/bin/env python3
"""Wee Native Runtime — standalone CLI backed by the Copilot SDK BYOK executor.

Streams response tokens to stdout. Supports Ollama, OpenRouter, LM Studio, and
any OpenAI-compatible API via Copilot SDK BYOK provider routing.

Issue #443: The Copilot SDK is the only execution path — there is no
OpenAI-compatible fallback loop. A Copilot SDK failure is a real error.

Usage:
    python3 wee_runtime.py --model MODEL --api-base URL [--api-key KEY] "PROMPT"
    python3 wee_runtime.py --model ollama/qwen3:8b --tools "ask what day it is"
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

# Provider presets: prefix → (api_base, default_api_key)
# Use env vars for Ollama and LM Studio to allow customization
_OLLAMA_HOST = (
    os.environ.get("WEE_OLLAMA_BASE_URL")
    or os.environ.get("WEE_OLLAMA_HOST")
    or os.environ.get("OLLAMA_HOST")
    or "http://192.168.1.101:11434"
).rstrip("/")
if not _OLLAMA_HOST.startswith(("http://", "https://")):
    _OLLAMA_HOST = f"http://{_OLLAMA_HOST}"
if _OLLAMA_HOST.endswith("/v1"):
    _OLLAMA_HOST = _OLLAMA_HOST[:-3]
_LMSTUDIO_HOST = os.environ.get("LMSTUDIO_HOST", "localhost")
_LMSTUDIO_PORT = os.environ.get("LMSTUDIO_PORT", "1234")

PROVIDER_PRESETS = {
    "ollama": (f"{_OLLAMA_HOST}/v1", "ollama"),
    "openrouter": ("https://openrouter.ai/api/v1", None),
    "lmstudio": (f"http://{_LMSTUDIO_HOST}:{_LMSTUDIO_PORT}/v1", "lm-studio"),
}

def _webui_secret(name: str) -> "str | None":
    """Read a credential stored by the WebUI Secret Manager."""
    try:
        from secret_tool.secret_tool import FileBackend

        result = FileBackend().get(name)
        if result.get("status") == "success":
            credential = result.get("credential")
            if isinstance(credential, str) and credential.strip():
                return credential.strip()
    except Exception:
        pass
    return None


def fetch_openrouter_pricing():
    """Fetch OpenRouter model pricing from API and cache it."""
    try:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            api_key = _webui_secret("OPENROUTER_API_KEY")
        if not api_key:
            try:
                import keyring
                api_key = keyring.get_password("openrouter", "api_key")
            except:
                pass

        if not api_key:
            return {}

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": "Bearer " + api_key},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())

        pricing = {}
        for model in data.get("data", []):
            model_id = model.get("id", "")
            pricing[model_id] = {
                "prompt": float(model.get("pricing", {}).get("prompt", 0) or 0),
                "completion": float(model.get("pricing", {}).get("completion", 0) or 0),
            }
        return pricing
    except Exception as e:
        return {}

def calculate_openrouter_cost(model_id: str, prompt_tokens: int, completion_tokens: int, pricing_cache: dict = None):
    """Calculate estimated cost for OpenRouter API call."""
    if pricing_cache is None:
        pricing_cache = fetch_openrouter_pricing()

    if model_id not in pricing_cache:
        return None

    p = pricing_cache[model_id]
    if p["prompt"] == 0 and p["completion"] == 0:
        return None

    cost = (prompt_tokens * p["prompt"] + completion_tokens * p["completion"]) / 1000
    return f"${cost:.6f}"

# Default free model config (used when wee_free_models.json is missing)
_DEFAULT_FREE_CONFIG = {
    "free_model_fallback_chain": [
        "openrouter/free",
        "openrouter/google/gemma-4-31b-it:free",
        "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "openrouter/google/gemma-3-27b-it:free",
        "openrouter/nousresearch/hermes-3-llama-3.1-405b:free",
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
        "openrouter/openai/gpt-oss-120b:free",
        "openrouter/google/gemma-3-12b-it:free",
        "openrouter/meta-llama/llama-3.2-3b-instruct:free",
        "openrouter/google/gemma-3-4b-it:free",
    ],
    "max_retries_per_model": 3,
    "retry_backoff_seconds": [2, 5, 10],
}


def load_free_model_config(config_path: str = None) -> dict:
    """Load wee_free_models.json; fall back to hardcoded defaults."""
    if config_path is None:
        config_path = Path(__file__).parent / "wee_free_models.json"
    try:
        with open(config_path) as f:
            data = json.load(f)
        # Merge with defaults to fill missing keys
        result = dict(_DEFAULT_FREE_CONFIG)
        result.update(data)
        return result
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(_DEFAULT_FREE_CONFIG)


def is_free_openrouter_model(model: str) -> bool:
    """Return True if model is an OpenRouter free model (ends with :free or is openrouter/free)."""
    m = model.lower()
    return m == "openrouter/free" or (m.startswith("openrouter/") and m.endswith(":free"))


# ---------------------------------------------------------------------------
# Model context window registry (Issue #273)
# ---------------------------------------------------------------------------
MODEL_CONTEXT_WINDOWS: dict = {
    # OpenAI
    "gpt-5": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "gpt-4.1": 1_047_576,
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4-32k": 32_768,
    "gpt-4": 8_192,
    "gpt-3.5-turbo-16k": 16_385,
    "gpt-3.5-turbo": 16_385,
    # Anthropic Claude (via OpenRouter or direct)
    "claude-3": 200000,
    "claude-2": 100000,
    # Meta Llama
    "llama-3": 131072,
    "llama-2": 4096,
    # Google Gemma
    "gemma4": 131072,
    "gemma3": 131072,
    "gemma2": 8192,
    "gemma": 8192,
    # Alibaba Qwen
    "qwen3.5-64k": 64000,
    "qwen3": 32768,
    "qwen2": 32768,
    "qwen": 32768,
    # Mistral AI
    "mixtral": 32768,
    "mistral": 32768,
    # Microsoft Phi
    "phi-3": 128000,
    "phi3": 128000,
    # DeepSeek
    "deepseek": 65536,
    # Code models
    "codellama": 16384,
}

_DEFAULT_CONTEXT_WINDOW = 4096


def get_context_window(model: str) -> int:
    """Return the context window size for a model.

    Matches by longest substring key in MODEL_CONTEXT_WINDOWS.
    Falls back to _DEFAULT_CONTEXT_WINDOW when no match is found.
    """
    model_lower = model.lower()
    matches = [
        (key, size) for key, size in MODEL_CONTEXT_WINDOWS.items() if key in model_lower
    ]
    if matches:
        return max(matches, key=lambda x: len(x[0]))[1]
    return _DEFAULT_CONTEXT_WINDOW


def estimate_tokens(text: str, model: str = "") -> int:
    """Estimate token count for a text string.

    Uses tiktoken for OpenAI models when available, falls back to ~4 chars/token.
    """
    if not text:
        return 0
    _openai_prefixes = ("gpt-", "text-embedding", "davinci", "curie", "babbage")
    if any(p in model.lower() for p in _openai_prefixes):
        try:
            import tiktoken

            try:
                enc = tiktoken.encoding_for_model(model)
            except KeyError:
                enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError:
            pass
    # Heuristic: ~4 chars per token
    return max(1, len(text) // 4)


def count_message_tokens(messages: list, model: str = "") -> int:
    """Estimate the total token count for a list of chat messages.

    Handles the following message shapes:

    * Standard ``role: user/assistant/system`` with string or list content.
    * Anthropic content-list parts: ``type: text``, ``type: tool_use``,
      ``type: tool_result``.
    * OpenAI tool-result messages: ``role: tool`` with a string ``content``
      and an optional ``tool_call_id`` field.
    * OpenAI assistant tool-call messages: ``role: assistant`` with a
      ``tool_calls`` array.
    """
    total = 0
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += estimate_tokens(part["text"], model)
                    elif part.get("type") in ("tool_use", "tool_result"):
                        # Anthropic: count tool name + input/output payload
                        total += estimate_tokens(part.get("name", ""), model)
                        inner = part.get("input") or part.get("content") or ""
                        if isinstance(inner, str):
                            total += estimate_tokens(inner, model)
                        elif isinstance(inner, dict):
                            total += estimate_tokens(str(inner), model)
        else:
            # String content -- covers standard messages and OpenAI role="tool"
            # tool-result messages (content is always a plain string there).
            total += estimate_tokens(str(content), model)
        if role in ("tool", "tool_result", "tool_call"):
            # OpenAI tool-result messages carry a tool_call_id; count it.
            tc_id = msg.get("tool_call_id", "")
            if tc_id:
                total += estimate_tokens(tc_id, model)
        # Count tool_calls array for assistant messages (OpenAI format)
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            total += estimate_tokens(fn.get("name", ""), model)
            total += estimate_tokens(fn.get("arguments", ""), model)
        total += 4  # per-message overhead (role, delimiters)
    return total


COMPACT_TRIGGER_FRACTION = 0.75
_COMPACT_WARN_PCT = COMPACT_TRIGGER_FRACTION * 100


def compact_messages(
    messages: list,
    target_tokens: int,
    model: str,
    client,
    keep_recent: int = 6,
) -> tuple:
    """Compact message history to fit within target_tokens.

    Preserves the system prompt and the most recent ``keep_recent`` messages.
    Older messages are summarised into a single context-summary exchange using
    the LLM, keeping the conversation coherent without blowing the context window.

    Note: Does not guarantee the result fits within *target_tokens*. If the
    ``keep_recent`` messages alone exceed the target, a :mod:`warnings` warning
    is emitted but the result is still returned — callers should check the
    returned token count independently.

    Returns:
        (compacted_messages, summary_text) tuple.
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= keep_recent:
        return messages, ""

    to_summarize = non_system[:-keep_recent]
    to_keep = non_system[-keep_recent:]

    # If the first kept message is a tool result, the paired assistant message
    # (which carries tool_calls) sits just before the keep boundary. Without it
    # the compacted history is invalid — OpenAI APIs reject a tool message that
    # has no preceding assistant message with a matching tool_calls entry.
    if to_keep and to_keep[0].get("role") == "tool":
        idx = len(non_system) - keep_recent - 1
        while idx >= 0:
            msg = non_system[idx]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                to_summarize = non_system[:idx]
                to_keep = non_system[idx:]
                break
            idx -= 1

    def _summary_snippet(text: str, limit: int = 500) -> str:
        text = str(text)
        if len(text) <= limit:
            return text
        head = max(1, int(limit * 0.65))
        tail = max(1, limit - head - 5)
        return f"{text[:head]} ... {text[-tail:]}"

    transcript_lines = []
    for m in to_summarize:
        role = m.get("role", "")
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        if role in ("user", "assistant") and content:
            prefix = "User" if role == "user" else "Assistant"
            transcript_lines.append(f"{prefix}: {_summary_snippet(content)}")

    if not transcript_lines:
        return messages, ""

    transcript = "\n".join(transcript_lines)
    summary_prompt = (
        "Summarize the following conversation history concisely. Preserve all key "
        "facts, decisions, file paths, and context needed to continue the conversation "
        "coherently. Keep the summary under 400 words.\n\n"
        f"Conversation:\n{transcript}"
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": summary_prompt}],
            stream=False,
        )
        summary_text = resp.choices[0].message.content or ""
    except Exception as exc:
        summary_text = f"[Compaction error: {exc}]"

    compacted = (
        system_msgs
        + [
            {
                "role": "user",
                "content": f"[Earlier conversation summary]\n{summary_text}",
            },
            {
                "role": "assistant",
                "content": (
                    "Understood. I have the context from the earlier conversation."
                ),
            },
        ]
        + to_keep
    )

    actual_tokens = count_message_tokens(compacted, model)
    if actual_tokens > target_tokens:
        import warnings

        warnings.warn(
            f"compact_messages: result ({actual_tokens} tokens) exceeds "
            f"target ({target_tokens} tokens) — recent messages alone are too large.",
            stacklevel=2,
        )

    return compacted, summary_text


# ---------------------------------------------------------------------------
# SearXNG search support (Issue #255)
# ---------------------------------------------------------------------------
SEARCH_TIMEOUT = 10  # seconds for SearXNG queries
SEARCH_MAX_CHARS = 2000  # max result chars to avoid context bloat


def _format_search_results(query: str, results: list, output_format: str) -> str:
    """Format normalized search result dictionaries for an LLM tool response."""
    if not results:
        return "No results found."
    if output_format == "json":
        return json.dumps(results, ensure_ascii=False)[:SEARCH_MAX_CHARS]

    lines = [f"Search results for: {query}"]
    for i, result in enumerate(results, 1):
        lines.append(
            f"\n{i}. {result.get('title', '(no title)')}\n"
            f"   {result.get('url', '')}\n"
            f"   {result.get('snippet', '')}"
        )
    return "\n".join(lines)[:SEARCH_MAX_CHARS]


def _decode_search_value(value: str) -> str:
    """Decode the JSON-style strings embedded in Brave's server-rendered data."""
    try:
        return json.loads(f'"{value}"')
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _execute_brave_search(
    query: str, count: int, output_format: str, searxng_url: str
) -> str:
    """Fallback for a Mac without a configured/reachable SearXNG service."""
    url = "https://search.brave.com/search?" + urllib.parse.urlencode({"q": query})
    try:
        # Search providers frequently reject Python's HTTP TLS fingerprint.
        # curl is available on supported macOS/Linux hosts and supplies a
        # browser-compatible transport without executing user-provided shell.
        response = subprocess.run(
            [
                "curl", "-fsSL", "--max-time", str(SEARCH_TIMEOUT),
                "-A", "Mozilla/5.0",
                "-H", "Accept-Language: en-US,en;q=0.9",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=SEARCH_TIMEOUT + 2,
        )
        if response.returncode != 0:
            raise RuntimeError(response.stderr.strip() or f"curl exited {response.returncode}")
        page = response.stdout
    except Exception as error:
        return (
            f"Search unavailable ({searxng_url}): SearXNG and fallback search "
            f"could not be reached ({error})"
        )

    # Brave includes the result data in its server-rendered hydration payload.
    # Parsing that payload avoids a JavaScript runtime while preserving direct
    # source URLs and result descriptions.
    raw_results = re.findall(
        r'title:"((?:\\.|[^"\\])*)",url:"((?:\\.|[^"\\])*)".*?description:"((?:\\.|[^"\\])*)"',
        page,
        re.S,
    )
    results = []
    seen_urls = set()
    for title_raw, url_raw, description_raw in raw_results:
        destination = _decode_search_value(url_raw)
        if not destination.startswith(("http://", "https://")) or destination in seen_urls:
            continue
        seen_urls.add(destination)
        title = _decode_search_value(title_raw)
        description = _decode_search_value(description_raw)
        results.append({
            "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
            "url": html.unescape(destination).strip(),
            "snippet": html.unescape(re.sub(r"<[^>]+>", "", description)).strip()[:300],
        })
        if len(results) >= count:
            break
    return _format_search_results(query, results, output_format)


def _execute_search(func_args: dict) -> str:
    """Handle search tool calls via SearXNG (Issue #255).

    Queries the self-hosted SearXNG instance and returns results in the
    requested format. Returns an empty result gracefully on failure rather
    than raising an exception, to avoid breaking the agentic loop.

    Args:
        func_args: Dict with 'q' (required), 'count' (optional, default 5),
                   'format' (optional: 'json'|'text', default 'text').
    """
    query = (func_args.get("q") or "").strip()
    if not query:
        return "Error: search query ('q') is required"

    count_raw = func_args.get("count")
    count = min(int(count_raw if count_raw is not None else 5), 20)
    output_format = (func_args.get("format") or "text").lower()

    searxng_url = os.environ.get("WEE_SEARXNG_URL", "http://127.0.0.1:8888")
    searxng_url = searxng_url.rstrip("/")

    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "json",
            "categories": "general",
            "language": "en",
        }
    )
    url = f"{searxng_url}/search?{params}"
    try:
        headers = {
            "User-Agent": "Wee-Runtime/1.0",
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1"
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError:
        return _execute_brave_search(query, count, output_format, searxng_url)
    except Exception as e:
        return f"Search error: {e}"

    results = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("content", "") or "")[:300],
        }
        for item in data.get("results", [])[:count]
    ]
    return _format_search_results(query, results, output_format)


def resolve_model_and_endpoint(model: str, api_base: str = None, api_key: str = None):
    """Parse model string and resolve API endpoint.

    Model format: [provider/]model_name
    Examples:
        ollama/gemma4:e4b  → api_base=ollama preset, model=gemma4:e4b
        openrouter/meta-llama/llama-4-scout
            → api_base=openrouter, model=meta-llama/llama-4-scout
        gemma4:e4b         → use explicit api_base or default to ollama
    """
    resolved_model = model
    resolved_base = api_base
    resolved_key = api_key

    # Check for provider prefix
    for prefix, (preset_base, preset_key) in PROVIDER_PRESETS.items():
        if model.lower().startswith(f"{prefix}/"):
            resolved_model = model[len(prefix) + 1 :]
            if not resolved_base:
                resolved_base = preset_base
            if not resolved_key and preset_key:
                resolved_key = preset_key
            break

    # Defaults
    if not resolved_base:
        resolved_base = os.environ.get(
            "WEE_API_BASE", PROVIDER_PRESETS["ollama"][0]
        )
    if not resolved_key:
        # Issue #153: Check OPENROUTER_API_KEY env var for OpenRouter first
        if "openrouter" in (resolved_base or "").lower():
            resolved_key = os.environ.get("OPENROUTER_API_KEY")
        if not resolved_key:
            resolved_key = os.environ.get("WEE_API_KEY")
        if not resolved_key and "openrouter" in (resolved_base or "").lower():
            resolved_key = _webui_secret("OPENROUTER_API_KEY")
        # Try keyring for OpenRouter
        if not resolved_key and "openrouter" in (resolved_base or "").lower():
            try:
                import keyring

                resolved_key = keyring.get_password("openrouter", "api_key")
            except Exception:
                pass
        # Issue #153: Raise clear error instead of defaulting to "ollama"
        if not resolved_key:
            if "openrouter" in (resolved_base or "").lower():
                print(
                    "Error: OpenRouter API key not found. Save OPENROUTER_API_KEY "
                    "in Wee Secrets, set it in the service environment, or store "
                    "it in the system keyring.",
                    file=sys.stderr,
                )
                sys.exit(1)
            resolved_key = "ollama"

    return resolved_model, resolved_base, resolved_key



# Issue #113: Anti-hallucination system prompt addendum
_ANTI_HALLUCINATION_PROMPT = (
    "\n\n[CRITICAL — Output Integrity Rules]\n"
    "1. NEVER fabricate, invent, or hallucinate command output. If a command "
    "fails or you cannot execute it, report the EXACT error message.\n"
    "2. NEVER provide example or placeholder output and present it as real. "
    'If you show an example, clearly label it as "EXAMPLE (not real output)".\n'
    "3. When a tool call returns an error, relay the error verbatim to the user. "
    "Do NOT attempt to guess what the successful output would have looked like.\n"
    "4. For SSH commands: ALWAYS use ``-o StrictHostKeyChecking=accept-new`` to "
    "avoid host-key verification failures on first connect.\n"
)

def list_available_models(provider: str = None):
    """List available models from all configured providers.

    Args:
        provider: Optional filter ('ollama', 'openrouter', 'lmstudio').
                 If None, shows all. Case-insensitive.
    """
    import httpx

    _ollama_base = (
        os.environ.get("WEE_OLLAMA_BASE_URL")
        or os.environ.get("WEE_OLLAMA_HOST")
        or os.environ.get("OLLAMA_HOST")
        or "http://192.168.1.101:11434"
    ).rstrip("/")
    if not _ollama_base.startswith(("http://", "https://")):
        _ollama_base = f"http://{_ollama_base}"
    if _ollama_base.endswith("/v1"):
        _ollama_base = _ollama_base[:-3]

    if provider:
        provider = provider.lower()

    print("Available Models by Provider:")
    print("=" * 60)

    # Ollama models
    if not provider or provider == "ollama":
        print(f"\nOllama ({_ollama_base}):")
        try:
            resp = httpx.get(
                f"{_ollama_base}/api/tags",
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0),
            )
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                for m in models:
                    name = m.get("name", "")
                    size_bytes = m.get("size", 0)
                    size_gb = size_bytes / (1024**3)
                    print(f"  ollama/{name:<40} ({size_gb:.1f} GB)")
                if not models:
                    print("  (no models available)")
            else:
                print("  (unreachable)")
        except Exception as e:
            print(f"  (error: {e})")

    # OpenRouter models
    if not provider or provider == "openrouter":
        print("\nOpenRouter (https://openrouter.ai):")
        try:
            resp = httpx.get("https://openrouter.ai/api/v1/models", timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                if models:
                    print(f"  Found {len(models)} models. Use 'openrouter/<model-id>'")
                    providers_dict = {}
                    for m in models:
                        model_id = m.get("id", "")
                        if "/" in model_id:
                            prov = model_id.split("/")[0]
                            if prov not in providers_dict:
                                providers_dict[prov] = []
                            providers_dict[prov].append(model_id)
                    for prov in sorted(providers_dict.keys()):
                        print(f"    {prov}:")
                        for model_id in sorted(providers_dict[prov]):
                            print(f"      openrouter/{model_id}")
                else:
                    print("  (no models available)")
            else:
                print(f"  (error: HTTP {resp.status_code})")
        except Exception as e:
            print(f"  (error: {e})")

    # LM Studio
    if not provider or provider == "lmstudio":
        print("\nLM Studio (http://localhost:1234):")
        _lmstudio_host = os.environ.get("LMSTUDIO_HOST", "localhost")
        _lmstudio_port = os.environ.get("LMSTUDIO_PORT", "1234")
        try:
            resp = httpx.get(
                f"http://{_lmstudio_host}:{_lmstudio_port}/v1/models",
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0),
            )
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                if models:
                    for m in models:
                        model_id = m.get("id", "")
                        print(f"  lmstudio/{model_id}")
                else:
                    print("  (no models available)")
            else:
                print("  (unreachable or no models loaded)")
        except Exception as e:
            print(f"  (unreachable: {e})")


def main():
    parser = argparse.ArgumentParser(
        description="Wee Native Runtime — OpenAI-compatible chat completions"
    )
    parser.add_argument(
        "--list-models",
        nargs="?",
        const=True,
        metavar="PROVIDER",
        help="List available models (optional: ollama, openrouter, lmstudio)",
    )
    parser.add_argument("--model", help="Model name (e.g., ollama/gemma4:e4b)")
    parser.add_argument("--api-base", default=None, help="API base URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--system-prompt", default="", help="System prompt")
    parser.add_argument(
        "--timeout", type=int, default=300, help="Request timeout in seconds"
    )
    parser.add_argument(
        "--temperature", type=float, default=None, help="Sampling temperature"
    )
    parser.add_argument(
        "--tools",
        action="store_true",
        default=False,
        help="Enable tool calling via the Copilot SDK session",
    )
    parser.add_argument("prompt", nargs="?", help="User prompt")
    args = parser.parse_args()

    # Handle --list-models
    if args.list_models is not None:
        provider = None if args.list_models is True else args.list_models
        list_available_models(provider)
        sys.exit(0)

    # Validate required arguments for normal operation
    if not args.model:
        parser.error("--model is required (unless using --list-models)")
    if not args.prompt:
        parser.error("prompt is required (unless using --list-models)")

    # Issue #443: the Copilot SDK BYOK executor is the only execution path.
    # No OpenAI-compatible fallback loop — a Copilot SDK failure is a real
    # error and must surface as one, not silently degrade to a fragile
    # hand-rolled tool loop.
    from wee_copilot_sdk import execute_wee_copilot, resolve_wee_provider

    route = resolve_wee_provider(
        args.model, api_base=args.api_base, api_key=args.api_key
    )
    sdk_prompt = (args.system_prompt or "") + _ANTI_HALLUCINATION_PROMPT
    sdk_prompt += f"\n\n[USER]\n{args.prompt}"
    sdk_streamed = [False]

    def _sdk_event(kind, payload):
        if kind == "chunk":
            sdk_streamed[0] = True
            sys.stdout.write(str(payload))
            sys.stdout.flush()

    try:
        sdk_output, _ = execute_wee_copilot(
            prompt=sdk_prompt,
            route=route,
            working_directory=os.getcwd(),
            timeout=float(args.timeout),
            enable_tools=args.tools,
            event_callback=_sdk_event,
        )
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as sdk_error:
        print(f"Error: Wee Copilot SDK failed: {sdk_error}", file=sys.stderr)
        sys.exit(1)

    if sdk_streamed[0]:
        sys.stdout.write("\n")
    elif sdk_output:
        sys.stdout.write(sdk_output + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
