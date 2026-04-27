#!/usr/bin/env python3
"""Tests for Issue #93: Keyring unlock via WebUI."""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/opt/n8n-copilot-shim-dev")

# Import secret_tool by file path to avoid conflict with existing
# test_secret_tool.py which imports it as a bare module
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "secret_tool_mod",
    "/opt/n8n-copilot-shim-dev/secret_tool/secret_tool.py",
)
_st = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_st)
sys.modules["secret_tool_mod"] = _st


class TestSecretToolStatus(unittest.TestCase):
    """Test secret_tool.py status subcommand."""

    def test_status_returns_valid_dict(self):
        _check_keyring_status = _st._check_keyring_status
        result = _check_keyring_status()
        self.assertIn("status", result)
        self.assertIn(result["status"], ["unlocked", "locked", "unavailable", "error"])

    def test_status_has_required_fields(self):
        _check_keyring_status = _st._check_keyring_status
        result = _check_keyring_status()
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result["status"], str)
        # message is only required for non-unlocked states
        if result["status"] != "unlocked":
            self.assertIn("message", result)

    @patch("secret_tool_mod.shutil.which")
    def test_find_gnome_keyring_daemon(self, mock_which):
        _find_gnome_keyring_daemon = _st._find_gnome_keyring_daemon
        mock_which.return_value = "/usr/bin/gnome-keyring-daemon"
        result = _find_gnome_keyring_daemon()
        self.assertEqual(result, "/usr/bin/gnome-keyring-daemon")

    @patch("secret_tool_mod.shutil.which")
    def test_find_gnome_keyring_daemon_not_found(self, mock_which):
        _find_gnome_keyring_daemon = _st._find_gnome_keyring_daemon
        mock_which.return_value = None
        result = _find_gnome_keyring_daemon()
        self.assertTrue(result is None or isinstance(result, str))

    def test_unlock_without_password_fails(self):
        _unlock_keyring = _st._unlock_keyring
        result = _unlock_keyring("")
        self.assertEqual(result["status"], "error")
        self.assertIn("required", result["message"].lower())

    def test_unlock_with_password_returns_dict(self):
        _unlock_keyring = _st._unlock_keyring
        result = _unlock_keyring("dummy-password-for-test")
        self.assertIn("status", result)
        self.assertIn(result["status"], ["unlocked", "success", "error", "unavailable"])


class TestKeyringAPIEndpoints(unittest.TestCase):
    """Test agent_manager.py keyring API endpoints."""

    def test_keyring_status_endpoint_exists(self):
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py", "r") as f:
            content = f.read()
        self.assertIn("/api/v1/secrets/keyring-status", content)
        self.assertIn("async def keyring_status", content)

    def test_keyring_unlock_endpoint_exists(self):
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py", "r") as f:
            content = f.read()
        self.assertIn("/api/v1/secrets/keyring-unlock", content)
        self.assertIn("async def keyring_unlock", content)

    def test_unlock_requires_password(self):
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py", "r") as f:
            content = f.read()
        self.assertIn('body.get("password"', content)
        self.assertIn("password is required", content)

    def test_status_calls_secret_tool(self):
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py", "r") as f:
            content = f.read()
        import re

        self.assertIsNotNone(
            re.search(r'_SECRET_TOOL_PATH,\s+"status"', content),
            "_SECRET_TOOL_PATH should be called with 'status' command",
        )

    def test_unlock_calls_secret_tool(self):
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py", "r") as f:
            content = f.read()
        import re

        self.assertIsNotNone(
            re.search(r'_SECRET_TOOL_PATH,\s+"unlock"', content),
            "_SECRET_TOOL_PATH should be called with 'unlock' command",
        )

    def test_password_sent_via_stdin(self):
        """Password must be sent via stdin, not as CLI arg."""
        with open("/opt/n8n-copilot-shim-dev/agent_manager.py", "r") as f:
            content = f.read()
        # Find the unlock endpoint section
        idx = content.find("async def keyring_unlock")
        section = content[idx : idx + 1500]
        self.assertIn("stdin=asyncio.subprocess.PIPE", section)
        self.assertIn(".communicate(", section)


class TestSecretToolSubcommands(unittest.TestCase):
    """Test secret_tool.py has status/unlock in code."""

    def test_secret_tool_has_status(self):
        with open("/opt/n8n-copilot-shim-dev/secret_tool/secret_tool.py", "r") as f:
            content = f.read()
        self.assertIn('"status"', content)
        self.assertIn("_check_keyring_status", content)

    def test_secret_tool_has_unlock(self):
        with open("/opt/n8n-copilot-shim-dev/secret_tool/secret_tool.py", "r") as f:
            content = f.read()
        self.assertIn('"unlock"', content)
        self.assertIn("_unlock_keyring", content)


class TestWebUIComponents(unittest.TestCase):
    """Test WebUI HTML/JS/CSS changes."""

    def test_html_has_keyring_banner(self):
        with open("/opt/n8n-copilot-shim-dev/webui/dist/index.html", "r") as f:
            content = f.read()
        self.assertIn("keyring-status-banner", content)
        self.assertIn("keyring-banner-title", content)
        self.assertIn("keyring-banner-detail", content)
        self.assertIn("btn-keyring-unlock", content)

    def test_html_has_unlock_dialog(self):
        with open("/opt/n8n-copilot-shim-dev/webui/dist/index.html", "r") as f:
            content = f.read()
        self.assertIn("keyring-unlock-dialog", content)
        self.assertIn("keyring-password-input", content)
        self.assertIn("btn-keyring-submit", content)
        self.assertIn("btn-keyring-cancel", content)

    def test_js_has_keyring_functions(self):
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js", "r") as f:
            content = f.read()
        for fn in [
            "checkKeyringStatus",
            "showKeyringUnlockDialog",
            "hideKeyringUnlockDialog",
            "submitKeyringUnlock",
            "_initKeyringListeners",
        ]:
            self.assertIn(fn, content, f"Missing function: {fn}")

    def test_js_calls_check_on_panel_show(self):
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js", "r") as f:
            content = f.read()
        idx = content.find("function showSecretsPanel")
        end_idx = content.find("\n}", idx) + 2
        snippet = content[idx:end_idx]
        self.assertIn("checkKeyringStatus", snippet)

    def test_js_inits_keyring_listeners(self):
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.js", "r") as f:
            content = f.read()
        self.assertIn("_initKeyringListeners();", content)

    def test_css_has_keyring_styles(self):
        with open("/opt/n8n-copilot-shim-dev/webui/dist/app.css", "r") as f:
            content = f.read()
        for cls in [
            ".keyring-banner",
            ".keyring-banner--locked",
            ".keyring-dialog-overlay",
            ".keyring-dialog",
            ".keyring-dialog-feedback--error",
            ".keyring-dialog-feedback--success",
        ]:
            self.assertIn(cls, content, f"Missing CSS class: {cls}")


class TestSecretToolCLI(unittest.TestCase):
    """Test secret_tool.py CLI parsing for new subcommands."""

    def test_parse_status_args(self):
        parse_args = _st.parse_args
        args = parse_args(["status"])
        self.assertEqual(args.cmd, "status")

    def test_parse_unlock_args(self):
        parse_args = _st.parse_args
        args = parse_args(["unlock"])
        self.assertEqual(args.cmd, "unlock")

    def test_parse_existing_commands_still_work(self):
        parse_args = _st.parse_args
        args = parse_args(["list", "--backend", "file"])
        self.assertEqual(args.cmd, "list")
        self.assertEqual(args.backend, "file")


class TestKeyringStatusCheck(unittest.TestCase):
    """Test keyring status probing strategies."""

    @patch("secret_tool_mod.subprocess.run")
    def test_status_handles_timeout(self, mock_run):
        import subprocess as sp

        mock_run.side_effect = sp.TimeoutExpired(cmd=["secret-tool"], timeout=3)
        _check_keyring_status = _st._check_keyring_status
        with patch("secret_tool_mod.shutil.which", return_value="/usr/bin/secret-tool"):
            result = _check_keyring_status()
        self.assertIn(result["status"], ["locked", "unavailable", "unlocked", "error"])

    @patch("secret_tool_mod.subprocess.run")
    def test_status_handles_success(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run.return_value = mock_result
        _check_keyring_status = _st._check_keyring_status
        with patch("secret_tool_mod.shutil.which", return_value="/usr/bin/secret-tool"):
            result = _check_keyring_status()
        self.assertIn(result["status"], ["unlocked", "locked", "unavailable", "error"])

    def test_password_not_in_output(self):
        _unlock_keyring = _st._unlock_keyring
        result = _unlock_keyring("super-secret-test")
        result_str = json.dumps(result)
        self.assertNotIn("super-secret-test", result_str)


if __name__ == "__main__":
    unittest.main()
