"""Tests for F020: Slash command registry that bypasses the LLM."""

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
        self.assertGreater(len(reg), 10)

    def test_registry_contains_secret(self):
        """/secret should be in the registry with a handler."""
        entry = self.sm._slash_command_registry.get("/secret")
        self.assertIsNotNone(entry)
        self.assertIsNotNone(entry["handler"])
        self.assertIn("secret", entry["description"].lower())

    def test_registry_contains_legacy_commands(self):
        """Legacy commands should be registered with handler=None."""
        for cmd in [
            "/help", "/status", "/cancel", "/runtime",
            "/model", "/agent", "/session",
        ]:
            entry = self.sm._slash_command_registry.get(cmd)
            self.assertIsNotNone(entry, f"{cmd} not in registry")
            self.assertIsNone(entry["handler"], f"{cmd} should have handler=None")

    def test_register_slash_adds_command(self):
        """register_slash should add a new command to the registry."""
        def handler(a, s, n):
            return "test"
        self.sm._register_slash("/test_f020", handler, "Test command")
        entry = self.sm._slash_command_registry.get("/test_f020")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["handler"], handler)
        self.assertEqual(entry["description"], "Test command")
        # Cleanup
        del self.sm._slash_command_registry["/test_f020"]

    def test_get_slash_commands_returns_dict(self):
        """get_slash_commands should return {command: description}."""
        cmds = self.sm.get_slash_commands()
        self.assertIsInstance(cmds, dict)
        self.assertIn("/secret", cmds)
        self.assertIn("/help", cmds)


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
        """'/secret' with no args should show help."""
        result = self.sm._slash_secret(None, {}, "test-session")
        self.assertIn("Secret Commands", result)
        self.assertIn("/secret list", result)
        self.assertIn("/secret set", result)
        self.assertIn("/secret delete", result)

    def test_secret_unknown_subcommand_shows_help(self):
        """'/secret unknown' should show help."""
        result = self.sm._slash_secret("blah", {}, "test-session")
        self.assertIn("Secret Commands", result)

    def test_secret_list(self):
        """'/secret list' should return a list of secret names."""
        result = self.sm._slash_secret("list", {}, "test-session")
        # Should not error out — either shows names or "(none)"
        self.assertTrue(
            "Secrets" in result or "none" in result.lower(),
            f"Unexpected list result: {result[:100]}",
        )

    def test_secret_set_and_delete(self):
        """'/secret set' should create, '/secret delete' should remove."""
        # Set
        result = self.sm._slash_secret(
            "set _f020_test_secret testvalue123", {}, "test-session"
        )
        self.assertIn("_f020_test_secret", result)
        self.assertIn("created", result)

        # Verify it appears in list
        result = self.sm._slash_secret("list", {}, "test-session")
        self.assertIn("_f020_test_secret", result)

        # Update (set again)
        result = self.sm._slash_secret(
            "set _f020_test_secret newvalue456", {}, "test-session"
        )
        self.assertIn("updated", result)

        # Delete
        result = self.sm._slash_secret(
            "delete _f020_test_secret", {}, "test-session"
        )
        self.assertIn("deleted", result)

    def test_secret_set_missing_value(self):
        """'/secret set name' without value should show usage."""
        result = self.sm._slash_secret("set onlyname", {}, "test-session")
        self.assertIn("Usage", result)

    def test_secret_set_missing_all(self):
        """'/secret set' without name or value should show help."""
        result = self.sm._slash_secret("set", {}, "test-session")
        self.assertIn("Secret Commands", result)

    def test_secret_set_invalid_name(self):
        """'/secret set' with path-traversal name should be rejected."""
        result = self.sm._slash_secret(
            "set ../evil/path myvalue", {}, "test-session"
        )
        self.assertIn("Invalid name", result)

    def test_secret_set_slash_in_name(self):
        """'/secret set' with slash in name should be rejected."""
        result = self.sm._slash_secret(
            "set na/me value", {}, "test-session"
        )
        self.assertIn("Invalid name", result)

    def test_secret_delete_invalid_name(self):
        """'/secret delete' with invalid name should be rejected."""
        result = self.sm._slash_secret(
            "delete ../evil", {}, "test-session"
        )
        self.assertIn("Invalid name", result)

    def test_secret_delete_nonexistent(self):
        """'/secret delete' for nonexistent secret should show error."""
        result = self.sm._slash_secret(
            "delete _f020_nonexistent_secret_xyz", {}, "test-session"
        )
        # Should contain error indicator
        self.assertTrue(
            "\u274c" in result or "not found" in result.lower(),
            f"Expected error for nonexistent: {result[:100]}",
        )

    def test_secret_value_with_spaces(self):
        """'/secret set name value with spaces' should work."""
        result = self.sm._slash_secret(
            "set _f020_space_test hello world test", {}, "test-session"
        )
        self.assertIn("_f020_space_test", result)
        self.assertIn("created", result)
        # Cleanup
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
        """/secret list via execute() should be handled by registry."""
        # Create a minimal session
        session_id = "test_f020_exec"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/secret list", session_id)
        self.assertTrue(
            "Secrets" in result or "none" in result.lower(),
            f"Expected secrets list, got: {result[:100]}",
        )

    def test_execute_routes_help_to_legacy(self):
        """/help should still work via legacy if/elif chain."""
        session_id = "test_f020_help"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute("/help", session_id)
        self.assertIn("Available Commands", result)
        # F020: /secret should appear in /help output
        self.assertIn("/secret", result)

    def test_execute_secret_set_bypasses_llm(self):
        """/secret set should return immediately, not dispatch to LLM."""
        session_id = "test_f020_bypass"
        self.sm.get_or_create_session_data(session_id)
        result = self.sm.execute(
            "/secret set _f020_bypass_test bypassval", session_id
        )
        # Should return a short confirmation, not an LLM response
        self.assertTrue(
            len(result) < 200,
            f"Response too long — possibly sent to LLM: {result[:200]}",
        )
        self.assertIn("_f020_bypass_test", result)
        # Cleanup
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
        """Create a test session and return its ID."""
        resp = self.client.post(
            "/api/v1/sessions/create",
            json={"agent": "orchestrator", "runtime": "copilot"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["session_id"]

    def test_secret_list_via_execute(self):
        """POST /execute with /secret list should return secrets."""
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
        """POST /execute with /secret set should store and confirm."""
        sid = self._create_session()
        resp = self.client.post(
            f"/api/v1/sessions/{sid}/execute",
            json={"query": "/secret set _f020_api_test apivalue123"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("_f020_api_test", data["response"])
        # Cleanup
        self.client.post(
            f"/api/v1/sessions/{sid}/execute",
            json={"query": "/secret delete _f020_api_test"},
            headers=self.auth_header,
        )

    def test_help_includes_secret(self):
        """POST /execute with /help should mention /secret."""
        sid = self._create_session()
        resp = self.client.post(
            f"/api/v1/sessions/{sid}/execute",
            json={"query": "/help"},
            headers=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("/secret", resp.json()["response"])


if __name__ == "__main__":
    unittest.main()
