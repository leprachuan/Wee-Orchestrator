"""Tests for issue #491 — per-agent Webex token config + service control.

Covers:
- GET /api/v1/agents/{name}/bots/{channel}/status: running/stopped state,
  unsupported channel, unknown agent, invalid channel
- POST /api/v1/agents/{name}/bots/{channel}/restart: unit-name mapping
  (orchestrator -> the existing singleton service; other agents -> their
  own webex-connector-agent@ instance), token-configured gating for
  non-orchestrator agents, systemctl failure handling, auth requirement
- webex_connector.py's _resolve_agent_bot_token resolves an arbitrary
  agent's own token, not just orchestrator's
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["API_SHARED_KEY"] = "test_key_491"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


def _make_agents_json(agents, tmpdir):
    path = os.path.join(tmpdir, "agents.json")
    with open(path, "w") as f:
        json.dump({"agents": agents}, f)
    return path


class TestResolveAgentBotToken(unittest.TestCase):
    """Unit tests for webex_connector._resolve_agent_bot_token."""

    @patch("subprocess.run")
    def test_resolves_a_non_orchestrator_agents_own_token(self, mock_run):
        from webex_connector import _resolve_agent_bot_token

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"status": "success", "credential": "smarthome_token_123"}),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_agents_json(
                [
                    {
                        "name": "smarthome",
                        "path": "/opt/smarthome",
                        "bots": {"webex": {"token_secret": "wee.agent.smarthome.webex.bot_token"}},
                    },
                    {"name": "orchestrator", "path": "/opt/"},
                ],
                tmpdir,
            )
            with patch("pathlib.Path.exists", return_value=True):
                result = _resolve_agent_bot_token("smarthome", "webex", path)
        self.assertEqual(result, "smarthome_token_123")

    def test_returns_none_when_agent_has_no_bots_configured(self):
        from webex_connector import _resolve_agent_bot_token

        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_agents_json([{"name": "orchestrator", "path": "/opt/"}], tmpdir)
            result = _resolve_agent_bot_token("orchestrator", "webex", path)
        self.assertIsNone(result)

    def test_returns_none_for_unknown_agent(self):
        from webex_connector import _resolve_agent_bot_token

        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_agents_json([{"name": "orchestrator", "path": "/opt/"}], tmpdir)
            result = _resolve_agent_bot_token("nonexistent-agent", "webex", path)
        self.assertIsNone(result)


class TestWebexServiceControlAPI(unittest.TestCase):
    """Integration tests for GET status / POST restart on the webex channel."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import agent_manager

        cls._telegram_patch = patch.object(
            agent_manager, "_resolve_telegram_identity", side_effect=lambda identity: identity
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = patch.object(
            agent_manager, "_send_pairing_code", return_value=True
        )
        cls._send_pairing_patch.start()

        cls.tmpdir = tempfile.mkdtemp()
        cls.orchestrator_dir = os.path.join(cls.tmpdir, "orchestrator")
        cls.smarthome_dir = os.path.join(cls.tmpdir, "smarthome")
        os.makedirs(cls.orchestrator_dir, exist_ok=True)
        os.makedirs(cls.smarthome_dir, exist_ok=True)

        config_file = _make_agents_json(
            [
                {"name": "orchestrator", "path": cls.orchestrator_dir, "description": "Orchestrator"},
                {
                    "name": "smarthome",
                    "path": cls.smarthome_dir,
                    "description": "Smarthome",
                    "bots": {"webex": {"token_secret": "wee.agent.smarthome.webex.bot_token"}},
                },
                {"name": "unconfigured-agent", "path": cls.smarthome_dir, "description": "No bot yet"},
            ],
            cls.tmpdir,
        )

        os.environ["AGENT_CONFIG_FILE"] = config_file
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.auth = {"Authorization": "Bearer shared_test_key_491"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()
        os.environ.pop("AGENT_CONFIG_FILE", None)
        import shutil

        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # ── status ───────────────────────────────────────────────────────────────

    def test_status_unknown_agent_returns_404(self):
        resp = self.client.get("/api/v1/agents/ghost/bots/webex/status", headers=self.auth)
        self.assertEqual(resp.status_code, 404)

    def test_status_invalid_channel_returns_400(self):
        resp = self.client.get("/api/v1/agents/orchestrator/bots/bogus/status", headers=self.auth)
        self.assertEqual(resp.status_code, 400)

    def test_status_telegram_channel_reports_unsupported(self):
        resp = self.client.get("/api/v1/agents/orchestrator/bots/telegram/status", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["supported"])

    def test_status_requires_auth(self):
        resp = self.client.get("/api/v1/agents/orchestrator/bots/webex/status")
        self.assertIn(resp.status_code, (401, 403))

    @patch("subprocess.run")
    def test_status_reports_running_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="active\n", stderr="")
        resp = self.client.get("/api/v1/agents/smarthome/bots/webex/status", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["running"])
        self.assertEqual(data["unit"], "webex-connector-agent@smarthome.service")
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0][:2], ["sudo", "systemctl"])

    @patch("subprocess.run")
    def test_status_reports_running_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=3, stdout="inactive\n", stderr="")
        resp = self.client.get("/api/v1/agents/smarthome/bots/webex/status", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["running"])

    @patch("subprocess.run")
    def test_status_unit_name_for_orchestrator_is_the_singleton_service(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="active\n", stderr="")
        resp = self.client.get("/api/v1/agents/orchestrator/bots/webex/status", headers=self.auth)
        self.assertEqual(resp.json()["unit"], "webex-connector-dev.service")

    # ── restart ──────────────────────────────────────────────────────────────

    def test_restart_unknown_agent_returns_404(self):
        resp = self.client.post("/api/v1/agents/ghost/bots/webex/restart", headers=self.auth)
        self.assertEqual(resp.status_code, 404)

    def test_restart_invalid_channel_returns_400(self):
        resp = self.client.post("/api/v1/agents/orchestrator/bots/bogus/restart", headers=self.auth)
        self.assertEqual(resp.status_code, 400)

    def test_restart_telegram_channel_returns_400(self):
        resp = self.client.post("/api/v1/agents/orchestrator/bots/telegram/restart", headers=self.auth)
        self.assertEqual(resp.status_code, 400)

    def test_restart_requires_auth(self):
        resp = self.client.post("/api/v1/agents/orchestrator/bots/webex/restart")
        self.assertIn(resp.status_code, (401, 403))

    def test_restart_rejects_unconfigured_non_orchestrator_agent(self):
        resp = self.client.post(
            "/api/v1/agents/unconfigured-agent/bots/webex/restart", headers=self.auth
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no webex bot token configured", resp.json()["detail"])

    @patch("subprocess.run")
    def test_restart_succeeds_for_configured_agent(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = self.client.post("/api/v1/agents/smarthome/bots/webex/restart", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "restarted")
        self.assertEqual(data["unit"], "webex-connector-agent@smarthome.service")
        args = mock_run.call_args[0][0]
        self.assertEqual(args, ["sudo", "systemctl", "restart", "webex-connector-agent@smarthome.service"])

    @patch("subprocess.run")
    def test_restart_does_not_require_token_check_for_orchestrator(self, mock_run):
        # orchestrator's token comes from a systemd Environment= var, not the
        # per-agent secret store -- restart must not gate on token-status.
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = self.client.post("/api/v1/agents/orchestrator/bots/webex/restart", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["unit"], "webex-connector-dev.service")

    @patch("subprocess.run")
    def test_restart_reports_systemctl_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Access denied")
        resp = self.client.post("/api/v1/agents/smarthome/bots/webex/restart", headers=self.auth)
        self.assertEqual(resp.status_code, 500)
        self.assertIn("Access denied", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
