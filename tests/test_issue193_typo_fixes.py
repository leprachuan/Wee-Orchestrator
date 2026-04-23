import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import sys
import re

sys.path.insert(0, ".")

from agent_manager import SessionManager


class TestIssue193TypoFixes:
    """Regression tests for issue #193: "of" -> "off" typo fixes."""

    def test_notifications_off_sets_value_correctly(self):
        """Regression: /notifications off must set to 'off', not 'of'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SESSION_MAP_DIR": tmpdir}):
                manager = SessionManager.__new__(SessionManager)
                manager._session_map_dir = tmpdir

                sid = "test_notif"
                session_data = {"n8n_session_id": sid, "notification_preference": "all"}
                p = Path(tmpdir) / f"{sid}.json"
                p.write_text(json.dumps(session_data))

                with patch.object(manager, "update_session_field") as mock_update:
                    with patch.object(manager, "_notification_mgr", MagicMock()):
                        manager._slash_notifications("off", session_data, sid)

                        # Check that update_session_field was called with "off"
                        calls = mock_update.call_args_list
                        found = False
                        for call in calls:
                            if len(call[0]) >= 3 and call[0][2] == "off":
                                found = True
                                break
                        assert found, "Should call update_session_field with 'off'"

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
