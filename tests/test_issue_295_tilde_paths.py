#!/usr/bin/env python3
"""Regression test for issue #295: UI rejects ~ in agent path."""

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")


def test_issue_295_tilde_path_expansion_on_load(tmp_path):
    """Backend should expand ~ paths when loading agents."""
    config_file = tmp_path / "agents.json"
    tilde_path = "~/test-agent"
    expected = os.path.expanduser(tilde_path)

    config_file.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "name": "test",
                        "path": tilde_path,
                        "description": "Test",
                    }
                ]
            }
        )
    )

    from agent_manager import SessionManager

    mgr = SessionManager()
    agents = mgr._load_agents_config(config_file)

    assert agents["test"]["path"] == expected


def test_issue_295_absolute_path_unchanged(tmp_path):
    """Absolute paths should be unchanged."""
    config_file = tmp_path / "agents.json"
    abs_path = "/opt/test"

    config_file.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "name": "test",
                        "path": abs_path,
                        "description": "Test",
                    }
                ]
            }
        )
    )

    from agent_manager import SessionManager

    mgr = SessionManager()
    agents = mgr._load_agents_config(config_file)

    assert agents["test"]["path"] == abs_path


def test_issue_295_tilde_path_expansion_on_reload(tmp_path):
    """reload_agents_from_disk() should also expand ~ paths."""
    config_file = tmp_path / "agents.json"
    tilde_path = "~/test-agent"
    expected = os.path.expanduser(tilde_path)

    config_file.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "name": "test",
                        "path": tilde_path,
                        "description": "Test",
                    }
                ]
            }
        )
    )

    from agent_manager import SessionManager

    mgr = SessionManager()
    # First load to set up _agents_config_path
    agents = mgr._load_agents_config(config_file)
    assert agents["test"]["path"] == expected

    # Now reload and verify ~ paths are still expanded
    success, msg = mgr.reload_agents_from_disk()
    assert success, f"reload failed: {msg}"
    assert (
        mgr.AGENTS["test"]["path"] == expected
    ), f"reload did not expand ~ paths: got {mgr.AGENTS['test']['path']}"


def test_issue_295_reload_preserves_all_fields(tmp_path):
    """reload_agents_from_disk() should preserve all agent fields."""
    config_file = tmp_path / "agents.json"

    config_file.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "name": "test",
                        "path": "~/test",
                        "description": "Test Agent",
                        "max_concurrent": 2,
                        "runtime": "claude",
                        "model": "claude-sonnet-4.6",
                        "primary_runtime": "copilot",
                        "primary_model": "gpt-5-sonnet",
                        "fallback_runtime": "opencode",
                        "fallback_model": "nvidia/llama",
                    }
                ]
            }
        )
    )

    from agent_manager import SessionManager

    mgr = SessionManager()
    agents = mgr._load_agents_config(config_file)

    # Reload and verify all fields
    success, msg = mgr.reload_agents_from_disk()
    assert success, f"reload failed: {msg}"

    agent = mgr.AGENTS["test"]
    assert agent["path"] == os.path.expanduser("~/test")
    assert agent["description"] == "Test Agent"
    assert agent["max_concurrent"] == 2
    assert agent["runtime"] == "claude"
    assert agent["model"] == "claude-sonnet-4.6"
    assert agent["primary_runtime"] == "copilot"
    assert agent["primary_model"] == "gpt-5-sonnet"
    assert agent["fallback_runtime"] == "opencode"
    assert agent["fallback_model"] == "nvidia/llama"
