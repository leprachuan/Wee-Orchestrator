"""Regression test for dispatcher stall timeout detection.

Issue: Work queue dispatcher had no timeout for stalled items.
If wee-qa got stuck and never completed, the dispatcher would infinitely
re-dispatch it, wasting resources and blocking new items.

This test ensures the timeout mechanism properly detects and escalates
stalled qa-review items after STALL_TIMEOUT_MINUTES.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, '/opt/n8n-copilot-shim-dev/scripts')
from dispatch_wee_dev_work_queue import (
    check_stall_timeout,
    parse_iso_datetime,
    STALL_TIMEOUT_MINUTES,
    now_iso,
)


def test_parse_iso_datetime_with_timezone():
    """parse_iso_datetime should handle ISO strings with timezone."""
    iso_str = "2026-04-29T20:20:00+00:00"
    dt = parse_iso_datetime(iso_str)
    assert dt is not None
    assert dt.year == 2026


def test_parse_iso_datetime_with_z():
    """parse_iso_datetime should handle Z timezone notation."""
    iso_str = "2026-04-29T20:20:00Z"
    dt = parse_iso_datetime(iso_str)
    assert dt is not None


def test_parse_iso_datetime_invalid():
    """parse_iso_datetime should return None for invalid input."""
    assert parse_iso_datetime("invalid") is None
    assert parse_iso_datetime(None) is None


def test_check_stall_timeout_none_lock():
    """check_stall_timeout should return False for None lock."""
    assert check_stall_timeout(None) is False


def test_check_stall_timeout_no_created_at():
    """check_stall_timeout should return False if created_at is missing."""
    lock = {"state": "qa-review"}
    assert check_stall_timeout(lock) is False


def test_check_stall_timeout_recent_qa_review():
    """check_stall_timeout should return False if qa-review is recent."""
    lock = {
        "state": "qa-review",
        "created_at": now_iso(),
    }
    assert check_stall_timeout(lock) is False


def test_check_stall_timeout_stale_qa_review():
    """check_stall_timeout should return True if qa-review exceeds timeout."""
    old_time = (
        datetime.now(timezone.utc)
        - timedelta(minutes=STALL_TIMEOUT_MINUTES + 5)
    ).isoformat()
    lock = {
        "state": "qa-review",
        "created_at": old_time,
    }
    assert check_stall_timeout(lock) is True


def test_check_stall_timeout_in_progress():
    """check_stall_timeout should apply to in-progress state too."""
    old_time = (
        datetime.now(timezone.utc)
        - timedelta(minutes=STALL_TIMEOUT_MINUTES + 10)
    ).isoformat()
    lock = {
        "state": "in-progress",
        "created_at": old_time,
    }
    assert check_stall_timeout(lock) is True


def test_check_stall_timeout_qa_failed():
    """check_stall_timeout should apply to qa-failed state too."""
    old_time = (
        datetime.now(timezone.utc)
        - timedelta(minutes=STALL_TIMEOUT_MINUTES + 10)
    ).isoformat()
    lock = {
        "state": "qa-failed",
        "created_at": old_time,
    }
    assert check_stall_timeout(lock) is True


def test_check_stall_timeout_not_applicable_to_wee_dev_running():
    """check_stall_timeout should NOT apply to wee-dev-running state."""
    old_time = (
        datetime.now(timezone.utc)
        - timedelta(minutes=STALL_TIMEOUT_MINUTES + 20)
    ).isoformat()
    lock = {
        "state": "wee-dev-running",
        "created_at": old_time,
    }
    assert check_stall_timeout(lock) is False


def test_check_stall_timeout_not_applicable_to_dispatching():
    """check_stall_timeout should NOT apply to dispatching state."""
    old_time = (
        datetime.now(timezone.utc)
        - timedelta(minutes=STALL_TIMEOUT_MINUTES + 20)
    ).isoformat()
    lock = {
        "state": "dispatching",
        "created_at": old_time,
    }
    assert check_stall_timeout(lock) is False


if __name__ == "__main__":
    tests = [
        test_parse_iso_datetime_with_timezone,
        test_parse_iso_datetime_with_z,
        test_parse_iso_datetime_invalid,
        test_check_stall_timeout_none_lock,
        test_check_stall_timeout_no_created_at,
        test_check_stall_timeout_recent_qa_review,
        test_check_stall_timeout_stale_qa_review,
        test_check_stall_timeout_in_progress,
        test_check_stall_timeout_qa_failed,
        test_check_stall_timeout_not_applicable_to_wee_dev_running,
        test_check_stall_timeout_not_applicable_to_dispatching,
    ]

    for test in tests:
        try:
            test()
            print(f"✓ {test.__name__}")
        except AssertionError as e:
            print(f"✗ {test.__name__}: {e}")
            sys.exit(1)

    print(f"\n✓ All {len(tests)} regression tests passed")
