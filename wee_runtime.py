#!/usr/bin/env python3  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
"""Wee Native Runtime — OpenAI-compatible chat completion backend.  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
Standalone CLI for use in background tasks. Streams response tokens to stdout.  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
Supports Ollama, OpenRouter, LM Studio, and any OpenAI-compatible API.  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
Issue #107: Tool-calling agentic loop — detects tool calls, executes bash/python,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
re-sends results to model, and streams the final response.  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
Usage:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    python3 wee_runtime.py --model MODEL --api-base URL [--api-key KEY] "PROMPT"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    python3 wee_runtime.py --model ollama/qwen3:8b --tools "ask what day it is"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
"""  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501

# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
import argparse  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
import re  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
import json  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
import os  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
import subprocess  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
import sys  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501

# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# Provider presets: prefix → (api_base, default_api_key)  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
PROVIDER_PRESETS = {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "ollama": (
        "http://192.168.1.101:11434/v1",
        "ollama",
    ),  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "openrouter": (
        "https://openrouter.ai/api/v1",
        None,
    ),  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "lmstudio": (
        "http://localhost:1234/v1",
        "lm-studio",
    ),  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
}  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# Tool definitions (Issue #107)  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
_WEE_TOOLS = [  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        "type": "function",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        "function": {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "name": "bash",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "description": "Execute a bash shell command and return its output.",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "parameters": {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                "type": "object",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                "properties": {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    "command": {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "type": "string",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "description": "The bash command to execute",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    }  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                },  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                "required": [
                    "command"
                ],  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            },  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        },  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    },  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        "type": "function",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        "function": {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "name": "python",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "description": "Execute Python 3 code and return the output.",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "parameters": {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                "type": "object",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                "properties": {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    "code": {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "type": "string",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "description": "The Python code to execute",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    }  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                },  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                "required": [
                    "code"
                ],  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            },  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        },  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    },  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
]  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
MAX_TOOL_ROUNDS = 10  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
TOOL_TIMEOUT = 120  # seconds per tool execution  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501


# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
def resolve_model_and_endpoint(
    model: str, api_base: str = None, api_key: str = None
):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    """Parse model string and resolve API endpoint.  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
      Model format: [provider/]model_name  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
      Examples:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
          ollama/gemma4:e4b  → api_base=ollama preset, model=gemma4:e4b  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
          openrouter/meta-llama/llama-4-scout → api_base=openrouter, model=meta-llama/llama-4-scout  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
          gemma4:e4b         → use explicit api_base or default to ollama  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    """  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    resolved_model = model  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    resolved_base = api_base  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    resolved_key = api_key  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # Check for provider prefix  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    for prefix, (
        preset_base,
        preset_key,
    ) in (
        PROVIDER_PRESETS.items()
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        if model.lower().startswith(
            f"{prefix}/"
        ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            resolved_model = model[
                len(prefix) + 1 :
            ]  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                not resolved_base
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                resolved_base = preset_base  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                not resolved_key and preset_key
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                resolved_key = preset_key  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            break  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # Defaults  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    if (
        not resolved_base
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        resolved_base = os.environ.get(  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "WEE_API_BASE",
            "http://192.168.1.101:11434/v1",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    if (
        not resolved_key
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        resolved_key = os.environ.get(
            "WEE_API_KEY"
        ) or os.environ.get(  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "OPENROUTER_API_KEY"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        if (
            not resolved_key
        ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                "openrouter" in (resolved_base or "").lower()
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                try:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    import keyring  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501

                    resolved_key = keyring.get_password(
                        "openrouter", "api_key"
                    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                except (
                    Exception
                ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    pass  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                not resolved_key
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                resolved_key = "ollama"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    return (
        resolved_model,
        resolved_base,
        resolved_key,
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501


# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
def execute_tool(
    func_name: str, func_args: dict
) -> (
    str
):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    """Execute a tool call and return its output (Issues #107, #111)."""  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    try:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        if (
            func_name == "bash"
        ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            command = func_args.get(
                "command", ""
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                not command
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                return "Error: No command provided"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # Issue #111: Sanitize SSH commands  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            command = sanitize_bash_command(
                command
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            result = subprocess.run(  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                [
                    "bash",
                    "-c",
                    command,
                ],  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                capture_output=True,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                text=True,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                timeout=TOOL_TIMEOUT,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            output = (
                result.stdout
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                result.returncode != 0 and result.stderr
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                output += f"\nSTDERR: {result.stderr}"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            return (
                output.strip() or "(no output)"
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        elif (
            func_name == "python"
        ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            code = func_args.get(
                "code", ""
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                not code
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                return "Error: No code provided"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            result = subprocess.run(  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                [
                    sys.executable,
                    "-c",
                    code,
                ],  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                capture_output=True,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                text=True,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                timeout=TOOL_TIMEOUT,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            output = (
                result.stdout
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                result.returncode != 0 and result.stderr
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                output += f"\nSTDERR: {result.stderr}"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            return (
                output.strip() or "(no output)"
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        else:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            return f"Error: Unknown tool {func_name}"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    except (
        subprocess.TimeoutExpired
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        return f"Error: Tool {func_name} timed out after {TOOL_TIMEOUT}s"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    except (
        Exception
    ) as e:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        return f"Error executing tool {func_name}: {e}"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501


# Issue #113: SSH command sanitisation  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
_SSH_BIN_RE = re.compile(
    r"\b(ssh|scp|sftp)\b"
)  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501


# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# Issue #111: SSH sanitization now wired into execute_tool() (resolves #113 TODO).  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
def sanitize_bash_command(
    command: str,
) -> (
    str
):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    """Auto-inject SSH flags to prevent host key verification failures.  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
      Injects ``-o StrictHostKeyChecking=accept-new`` after ssh/scp/sftp  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
      when the flag is not already present.  ``accept-new`` is preferred  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
      over ``no`` because it still rejects CHANGED keys (potential MITM).  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
      Wired into execute_tool() by Issue #111. Called on every bash tool input before execution  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
      once wee_runtime.py gains a tool execution loop.  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    """  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    if not command or not _SSH_BIN_RE.search(
        command
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        return command  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    if (
        "StrictHostKeyChecking" in command
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        return command  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501

    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    def _inject(
        m,
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        return (
            m.group(0) + " -o StrictHostKeyChecking=accept-new"
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501

    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    return _SSH_BIN_RE.sub(
        _inject, command, count=0
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501


# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# Issue #113: Anti-hallucination system prompt addendum  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
_ANTI_HALLUCINATION_PROMPT = (  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "\n\n[CRITICAL — Output Integrity Rules]\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "1. NEVER fabricate, invent, or hallucinate command output. If a command "  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "fails or you cannot execute it, report the EXACT error message.\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "2. NEVER provide example or placeholder output and present it as real. "  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    'If you show an example, clearly label it as "EXAMPLE (not real output)".\n'  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "3. When a tool call returns an error, relay the error verbatim to the user. "  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "Do NOT attempt to guess what the successful output would have looked like.\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "4. For SSH commands: ALWAYS use ``-o StrictHostKeyChecking=accept-new`` to "  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "avoid host-key verification failures on first connect.\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
)  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# Issue #111: Explicit tool capability prompt for Ollama models.  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# Many Ollama models ignore JSON tool schemas unless the system prompt  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# explicitly tells them about tool availability and usage instructions.  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
_WEE_TOOL_CAPABILITY_PROMPT = (  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "\n\n[Tool Capabilities]\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "You have the following tools available. Use them to fulfil user requests.\n\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "1. **bash** — Execute shell commands. Parameters: {\"command\": \"<shell command>\"}\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "   Use this for: file operations, system commands, SSH, curl, git, etc.\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "2. **python** — Execute Python code. Parameters: {\"code\": \"<python code>\"}\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "   Use this for: data processing, calculations, scripting, etc.\n\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "IMPORTANT: You are NOT sandboxed. You CAN and SHOULD use these tools to execute\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "commands, SSH into remote hosts, read/write files, and interact with the system.\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    "NEVER claim you cannot execute commands or are in a sandbox — use the tools above.\n"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
)  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501


# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
def main():  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    parser = argparse.ArgumentParser(  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        description="Wee Native Runtime — OpenAI-compatible chat completions"  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    parser.add_argument(
        "--model", required=True, help="Model name (e.g., ollama/gemma4:e4b)"
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    parser.add_argument(
        "--api-base", default=None, help="API base URL"
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    parser.add_argument(
        "--api-key", default=None, help="API key"
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    parser.add_argument(
        "--system-prompt", default="", help="System prompt"
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    parser.add_argument(
        "--timeout", type=int, default=300, help="Request timeout in seconds"
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    parser.add_argument(
        "--temperature", type=float, default=None, help="Sampling temperature"
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    parser.add_argument(
        "--tools",
        action="store_true",
        default=False,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        help="Enable tool calling (bash, python)",
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    parser.add_argument(
        "prompt", help="User prompt"
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    args = (
        parser.parse_args()
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    model, api_base, api_key = (
        resolve_model_and_endpoint(  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            args.model,
            args.api_base,
            args.api_key,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        )
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    try:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        from openai import (
            OpenAI,
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    except (
        ImportError
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        print(
            "Error: openai package not installed. Run: pip install openai",
            file=sys.stderr,
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        sys.exit(
            1
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    import httpx  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501

    client = OpenAI(  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        base_url=api_base,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        api_key=api_key,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        timeout=httpx.Timeout(  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            timeout=float(
                args.timeout
            ),  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            connect=15.0,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        ),  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        max_retries=0,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    messages = (
        []
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # Issue #113: Augment system prompt with anti-hallucination rules  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    effective_system_prompt = (
        args.system_prompt or ""
    ) + _ANTI_HALLUCINATION_PROMPT  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # Issue #111: Include tool capability prompt when tools are enabled  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    if (
        args.tools
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        effective_system_prompt += _WEE_TOOL_CAPABILITY_PROMPT  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    if (
        effective_system_prompt.strip()
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        messages.append(
            {"role": "system", "content": effective_system_prompt}
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    messages.append(
        {"role": "user", "content": args.prompt}
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    if (
        not args.tools
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        # Simple streaming (no tool calling)  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        create_kwargs = {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "model": model,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "messages": messages,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            "stream": True,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        }  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        if (
            args.temperature is not None
        ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            create_kwargs["temperature"] = (
                args.temperature
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        try:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            stream = client.chat.completions.create(
                **create_kwargs
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            for (
                chunk
            ) in (
                stream
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                if (
                    chunk.choices and chunk.choices[0].delta.content
                ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    sys.stdout.write(
                        chunk.choices[0].delta.content
                    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    sys.stdout.flush()  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            sys.stdout.write(
                "\n"
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            sys.stdout.flush()  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        except (
            KeyboardInterrupt
        ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            sys.exit(
                130
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        except (
            Exception
        ) as e:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            print(
                f"\nError: Wee native runtime failed: {e}", file=sys.stderr
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            sys.exit(
                1
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        return  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # -- Tool-calling agentic loop (Issue #107) --  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    collected_output = (
        []
    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    tool_call_counter = 0  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    try:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        for round_num in range(
            MAX_TOOL_ROUNDS + 1
        ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            create_kwargs = {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                "model": model,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                "messages": messages,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                "stream": True,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            }  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                args.temperature is not None
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                create_kwargs["temperature"] = (
                    args.temperature
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                round_num < MAX_TOOL_ROUNDS
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                create_kwargs["tools"] = (
                    _WEE_TOOLS  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                )
            # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            try:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                stream = client.chat.completions.create(
                    **create_kwargs
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            except (
                Exception
            ) as tools_err:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                if (
                    "tools" in create_kwargs
                ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    print(  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        f"[Wee] Tools not supported, retrying without: {tools_err}",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        file=sys.stderr,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    create_kwargs.pop(
                        "tools", None
                    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    stream = client.chat.completions.create(
                        **create_kwargs
                    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                else:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    raise  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            round_content = (
                []
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            tool_calls_acc = (
                {}
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            for (
                chunk
            ) in (
                stream
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                if (
                    not chunk.choices
                ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    continue  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                delta = chunk.choices[
                    0
                ].delta  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                if (
                    delta.content
                ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    token = (
                        delta.content
                    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    round_content.append(
                        token
                    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    sys.stdout.write(
                        token
                    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    sys.stdout.flush()  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                if getattr(
                    delta, "tool_calls", None
                ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    for (
                        tc_delta
                    ) in (
                        delta.tool_calls
                    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        idx = (
                            tc_delta.index
                        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        if (
                            idx not in tool_calls_acc
                        ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                            tool_call_counter += 1  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                            tool_calls_acc[idx] = (
                                {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                                    "id": getattr(tc_delta, "id", None)
                                    or f"tc_wee_{tool_call_counter}",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                                    "name": "",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                                    "arguments": "",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                                }
                            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        if (
                            tc_delta.id
                        ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                            tool_calls_acc[idx][
                                "id"
                            ] = (
                                tc_delta.id
                            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        if (
                            tc_delta.function
                        ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                            if (
                                tc_delta.function.name
                            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                                tool_calls_acc[idx][
                                    "name"
                                ] = (
                                    tc_delta.function.name
                                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                            if (
                                tc_delta.function.arguments
                            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                                tool_calls_acc[idx][
                                    "arguments"
                                ] += (
                                    tc_delta.function.arguments
                                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            content_text = "".join(
                round_content
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                not tool_calls_acc
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                collected_output.append(
                    content_text
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                break  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # Tool calls detected  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            print(  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                f"\n[Wee] Round {round_num + 1}: {len(tool_calls_acc)} tool call(s)",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                file=sys.stderr,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            assistant_tool_calls = (
                []
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            for idx in sorted(
                tool_calls_acc.keys()
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                tc = tool_calls_acc[
                    idx
                ]  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                assistant_tool_calls.append(
                    {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "id": tc[
                            "id"
                        ],  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "type": "function",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        },  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    }
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            messages.append(
                {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    "role": "assistant",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    "content": content_text
                    or None,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    "tool_calls": assistant_tool_calls,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                }
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            for (
                tc_entry
            ) in (
                assistant_tool_calls
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                tc_id = tc_entry[
                    "id"
                ]  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                func_name = tc_entry["function"][
                    "name"
                ]  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                func_args_str = tc_entry["function"][
                    "arguments"
                ]  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                try:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    func_args = json.loads(
                        func_args_str
                    )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                except (
                    ValueError,
                    json.JSONDecodeError,
                ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    func_args = {
                        "raw": func_args_str
                    }  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                func_repr = json.dumps(func_args)[
                    :200
                ]  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                print(
                    f"[Wee] Tool: {func_name}({func_repr})", file=sys.stderr
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                # Issue #142: Emit structured JSON for bg task tool call tracking  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                tc_start = json.dumps(
                    {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "__wee_tc__": "start",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "id": tc_id,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "name": func_name,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "input": func_args,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    }
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                sys.stdout.write(
                    tc_start + "\n"
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                sys.stdout.flush()  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                tool_result = execute_tool(
                    func_name, func_args
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                # Issue #142: Emit tool result to stdout  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                tc_done = json.dumps(
                    {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "__wee_tc__": "done",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "id": tc_id,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "name": func_name,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "output": (tool_result or "")[
                            :500
                        ],  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    }
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                sys.stdout.write(
                    tc_done + "\n"
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                sys.stdout.flush()  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                messages.append(
                    {  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "role": "tool",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "tool_call_id": tc_id,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                        "content": tool_result
                        or "No output",  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                    }
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        else:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            # All rounds had tool calls with no final text  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            last_results = [
                m["content"] for m in messages if m.get("role") == "tool"
            ]  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            if (
                last_results
            ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                fallback = (
                    "Tool execution completed. Last result:\n" + last_results[-1][:2000]
                )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            else:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
                fallback = "Max tool rounds reached without final response."  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            collected_output.append(
                fallback
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
            sys.stdout.write(
                fallback
            )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        sys.stdout.write(
            "\n"
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        sys.stdout.flush()  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    except (
        KeyboardInterrupt
    ):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        sys.exit(
            130
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    except (
        Exception
    ) as e:  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        print(
            f"\nError: Wee native runtime failed: {e}", file=sys.stderr
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
        sys.exit(
            1
        )  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501


# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
# noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
if (
    __name__ == "__main__"
):  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    main()  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
