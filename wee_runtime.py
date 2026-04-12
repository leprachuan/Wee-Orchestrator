#!/usr/bin/env python3
"""Wee Native Runtime — OpenAI-compatible chat completion backend.

Standalone CLI for use in background tasks. Streams response tokens to stdout.
Supports Ollama, OpenRouter, LM Studio, and any OpenAI-compatible API.

Issue #128: Adds token usage tracking, OpenRouter cost estimation,
__WEE_META__ output line, and JSONL logging to ~/.copilot/logs/token_usage.jsonl.

Usage:
    python3 wee_runtime.py --model MODEL --api-base URL [--api-key KEY] "PROMPT"
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


# Provider presets: prefix → (api_base, default_api_key)
PROVIDER_PRESETS = {
    "ollama": ("http://192.168.1.101:11434/v1", "ollama"),
    "openrouter": ("https://openrouter.ai/api/v1", None),
    "lmstudio": ("http://localhost:1234/v1", "lm-studio"),
}

# Log file for token usage
LOG_FILE = Path.home() / ".copilot" / "logs" / "token_usage.jsonl"


def resolve_model_and_endpoint(model: str, api_base: str = None, api_key: str = None):
    """Parse model string and resolve API endpoint.

    Model format: [provider/]model_name
    Examples:
        ollama/gemma4:e4b  → api_base=ollama preset, model=gemma4:e4b
        openrouter/meta-llama/llama-4-scout → api_base=openrouter, model=meta-llama/llama-4-scout
        gemma4:e4b         → use explicit api_base or default to ollama
    """
    resolved_model = model
    resolved_base = api_base
    resolved_key = api_key

    for prefix, (preset_base, preset_key) in PROVIDER_PRESETS.items():
        if model.lower().startswith(f"{prefix}/"):
            resolved_model = model[len(prefix) + 1:]
            if not resolved_base:
                resolved_base = preset_base
            if not resolved_key and preset_key:
                resolved_key = preset_key
            break

    if not resolved_base:
        resolved_base = os.environ.get(
            "WEE_API_BASE", "http://192.168.1.101:11434/v1"
        )
    if not resolved_key:
        resolved_key = os.environ.get("WEE_API_KEY") or os.environ.get(
            "OPENROUTER_API_KEY"
        )
        if not resolved_key:
            if "openrouter" in (resolved_base or "").lower():
                try:
                    import keyring
                    resolved_key = keyring.get_password("openrouter", "api_key")
                except Exception:
                    pass
            if not resolved_key:
                resolved_key = "ollama"

    return resolved_model, resolved_base, resolved_key


def fetch_openrouter_pricing() -> dict:
    """Fetch and cache OpenRouter model pricing (1h TTL).

    Returns dict of {model_id: {prompt: float, completion: float}} per-token prices.
    """
    import urllib.request

    cache_path = Path("/tmp/openrouter_pricing.json")
    now = time.time()

    if cache_path.exists() and (now - cache_path.stat().st_mtime) < 3600:
        try:
            with open(cache_path) as f:
                return json.load(f)
        except Exception:
            pass

    try:
        with urllib.request.urlopen(
            "https://openrouter.ai/api/v1/models", timeout=10
        ) as resp:
            data = json.loads(resp.read().decode())

        pricing = {}
        for model_info in data.get("data", []):
            mid = model_info.get("id", "")
            p = model_info.get("pricing", {})
            try:
                pricing[mid] = {
                    "prompt": float(p.get("prompt", 0) or 0),
                    "completion": float(p.get("completion", 0) or 0),
                }
            except (ValueError, TypeError):
                pass

        with open(cache_path, "w") as f:
            json.dump(pricing, f)
        return pricing
    except Exception as e:
        print(f"[wee_runtime] Could not fetch OpenRouter pricing: {e}", file=sys.stderr)
        return {}


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int, pricing: dict):
    """Calculate USD cost and a display label for the given model and token counts.

    Returns (cost_usd: float, label: str) where label is one of:
        'local'    — Ollama or local endpoint
        'free'     — OpenRouter free model
        '$0.0001'  — paid model with calculated cost
    """
    model_lower = model.lower()

    # Local/Ollama = free
    if model_lower.startswith("ollama/") or "192.168" in model_lower:
        return 0.0, "local"

    # Strip provider prefix for pricing lookup
    bare_model = model
    for prefix in ("openrouter/", "lmstudio/", "wee/"):
        if model_lower.startswith(prefix):
            bare_model = model[len(prefix):]
            break

    if bare_model not in pricing:
        return 0.0, "free"

    p = pricing[bare_model]
    cost = (prompt_tokens * p["prompt"]) + (completion_tokens * p["completion"])

    if cost == 0.0:
        return 0.0, "free"
    if cost < 0.00001:
        label = f"${cost:.8f}".rstrip("0")
    elif cost < 0.001:
        label = f"${cost:.6f}".rstrip("0")
    else:
        label = f"${cost:.4f}".rstrip("0").rstrip(".")
    return cost, label


def log_token_usage(
    session_id: str,
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost_usd: float,
    duration_ms: int,
):
    """Append a usage entry to ~/.copilot/logs/token_usage.jsonl."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.time(),
            "session_id": session_id,
            "model": model,
            "runtime": "wee",
            "provider": provider,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
        }
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[wee_runtime] Failed to log usage: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Wee Native Runtime — OpenAI-compatible chat completions"
    )
    parser.add_argument("--model", required=True, help="Model name (e.g., ollama/gemma4:e4b)")
    parser.add_argument("--api-base", default=None, help="API base URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--system-prompt", default="", help="System prompt")
    parser.add_argument("--timeout", type=int, default=300, help="Request timeout in seconds")
    parser.add_argument("--temperature", type=float, default=None, help="Sampling temperature")
    parser.add_argument("--session-id", default="", help="Session ID for usage logging")
    parser.add_argument("prompt", help="User prompt")
    args = parser.parse_args()

    model, api_base, api_key = resolve_model_and_endpoint(
        args.model, args.api_base, args.api_key
    )

    try:
        from openai import OpenAI
    except ImportError:
        print("Error: openai package not installed. Run: pip install openai", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(
        base_url=api_base,
        api_key=api_key,
        timeout=args.timeout,
    )

    messages = []
    if args.system_prompt:
        messages.append({"role": "system", "content": args.system_prompt})
    messages.append({"role": "user", "content": args.prompt})

    create_kwargs = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if args.temperature is not None:
        create_kwargs["temperature"] = args.temperature

    start_time = time.time()
    last_usage = None

    try:
        stream = client.chat.completions.create(**create_kwargs)

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                sys.stdout.write(token)
                sys.stdout.flush()
            # Capture usage from final chunk
            if hasattr(chunk, "usage") and chunk.usage is not None:
                last_usage = chunk.usage

        sys.stdout.write("\n")
        sys.stdout.flush()

        # Compute token usage and cost (Issue #128)
        duration_ms = int((time.time() - start_time) * 1000)

        if last_usage is not None:
            prompt_tokens = getattr(last_usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(last_usage, "completion_tokens", 0) or 0
            total_tokens = getattr(last_usage, "total_tokens", prompt_tokens + completion_tokens)

            provider = "ollama" if "192.168" in api_base else ("openrouter" if "openrouter" in api_base else "wee")
            pricing = fetch_openrouter_pricing() if provider == "openrouter" else {}
            cost_usd, cost_label = calculate_cost(args.model, prompt_tokens, completion_tokens, pricing)

            meta = {
                "tokens": total_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost_usd,
                "cost_label": cost_label,
                "model": args.model,
                "runtime": "wee",
            }

            # Output metadata line for backend to parse
            print(f"__WEE_META__ {json.dumps(meta)}", flush=True)

            # Log to JSONL
            log_token_usage(
                session_id=args.session_id or "cli",
                model=args.model,
                provider=provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
            )

    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"\nError: Wee native runtime failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
