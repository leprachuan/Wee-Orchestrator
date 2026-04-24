"""Tests for F020: Slash command registry that bypasses the LLM.

Every slash command is now dispatched through the registry with a
callable handler.  None of them touch the LLM.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "8099")


class TestSlashCommandRegistry(unittest.TestCase):
    """Test the slash command registry infrastructure."""

    @classmethod
    def setUpClass(cls):
        import agent_manager

        cls.sm = agent_manager.SessionManager(
            config_file=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "agents.json",
            ),
            app_env="DEV",
        )

    def test_registry_populated(self):
        """Registry should contain all known slash commands."""
        reg = self.sm._slash_command_registry
        self.assertGreater(len(reg), 15)

    def test_registry_contains_secret(self):
        """/secret should be in the registry with a handler."""
        entry = self.sm._slash_command_registry.get("/secret")
        self.assertIsNotNone(entry)
        self.assertIsNotNone(entry["handler"])
        self.assertIn("secret", entry["description"].lower())

    def test_all_commands_have_handlers(self):
        """F020: Every registered command must have a callable handler."""
        for cmd, entry in self.sm._slash_command_registry.items():
            self.assertIsNotNone(
                entry["handler"],
                f"{cmd} is registered without a handler",
            )
            self.assertTrue(
                callable(entry["handler"]),
                f"{cmd} handler is not callable",
            )

    def test_all_expected_commands_registered(self):
        """All known slash commands should be present in the registry."""
        expected = [
            "/help",
            "/status",
            "/cancel",
            "/capabilities",
            "/runtime",
            "/model",
            "/agent",
            "/session",
            "/timeout",
            "/render",
            "/notifications",
            "/mode",
            "/schedule",
            "/background",
            "/update",
            "/upgrade",
            "/pull",
            "/secret",
        ]
        reg = self.sm._slash_command_registry
        for cmd in expected:
            self.assertIn(cmd, reg, f"{cmd} not in registry")

    def test_update_upgrade_pull_share_handler(self):
        """/update, /upgrade, /pull should map to the same handler."""
        h_update = self.sm._slash_command_registry["/update"]["handler"]
        h_upgrade = self.sm._slash_command_registry["/upgrade"]["handler"]
        h_pull = self.sm._slash_command_registry["/pull"]["handler"]
        self.assertIs(h_update.__func__, h_upgrade.__func__)
        self.assertIs(h_update.__func__, h_pull.__func__)

    def test_register_slash_adds_command(self):
        """register_slash should add a new command to the registry."""

        def handler(a, s, n):
            return "test"

        self.sm._register_slash("/test_f020", handler, "Test command")
        entry = self.sm._slash_command_registry.get("/test_f020")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["handler"], handler)
        self.assertEqual(entry["description"], "Test command")
        del self.sm._slash_command_registry["/test_f020"]

    def test_get_slash_commands_returns_dict(self):
        """get_slash_commands should return {command: description}."""
        cmds = self.sm.get_slash_commands()
        self.assertIsInstance(cmds, dict)
        self.assertIn("/secret", cmds)
        self.assertIn("/help", cmds)


class TestSlashHandlerBehavior(unittest.TestCase):
    """Test that extracted handlers return correct responses."""

    @classmethod
    def setUpClass(cls):
        import agent_manager

        cls.sm = agent_manager.SessionManager(
            config_file=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "agents.json",
            ),
            app_env="DEV",
        )
        cls.session_id = "test_f020_handlers"
        cls.session_data = cls.sm.get_or_create_session_data(cls.session_id)

    def test_help_returns_commands(self):
        result = self.sm._slash_help(None, self.session_data, self.session_id)
        self.assertIn("Available Commands", result)
        self.assertIn("/secret", result)
        self.assertIn("/runtime", result)
        self.assertIn("/model", result)
        self.assertIn("/agent", result)

    def test_status_no_running(self):
        result = self.sm._slash_status(None, self.session_data, self.session_id)
        self.assertIn("No running query", result)

    def test_cancel_no_running(self):
        result = self.sm._slash_cancel(None, self.session_data, self.session_id)
        self.assertIn("No running query", result)

    def test_capabilities_returns_text(self):
        result = self.sm._slash_capabilities(None, self.session_data, self.session_id)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 50)

    def test_runtime_list(self):
        result = self.sm._slash_runtime("list", self.session_data, self.session_id)
        self.assertIn("copilot", result)
        self.assertIn("claude", result)
        self.assertIn("cursor", result)

    def test_runtime_current(self):
        result = self.sm._slash_runtime("current", self.session_data, self.session_id)
        self.assertIn("Current Runtime", result)

    def test_runtime_no_args(self):
        result = self.sm._slash_runtime(None, self.session_data, self.session_id)
        self.assertIn("Usage", result)

    def test_model_list(self):
        result = self.sm._slash_model("list", self.session_data, self.session_id)
        self.assertIn("model", result.lower())

    def test_model_current(self):
        result = self.sm._slash_model("current", self.session_data, self.session_id)
        self.assertIn("Current Model", result)

    def test_agent_list(self):
        result = self.sm._slash_agent("list", self.session_data, self.session_id)
        self.assertIn("orchestrator", result)

    def test_agent_current(self):
        result = self.sm._slash_agent("current", self.session_data, self.session_id)
        self.assertIn("Current Agent", result)

    def test_agent_no_args(self):
        result = self.sm._slash_agent(None, self.session_data, self.session_id)
        self.assertIn("Usage", result)

    def test_timeout_current(self):
        result = self.sm._slash_timeout(None, self.session_data, self.session_id)
        self.assertIn("Timeout", result)

    def test_timeout_set_valid(self):
        result = self.sm._slash_timeout("set 120", self.session_data, self.session_id)
        self.assertIn("120", result)

    def test_timeout_set_too_low(self):
        result = self.sm._slash_timeout("set 5", self.session_data, self.session_id)
        self.assertIn("at least 30", result)

    def test_render_current(self):
        result = self.sm._slash_render(None, self.session_data, self.session_id)
        self.assertIn("Render Type", result)

    def test_render_set_valid(self):
        result = self.sm._slash_render(
            "set markdown", self.session_data, self.session_id
        )
        self.assertIn("markdown", result)

    def test_render_set_invalid(self):
        result = self.sm._slash_render(
            "set invalid_format", self.session_data, self.session_id
        )
        self.assertIn("Invalid", result)

    def test_notifications_status(self):
        result = self.sm._slash_notifications(None, self.session_data, self.session_id)
        self.assertIn("Notifications", result)

    def test_mode_current(self):
        result = self.sm._slash_mode("current", self.session_data, self.session_id)
        self.assertIn("Mode", result)

    def test_mode_list(self):
        result = self.sm._slash_mode("list", self.session_data, self.session_id)
        self.assertIn("elevated", result)
        self.assertIn("restricted", result)
        self.assertIn("sandboxed", result)

    def test_background_help(self):
        result = self.sm._slash_background(None, self.session_data, self.session_id)
        self.assertIn("Background Task", result)

    def test_background_list(self):
        result = self.sm._slash_background("list", self.session_data, self.session_id)
        # Returns task list (possibly empty) or error if scheduler not init
        self.assertIsInstance(result, str)
        self.assertTrue(
            "Background" in result
            or "No background" in result
            or "error" in result.lower()
            or "task" in result.lower(),
            f"Unexpected background list result: {result[:100]}",
        )


class TestSlashSecretHandler(unittest.TestCase):
    """Test the /secret slash command handler."""

    @classmethod
    def setUpClass(cls):
        import agent_manager

        cls.sm = agent_manager.SessionManager(
            config_file=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "agents.json",
            ),
            app_env="DEV",
        )

    def test_secret_no_args_shows_help(self):
        result = self.sm._slash_secret(None, {}, "test-session")
        self.assertIn("Secret Commands", result)
        self.assertIn("/secret list", result)
        self.assertIn("/secret set", result)
        self.assertIn("/secret delete", result)

    def test_secret_unknown_subcommand_shows_help(self):
        result = self.sm._slash_secret("blah", {}, "test-session")
        self.assertIn("Secret Commands", result)

    def test_secret_list(self):
        result = self.sm._slash_secret("list", {}, "test-session")
        self.assertTrue(
            "Secrets" in result or "none" in result.lower(),
            f"Unexpected list result: {result[:100]}",
        )

    def test_secret_set_and_delete(self):
        # Clean up any stale secret from a previous failed run
        self.sm._slash_secret("delete _f020_test_secret", {}, "test-session")

        result = self.sm._slash_secret(
            "set _f020_test_secret testvalue123", {}, "test-session"
        )
        self.assertIn("_f020_test_secret", result)
        self.assertIn("created", result)

        result = self.sm._slash_secret("list", {}, "test-session")
        self.assertIn("_f020_test_secret", result)

        result = self.sm._slash_secret(
            "set _f020_test_secret newvalue456", {}, "test-session"
        )
        self.assertIn("updated", result)

        result = self.sm._slash_secret("delete _f020_test_secret", {}, "test-session")
        self.assertIn("deleted", result)

    def test_secret_set_missing_value(self):
        result = self.sm._slash_secret("set onlyname", {}, "test-session")
        self.assertIn("Usage", result)

    def test_secret_set_missing_all(self):
        result = self.sm._slash_secret("set", {}, "test-session")
        self.assertIn("Secret Commands", result)

    def test_secret_set_invalid_name(self):
        result = self.sm._slash_secret("set ../evil/path myvalue", {}, "test-session")
        self.assertIn("Invalid name", result)

    def test_secret_set_slash_in_name(self):
        result = self.sm._slash_secret("set na/me value", {}, "test-session")
        self.assertIn("Invalid name", result)

    def test_secret_delete_invalid_name(self):
        result = self.sm._slash_secret("delete ../evil", {}, "test-session")
        self.assertIn("Invalid name", result)

    def test_secret_delete_nonexistent(self):
        result = self.sm._slash_secret(
            "delete _f020_nonexistent_secret_xyz", {}, "test-session"
        )
        self.assertTrue(
            "\u274c" in result or "not found" in result.lower(),
            f"Expected error for nonexistent: {result[:100]}",
        )

    def test_secret_value_with_spaces(self):
        result = self.sm._slash_secret(
            "set _f020_space_test hello world test", {}, "test-session"
        )
        self.assertIn("_f020_space_test", result)
        self.assertIn("created", result)
        self.sm._slash_secret("delete _f020_space_test", {}, "test-session")


class TestSlashRegistryInExecute(unittest.TestCase):
    """Test that the registry dispatch works in the execute() flow."""

    @classmethod
    def setUpClass(cls):
        import agent_manager

        cls.sm = agent_manager.SessionManager(
            config_file=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "agents.json",
            ),
            app_env="DEV",
        )

    def test_execute_routes_secret_to_registry(self):
        session_id = "test_f020_exec"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/secret list", session_id)
        self.assertTrue(
            "Secrets" in result or "none" in result.lower(),
            f"Expected secrets list, got: {result[:100]}",
        )

    def test_execute_routes_help(self):
        """F020: /help dispatched via registry, not legacy chain."""
        session_id = "test_f020_help"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/help", session_id)
        self.assertIn("Available Commands", result)
        self.assertIn("/secret", result)

    def test_execute_routes_status(self):
        session_id = "test_f020_status_exec"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/status", session_id)
        self.assertIn("No running query", result)

    def test_execute_routes_cancel(self):
        session_id = "test_f020_cancel_exec"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/cancel", session_id)
        self.assertIn("No running query", result)

    def test_execute_routes_runtime_list(self):
        session_id = "test_f020_rt_exec"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/runtime list", session_id)
        self.assertIn("copilot", result)

    def test_execute_routes_model_list(self):
        session_id = "test_f020_model_exec"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/model list", session_id)
        self.assertIn("model", result.lower())

    def test_execute_routes_agent_list(self):
        session_id = "test_f020_agent_exec"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/agent list", session_id)
        self.assertIn("orchestrator", result)

    def test_execute_routes_timeout(self):
        session_id = "test_f020_timeout_exec"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/timeout", session_id)
        self.assertIn("Timeout", result)

    def test_execute_secret_set_bypasses_llm(self):
        session_id = "test_f020_bypass"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/secret set _f020_bypass_test bypassval", session_id)
        self.assertTrue(
            len(result) < 200,
            f"Response too long — possibly sent to LLM: {result[:200]}",
        )
        self.assertIn("_f020_bypass_test", result)
        self.sm.execute("/secret delete _f020_bypass_test", session_id)


class TestSlashSecretInAPI(unittest.TestCase):
    """Test /secret commands through the HTTP API endpoints."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import agent_manager

        cls._telegram_patch = patch.object(
            agent_manager,
            "_resolve_telegram_identity",
            side_effect=lambda identity: identity,
        )
        cls._telegram_patch.start()
        cls._send_pairing_patch = patch.object(
            agent_manager,
            "_send_pairing_code",
            return_value=True,
        )
        cls._send_pairing_patch.start()
        cls.app = agent_manager.create_api_app()
        cls.client = TestClient(cls.app)
        cls.auth_header = {"Authorization": "Bearer shared_test_key_123"}

    @classmethod
    def tearDownClass(cls):
        cls._telegram_patch.stop()
        cls._send_pairing_patch.stop()

    def _create_session(self):
        resp = self.client.post(
            "/api/v1/sessions/create",
            json={"agent": "orchestrator", "runtime": "copilot"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["session_id"]

    def test_secret_list_via_execute(self):
        sid = self._create_session()
        resp = self.client.post(
            f"/api/v1/sessions/{sid}/execute",
            json={"query": "/secret list"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("response", data)
        self.assertTrue(
            "Secrets" in data["response"] or "none" in data["response"].lower()
        )

    def test_secret_set_via_execute(self):
        sid = self._create_session()
        resp = self.client.post(
            f"/api/v1/sessions/{sid}/execute",
            json={"query": "/secret set _f020_api_test apivalue123"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("_f020_api_test", data["response"])
        self.client.post(
            f"/api/v1/sessions/{sid}/execute",
            json={"query": "/secret delete _f020_api_test"},
            headers=self.auth_header,
        )

    def test_help_includes_secret(self):
        sid = self._create_session()
        resp = self.client.post(
            f"/api/v1/sessions/{sid}/execute",
            json={"query": "/help"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("/secret", resp.json()["response"])

    def test_runtime_list_via_api(self):
        """F020: /runtime list via API should bypass LLM."""
        sid = self._create_session()
        resp = self.client.post(
            f"/api/v1/sessions/{sid}/execute",
            json={"query": "/runtime list"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("copilot", resp.json()["response"])


if __name__ == "__main__":
    unittest.main()
