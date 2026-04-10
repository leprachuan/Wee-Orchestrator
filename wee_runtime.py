#!/usr/bin/env python3
"""Wee Native Runtime — OpenAI-compatible chat completion backend.

Standalone CLI for use in background tasks. Streams response tokens to stdout.
Supports Ollama, OpenRouter, LM Studio, and any OpenAI-compatible API.

Usage:
    python3 wee_runtime.py --model MODEL --api-base URL [--api-key KEY] "PROMPT"
"""

import argparse
import json
import os
import sys
import time


# Provider presets: prefix → (api_base, default_api_key)
PROVIDER_PRESETS = {
    "ollama": ("http://192.168.1.101:11436/v1", "ollama"),
    "openrouter": ("https://openrouter.ai/api/v1", None),
    "lmstudio": ("http://localhost:1234/v1", "lm-studio"),
}


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

    # Check for provider prefix
    for prefix, (preset_base, preset_key) in PROVIDER_PRESETS.items():
        if model.lower().startswith(f"{prefix}/"):
            resolved_model = model[len(prefix) + 1:]
            if not resolved_base:
                resolved_base = preset_base
            if not resolved_key and preset_key:
                resolved_key = preset_key
            break

    # Defaults
    if not resolved_base:
        resolved_base = os.environ.get(
            "WEE_API_BASE", "http://192.168.1.101:11436/v1"
        )
    if not resolved_key:
        # Try environment variables
        resolved_key = os.environ.get("WEE_API_KEY") or os.environ.get(
            "OPENROUTER_API_KEY"
        )
        if not resolved_key:
            # Try keyring for OpenRouter
            if "openrouter" in (resolved_base or "").lower():
                try:
                    import keyring
                    resolved_key = keyring.get_password("openrouter", "api_key")
                except Exception:
                    pass
            if not resolved_key:
                resolved_key = "ollama"  # Safe default for local endpoints

    return resolved_model, resolved_base, resolved_key


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
    }
    if args.temperature is not None:
        create_kwargs["temperature"] = args.temperature

    try:
        stream = client.chat.completions.create(**create_kwargs)

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                sys.stdout.write(token)
                sys.stdout.flush()

        # Final newline
        sys.stdout.write("\n")
        sys.stdout.flush()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"\nError: Wee native runtime failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
