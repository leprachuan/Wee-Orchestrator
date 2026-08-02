"""
The per-session browser and shell MCP configs embed the API shared key so the
MCP server can authenticate against the local API. Two exposures came with
that, both introduced alongside the browser feature and inherited verbatim by
the shell feature (#477) since it writes into the same directory the same way:

1. `.mcp-configs/` was not in .gitignore, so one `git add -A` in a checkout
   would have committed a live shared key.
2. The file was written with the default umask, leaving the key readable by
   any other local user.

These pin both, for both features. The key is real credential material, so
the guard needs to be a test rather than a convention.
"""

import json
import os
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_mcp_config_directory_is_gitignored():
    """A generated file holding a live key must never be committable."""
    result = subprocess.run(
        ["git", "check-ignore", "-v", ".mcp-configs/browser-abc123.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        ".mcp-configs/ is not gitignored, so a generated browser MCP config "
        "carrying the API shared key could be committed:\n" + result.stderr
    )


def test_generated_config_is_owner_only(tmp_path, monkeypatch):
    """The key must not be readable by other local users."""
    import agent_manager as am

    monkeypatch.setattr(am, "SCRIPT_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("API_SHARED_KEY", "k" * 64)

    manager = am.SessionManager()
    monkeypatch.setattr(
        manager, "get_or_create_session_data", lambda _sid: {"identity": "u", "channel": "webui"}
    )

    path = manager._wee_browser_mcp_config_file("sess-hygiene")
    assert path is not None, "config should have been written"

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)} — group/other can read the key"

    directory_mode = stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode)
    assert directory_mode == 0o700, f"expected 0700 on the directory, got {oct(directory_mode)}"


def test_generated_config_still_carries_what_the_server_needs(tmp_path, monkeypatch):
    """Hardening must not break the contents the MCP server depends on."""
    import agent_manager as am

    monkeypatch.setattr(am, "SCRIPT_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("API_SHARED_KEY", "k" * 64)

    manager = am.SessionManager()
    monkeypatch.setattr(
        manager,
        "get_or_create_session_data",
        lambda _sid: {"identity": "local-macos", "channel": "webui"},
    )

    path = manager._wee_browser_mcp_config_file("sess-contents")
    env = json.load(open(path))["mcpServers"]["wee-browser"]["env"]

    assert env["WEE_BROWSER_SESSION_ID"] == "sess-contents"
    assert env["WEE_API_TOKEN"].startswith("shared_")
    # Without these the ownership check rejects the call as a different user,
    # which is what made browser control fail in the first place.
    assert env["WEE_API_IDENTITY"] == "local-macos"
    assert env["WEE_API_CHANNEL"] == "webui"


def test_shell_mcp_config_directory_is_gitignored():
    """Same exposure, same file, different prefix -- pin it separately anyway."""
    result = subprocess.run(
        ["git", "check-ignore", "-v", ".mcp-configs/shell-abc123.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        ".mcp-configs/ is not gitignored, so a generated shell MCP config "
        "carrying the API shared key could be committed:\n" + result.stderr
    )


def test_generated_shell_config_is_owner_only(tmp_path, monkeypatch):
    """The key must not be readable by other local users."""
    import agent_manager as am

    monkeypatch.setattr(am, "SCRIPT_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("API_SHARED_KEY", "k" * 64)

    manager = am.SessionManager()
    monkeypatch.setattr(
        manager, "get_or_create_session_data", lambda _sid: {"identity": "u", "channel": "webui"}
    )

    path = manager._wee_shell_mcp_config_file("sess-shell-hygiene")
    assert path is not None, "config should have been written"

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)} — group/other can read the key"

    directory_mode = stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode)
    assert directory_mode == 0o700, f"expected 0700 on the directory, got {oct(directory_mode)}"


def test_generated_shell_config_still_carries_what_the_server_needs(tmp_path, monkeypatch):
    """Hardening must not break the contents the MCP server depends on."""
    import agent_manager as am

    monkeypatch.setattr(am, "SCRIPT_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("API_SHARED_KEY", "k" * 64)

    manager = am.SessionManager()
    monkeypatch.setattr(
        manager,
        "get_or_create_session_data",
        lambda _sid: {"identity": "local-macos", "channel": "webui"},
    )

    path = manager._wee_shell_mcp_config_file("sess-shell-contents")
    env = json.load(open(path))["mcpServers"]["wee-shell"]["env"]

    assert env["WEE_SHELL_SESSION_ID"] == "sess-shell-contents"
    assert env["WEE_API_TOKEN"].startswith("shared_")
    assert env["WEE_API_IDENTITY"] == "local-macos"
    assert env["WEE_API_CHANNEL"] == "webui"
