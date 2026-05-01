import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, ".")

from agent_manager import SessionManager


class TestIssue193TypoFixes:
    """Regression tests for issue #193: "of" -> "off" typo fixes."""

    def test_notifications_off_sets_value_correctly(self):
        """Regression: /notifications off must set preference to 'off', not 'of'.

        Updated for issue #250: the handler now calls set_agent_pref() with 'off'
        for every known agent rather than update_session_field().
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SESSION_MAP_DIR": tmpdir}):
                import threading

                from notification_manager import NotificationManager

                manager = SessionManager.__new__(SessionManager)
                manager._session_map_dir = tmpdir
                # Provide identity so the per-agent auth check passes
                manager._bg_identity = "test_user"
                # Expose a two-element agents list so the bulk-set loop fires
                manager._agents = [{"name": "orchestrator"}, {"name": "wee-dev"}]

                # Use a real NotificationManager backed by a temp file
                prefs_file = str(Path(tmpdir) / "notification_prefs.json")
                settings_file = str(Path(tmpdir) / "notification_settings.json")
                notif_file = str(Path(tmpdir) / "notifications.json")
                nm = NotificationManager.__new__(NotificationManager)
                nm._path = notif_file
                nm._prefs_path = prefs_file
                nm._global_settings_path = settings_file
                nm._notifications = []
                nm._prefs = {}
                nm._lock = threading.Lock()
                nm._prefs_lock = threading.Lock()
                nm._global_settings_lock = threading.Lock()
                nm._telegram_send = None
                nm._webex_send = None
                manager._notification_mgr = nm

                sid = "test_notif"
                session_data = {"n8n_session_id": sid, "notification_preference": "all"}
                p = Path(tmpdir) / f"{sid}.json"
                p.write_text(json.dumps(session_data))

                manager._slash_notifications("off", session_data, sid)

                # Verify both agents were set to "off" (not "of")
                for agent in ("orchestrator", "wee-dev"):
                    pref = nm.get_agent_pref("test_user", agent)
                    assert (
                        pref == "off"
                    ), f"Agent '{agent}' preference should be 'off', got '{pref}'"

    def test_silent_off_disables_mode(self):
        """Regression: /silent off must disable silent mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SESSION_MAP_DIR": tmpdir}):
                manager = SessionManager.__new__(SessionManager)
                manager._session_map_dir = tmpdir

                sid = "test_silent"
                session_data = {"n8n_session_id": sid, "silent_mode": True}

                with patch.object(manager, "update_session_field") as mock_update:
                    manager._slash_silent("off", session_data, sid)

                    # Check that silent_mode was set to False
                    calls = mock_update.call_args_list
                    for call in calls:
                        if len(call[0]) >= 3 and call[0][1] == "silent_mode":
                            assert (
                                call[0][2] is False
                            ), "silent_mode should be False after /silent off"
                            return
                    pytest.fail("update_session_field not called with silent_mode")

    def test_verbose_off_enables_silent(self):
        """Regression: /verbose off must enable silent mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SESSION_MAP_DIR": tmpdir}):
                manager = SessionManager.__new__(SessionManager)
                manager._session_map_dir = tmpdir

                sid = "test_verbose"
                session_data = {"n8n_session_id": sid, "silent_mode": False}

                with patch.object(manager, "update_session_field") as mock_update:
                    manager._slash_verbose("off", session_data, sid)

                    # Check that silent_mode was set to True
                    calls = mock_update.call_args_list
                    for call in calls:
                        if len(call[0]) >= 3 and call[0][1] == "silent_mode":
                            assert (
                                call[0][2] is True
                            ), "silent_mode should be True after /verbose off"
                            return
                    pytest.fail("update_session_field not called with silent_mode")

    def test_verbose_usage_help_contains_off_not_standalone_of(self):
        """Regression: /verbose help text uses 'off', not typo 'of'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SESSION_MAP_DIR": tmpdir}):
                manager = SessionManager.__new__(SessionManager)
                manager._session_map_dir = tmpdir

                sid = "test_help"
                session_data = {"n8n_session_id": sid}

                response = manager._slash_verbose("", session_data, sid)

                # Ensure "off" is present as a valid option (within /verbose off)
                assert "off" in response.lower()
                # Ensure the typo pattern " of]" or "of `" does not appear
                assert not re.search(
                    r"\bof\b[\]`]", response
                ), "Response should not contain standalone 'of' before bracket/backtick"

    def test_silent_usage_help_contains_off_not_standalone_of(self):
        """Regression: /silent help text uses 'off', not typo 'of'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SESSION_MAP_DIR": tmpdir}):
                manager = SessionManager.__new__(SessionManager)
                manager._session_map_dir = tmpdir

                sid = "test_help2"
                session_data = {"n8n_session_id": sid}

                response = manager._slash_silent("", session_data, sid)

                # Ensure "off" is present as a valid option
                assert "off" in response.lower()
                # Ensure the typo pattern " of]" or "of `" does not appear
                assert not re.search(
                    r"\bof\b[\]`]", response
                ), "Response should not contain standalone 'of' before bracket/backtick"
