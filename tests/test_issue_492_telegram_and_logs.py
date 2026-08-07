"""Tests for issue #492 — Telegram bot token config, restart control, and
per-agent/channel log viewing, extending #491's Webex-only support.

Covers:
- GET/POST /api/v1/agents/{name}/bots/telegram/{status,restart}: unit-name
  mapping (orchestrator -> the existing singleton service; other agents ->
  their own telegram-bot-listener-agent@ instance), token-configured gating
  for non-orchestrator agents
- GET /api/v1/agents/{name}/bots/{channel}/logs: unit resolution for both
  channels and both orchestrator/non-orchestrator agents, unknown agent,
  invalid channel, auth requirement, journalctl failure handling
- telegram_connector.py's _resolve_agent_bot_token resolves an arbitrary
  agent's own token, not just orchestrator's (see also
  test_issue166_agent_bot_manager.py for the original single-agent coverage)
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Deliberately the same literal as test_issue_491_webex_per_agent.py: both
# files set this module-level env var during pytest's collection phase
# (before any test executes), so whichever file collects last would silently
# clobber the other's key for the duration of the run if the values
# differed -- create_api_app() reads API_SHARED_KEY at setUpClass time, not
# at each file's own import time. Keeping the literal in sync avoids that.
os.environ["API_SHARED_KEY"] = "test_key_491"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8098"


def _make_agents_json(agents, tmpdir):
    path = os.path.join(tmpdir, "agents.json")
    with open(path, "w") as f:
        json.dump({"agents": agents}, f)
    return path


class TestResolveAgentBotTokenTelegram(unittest.TestCase):
    """Unit tests for telegram_connector._resolve_agent_bot_token."""

    @patch("subprocess.run")
    def test_resolves_a_non_orchestrator_agents_own_token(self, mock_run):
        from telegram_connector import _resolve_agent_bot_token

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"status": "success", "credential": "smarthome_tg_token"}),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_agents_json(
                [
                    {
                        "name": "smarthome",
                        "path": "/opt/smarthome",
                        "bots": {"telegram": {"token_secret": "wee.agent.smarthome.telegram.bot_token"}},
                    },
                    {"name": "orchestrator", "path": "/opt/"},
                ],
                tmpdir,
            )
            with patch("pathlib.Path.exists", return_value=True):
                result = _resolve_agent_bot_token("smarthome", "telegram", path)
        self.assertEqual(result, "smarthome_tg_token")

    def test_returns_none_for_unknown_agent(self):
        from telegram_connector import _resolve_agent_bot_token

        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_agents_json([{"name": "orchestrator", "path": "/opt/"}], tmpdir)
            result = _resolve_agent_bot_token("nonexistent-agent", "telegram", path)
        self.assertIsNone(result)


class TestBotServiceControlAndLogsAPI(unittest.TestCase):
    """Integration tests for status/restart/logs across both channels."""

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
                    "bots": {
                        "webex": {"token_secret": "wee.agent.smarthome.webex.bot_token"},
                        "telegram": {"token_secret": "wee.agent.smarthome.telegram.bot_token"},
                    },
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

    # ── telegram status/restart ─────────────────────────────────────────────

    @patch("subprocess.run")
    def test_telegram_status_unit_name_for_non_orchestrator_agent(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="active\n", stderr="")
        resp = self.client.get("/api/v1/agents/smarthome/bots/telegram/status", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["running"])
        self.assertEqual(data["unit"], "telegram-bot-listener-agent@smarthome.service")

    @patch("subprocess.run")
    def test_telegram_status_unit_name_for_orchestrator_is_the_singleton_service(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="active\n", stderr="")
        resp = self.client.get("/api/v1/agents/orchestrator/bots/telegram/status", headers=self.auth)
        self.assertEqual(resp.json()["unit"], "telegram-bot-listener-dev.service")

    def test_telegram_restart_rejects_unconfigured_non_orchestrator_agent(self):
        resp = self.client.post(
            "/api/v1/agents/unconfigured-agent/bots/telegram/restart", headers=self.auth
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no telegram bot token configured", resp.json()["detail"])

    @patch("subprocess.run")
    def test_telegram_restart_succeeds_for_configured_agent(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = self.client.post("/api/v1/agents/smarthome/bots/telegram/restart", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "restarted")
        self.assertEqual(data["unit"], "telegram-bot-listener-agent@smarthome.service")
        args = mock_run.call_args[0][0]
        self.assertEqual(
            args, ["sudo", "systemctl", "restart", "telegram-bot-listener-agent@smarthome.service"]
        )

    # ── logs ─────────────────────────────────────────────────────────────────

    def test_logs_unknown_agent_returns_404(self):
        resp = self.client.get("/api/v1/agents/ghost/bots/webex/logs", headers=self.auth)
        self.assertEqual(resp.status_code, 404)

    def test_logs_invalid_channel_returns_400(self):
        resp = self.client.get("/api/v1/agents/orchestrator/bots/bogus/logs", headers=self.auth)
        self.assertEqual(resp.status_code, 400)

    def test_logs_requires_auth(self):
        resp = self.client.get("/api/v1/agents/orchestrator/bots/webex/logs")
        self.assertIn(resp.status_code, (401, 403))

    @patch("subprocess.run")
    def test_logs_returns_journalctl_lines_for_webex(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="2026-08-06T12:00:00-0400 host webex-connector-agent-smarthome[1]: started\n"
            "2026-08-06T12:00:01-0400 host webex-connector-agent-smarthome[1]: connected\n",
            stderr="",
        )
        resp = self.client.get("/api/v1/agents/smarthome/bots/webex/logs", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["unit"], "webex-connector-agent@smarthome.service")
        self.assertEqual(len(data["lines"]), 2)
        self.assertIn("connected", data["lines"][1])
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[:2], ["journalctl", "-u"])
        self.assertIn("webex-connector-agent@smarthome.service", cmd)

    @patch("subprocess.run")
    def test_logs_returns_journalctl_lines_for_telegram_orchestrator(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = self.client.get("/api/v1/agents/orchestrator/bots/telegram/logs", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["unit"], "telegram-bot-listener-dev.service")
        self.assertEqual(data["lines"], [])

    @patch("subprocess.run")
    def test_logs_respects_lines_query_param(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = self.client.get(
            "/api/v1/agents/orchestrator/bots/webex/logs?lines=50", headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        cmd = mock_run.call_args[0][0]
        self.assertIn("50", cmd)

    def test_logs_rejects_out_of_range_lines(self):
        resp = self.client.get(
            "/api/v1/agents/orchestrator/bots/webex/logs?lines=99999", headers=self.auth
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
