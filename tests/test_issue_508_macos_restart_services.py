"""Regression test for issue #508: POST /api/v1/settings/restart-services
returned a hard 404-worthy failure on macOS because the endpoint only knew
how to `systemctl restart` Linux service units. On macOS "Local" mode,
restarting must instead install/refresh a LaunchAgent for each connector and
use `launchctl bootstrap`/`kickstart`, matching how those processes already
run as independent units on Linux -- except for agent-manager-api itself,
which the macOS app manages directly as a process it owns, never as a
LaunchAgent, so restarting it here would race the very request handling it.
"""

import os
import sys
import plistlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_manager import create_api_app

try:
    from starlette.testclient import TestClient
except ImportError:
    from fastapi.testclient import TestClient


class TestMacOSRestartServices:
    @pytest.fixture
    def client(self):
        app = create_api_app()
        return TestClient(app, raise_server_exceptions=False)

    @pytest.fixture
    def auth_headers(self):
        # Several test files set API_SHARED_KEY at module-import time with
        # differing literals (see 3b95eb4's #491/#492 fix); whichever file
        # pytest collects first wins for the whole run. Reading the value
        # live here, rather than hardcoding the literal this file set,
        # keeps this test correct regardless of collection order.
        shared_key = os.environ.get("API_SHARED_KEY", "test_key_123")
        return {
            "Authorization": f"Bearer shared_{shared_key}",
            "X-User-Identity": "test-user",
            "X-Auth-Channel": "api",
        }

    def _mock_launchctl_success(self, *args, **kwargs):
        return MagicMock(returncode=0, stdout="", stderr="")

    def test_darwin_installs_launch_agents_and_uses_launchctl_not_systemctl(
        self, client, auth_headers, tmp_path
    ):
        with patch("platform.system", return_value="Darwin"), \
             patch("pathlib.Path.home", return_value=tmp_path), \
             patch("subprocess.run", side_effect=self._mock_launchctl_success) as mock_run:
            resp = client.post(
                "/api/v1/settings/restart-services", headers=auth_headers
            )

        assert resp.status_code == 200
        results = resp.json()["results"]

        assert set(results.keys()) == {
            "com.flipkey.wee-agent-manager-api",
            "com.flipkey.wee-webex-connector",
            "com.flipkey.wee-telegram-bot-listener",
            "com.flipkey.wee-task-scheduler-executor",
        }

        # The main API is never restarted via launchctl -- it isn't a
        # LaunchAgent on macOS, and killing it here would kill the request
        # handling this call.
        assert "not applicable" in results["com.flipkey.wee-agent-manager-api"]

        for label in [
            "com.flipkey.wee-webex-connector",
            "com.flipkey.wee-telegram-bot-listener",
            "com.flipkey.wee-task-scheduler-executor",
        ]:
            assert results[label] == "restarted", f"{label}: {results[label]}"

        # No systemctl anywhere on this path.
        for call in mock_run.call_args_list:
            argv = call.args[0]
            assert "systemctl" not in argv, f"Linux systemctl invoked on Darwin: {argv}"

        # bootout, bootstrap, and kickstart -k each ran once per connector.
        launchctl_calls = [c.args[0] for c in mock_run.call_args_list if c.args[0][0] == "launchctl"]
        assert sum(1 for c in launchctl_calls if c[1] == "bootout") == 3
        assert sum(1 for c in launchctl_calls if c[1] == "bootstrap") == 3
        assert sum(1 for c in launchctl_calls if c[1] == "kickstart") == 3

    def test_darwin_writes_a_valid_launch_agent_plist(
        self, client, auth_headers, tmp_path
    ):
        with patch("platform.system", return_value="Darwin"), \
             patch("pathlib.Path.home", return_value=tmp_path), \
             patch("subprocess.run", side_effect=self._mock_launchctl_success):
            resp = client.post(
                "/api/v1/settings/restart-services", headers=auth_headers
            )
        assert resp.status_code == 200

        plist_path = (
            tmp_path / "Library" / "LaunchAgents" / "com.flipkey.wee-webex-connector.plist"
        )
        assert plist_path.exists(), "restart must install the LaunchAgent plist"

        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)

        assert plist["Label"] == "com.flipkey.wee-webex-connector"
        assert plist["ProgramArguments"][0] == sys.executable
        assert plist["ProgramArguments"][1].endswith("webex_connector.py")
        # Static snapshot, not a live systemd EnvironmentFile= -- must at
        # least carry PATH/HOME so the interpreter and its imports resolve.
        assert "PATH" in plist["EnvironmentVariables"]

    def test_linux_still_uses_systemctl_unchanged(self, client, auth_headers):
        with patch("platform.system", return_value="Linux"), \
             patch("subprocess.run", side_effect=self._mock_launchctl_success) as mock_run:
            resp = client.post(
                "/api/v1/settings/restart-services", headers=auth_headers
            )

        assert resp.status_code == 200
        results = resp.json()["results"]
        assert set(results.keys()) == {
            "agent-manager-api-dev.service",
            "task-scheduler-executor-dev.service",
            "webex-connector-dev.service",
            "telegram-bot-listener-dev.service",
        }
        assert all(v == "restarted" for v in results.values())
        for call in mock_run.call_args_list:
            assert call.args[0][:2] == ["systemctl", "restart"]
