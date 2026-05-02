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
