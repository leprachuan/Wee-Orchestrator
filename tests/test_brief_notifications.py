#!/usr/bin/env python3
"""Tests for F024: Brief notification format for background and scheduled tasks.

Validates that external (Telegram/WebEx) notifications are short one-liners
instead of verbose multi-line messages.
"""

import os
import sys
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Setup: make notification_manager importable
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")


# ---------------------------------------------------------------------------
# 1. notification_manager._format_notification_message tests
# ---------------------------------------------------------------------------


class TestFormatNotificationMessage:
    """Validate the brief one-line notification format."""

    def _fmt(self, **overrides):
        from notification_manager import _format_notification_message

        base = {
            "task_id": "bg_abc123",
            "description": "Email triage",
            "status": "completed",
            "created_at": "2026-04-01T12:00:00Z",
            "output_preview": "Processed 12 emails, archived 9, starred 3",
            "error": None,
        }
        base.update(overrides)
        return _format_notification_message(base)

    def test_completed_is_one_line(self):
        msg = self._fmt(status="completed")
        assert "\n" not in msg, f"Message should be one line, got: {msg!r}"

    def test_failed_is_one_line(self):
        msg = self._fmt(status="failed", error="Connection timeout")
        assert "\n" not in msg, f"Message should be one line, got: {msg!r}"

    def test_completed_contains_icon_and_verb(self):
        msg = self._fmt(status="completed")
        assert "✅" in msg
        assert "done" in msg

    def test_failed_contains_icon_and_verb(self):
        msg = self._fmt(status="failed")
        assert "❌" in msg
        assert "failed" in msg

    def test_contains_task_id(self):
        msg = self._fmt(task_id="bg_xyz789")
        assert "bg_xyz789" in msg

    def test_contains_description(self):
        msg = self._fmt(description="Morning briefing")
        assert "Morning briefing" in msg

    def test_no_output_preview_in_message(self):
        msg = self._fmt(output_preview="very long output " * 50)
        assert "very long output" not in msg

    def test_no_error_in_message(self):
        msg = self._fmt(
            status="failed", error="Traceback (most recent call last)..."
        )
        assert "Traceback" not in msg

    def test_no_timestamp_in_message(self):
        msg = self._fmt(created_at="2026-04-01T12:00:00Z")
        assert "2026-04-01" not in msg

    def test_max_length_enforced(self):
        from notification_manager import _MAX_NOTIFICATION_LENGTH

        msg = self._fmt(description="A" * 500)
        assert len(msg) <= _MAX_NOTIFICATION_LENGTH

    def test_truncation_adds_ellipsis(self):
        msg = self._fmt(description="A" * 500)
        assert msg.endswith("...")

    def test_short_description_no_ellipsis(self):
        msg = self._fmt(description="Quick task")
        assert not msg.endswith("...")

    def test_default_description_when_missing(self):
        from notification_manager import _format_notification_message

        msg = _format_notification_message(
            {"task_id": "bg_1", "status": "completed"}
        )
        assert "Background task" in msg


# ---------------------------------------------------------------------------
# 2. _MAX_NOTIFICATION_LENGTH constant exists
# ---------------------------------------------------------------------------


class TestNotificationConstants:
    def test_max_notification_length_exists(self):
        from notification_manager import _MAX_NOTIFICATION_LENGTH

        assert isinstance(_MAX_NOTIFICATION_LENGTH, int)
        assert _MAX_NOTIFICATION_LENGTH == 200

    def test_max_notifications_still_exists(self):
        from notification_manager import _MAX_NOTIFICATIONS

        assert _MAX_NOTIFICATIONS == 200


# ---------------------------------------------------------------------------
# 3. scheduler/executor.py _brief_notification tests
# ---------------------------------------------------------------------------


class TestSchedulerBriefNotification:
    """Test the _brief_notification helper in scheduler/executor.py."""

    def _brief(self, icon, name, verb):
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "scheduler"
        ))
        from executor import _brief_notification

        return _brief_notification(icon, name, verb)

    def test_success_format(self):
        msg = self._brief("✅", "Morning Briefing", "done")
        assert msg == "✅ Morning Briefing — done"

    def test_failure_format(self):
        msg = self._brief("❌", "Email Triage", "failed")
        assert msg == "❌ Email Triage — failed"

    def test_timeout_format(self):
        msg = self._brief("⏱️", "Heartbeat Check", "timed out")
        assert msg == "⏱️ Heartbeat Check — timed out"

    def test_is_one_line(self):
        msg = self._brief("✅", "Some Job", "done")
        assert "\n" not in msg

    def test_max_length_enforced(self):
        from executor import _MAX_NOTIFICATION_LENGTH

        msg = self._brief("✅", "A" * 500, "done")
        assert len(msg) <= _MAX_NOTIFICATION_LENGTH

    def test_truncation_adds_ellipsis(self):
        msg = self._brief("✅", "A" * 500, "done")
        assert msg.endswith("...")

    def test_short_name_no_ellipsis(self):
        msg = self._brief("✅", "Quick", "done")
        assert not msg.endswith("...")


# ---------------------------------------------------------------------------
# 4. Integration: create_notification still stores output_preview for WebUI
# ---------------------------------------------------------------------------


class TestWebUIPreservesPreview:
    """Ensure output_preview and error are still stored for WebUI polling."""

    def test_output_preview_stored_in_notification(self):
        from notification_manager import NotificationManager

        with tempfile.TemporaryDirectory() as d:
            mgr = NotificationManager(
                notif_file=os.path.join(d, "notif.json"),
                prefs_file=os.path.join(d, "prefs.json"),
            )
            notif = mgr.create_notification(
                task_id="bg_test1",
                description="Test task",
                status="completed",
                channel="webui",
                user_key="webui_test",
                output_preview="Here is the full output",
                skip_external=True,
            )
            assert notif["output_preview"] == "Here is the full output"

    def test_error_stored_in_notification(self):
        from notification_manager import NotificationManager

        with tempfile.TemporaryDirectory() as d:
            mgr = NotificationManager(
                notif_file=os.path.join(d, "notif.json"),
                prefs_file=os.path.join(d, "prefs.json"),
            )
            notif = mgr.create_notification(
                task_id="bg_test2",
                description="Failed task",
                status="failed",
                channel="webui",
                user_key="webui_test",
                error="Connection refused",
                skip_external=True,
            )
            assert notif["error"] == "Connection refused"

    def test_output_preview_truncated_at_500(self):
        from notification_manager import NotificationManager

        with tempfile.TemporaryDirectory() as d:
            mgr = NotificationManager(
                notif_file=os.path.join(d, "notif.json"),
                prefs_file=os.path.join(d, "prefs.json"),
            )
            notif = mgr.create_notification(
                task_id="bg_test3",
                description="Big output",
                status="completed",
                channel="webui",
                user_key="webui_test",
                output_preview="X" * 1000,
                skip_external=True,
            )
            assert len(notif["output_preview"]) == 500


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
