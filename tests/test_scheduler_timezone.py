"""Tests for timezone-aware cron scheduling in scheduler/management.py.

Verifies that cron hours are interpreted in local time and next_run is stored in UTC.
Covers weekday/weekend schedules, DST (EST vs EDT), and timezone resolution.
"""

import sys
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zoneinfo import ZoneInfo

from scheduler.management import (
    _get_local_tz,
    _get_local_tz_name,
    cron_next_run,
)

EASTERN = ZoneInfo("America/New_York")
PACIFIC = ZoneInfo("America/Los_Angeles")
UTC_TZ = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# _get_local_tz_name
# ---------------------------------------------------------------------------


class TestGetLocalTzName(unittest.TestCase):
    def _strip_tz_env(self):
        import os
        return {k: v for k, v in os.environ.items() if k != "TZ"}

    def test_tz_env_var_takes_priority(self):
        with unittest.mock.patch.dict("os.environ", {"TZ": "America/Chicago"}):
            self.assertEqual(_get_local_tz_name(), "America/Chicago")

    def test_reads_localtime_symlink(self):
        env = self._strip_tz_env()
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            with unittest.mock.patch(
                "os.readlink", return_value="/usr/share/zoneinfo/America/New_York"
            ):
                self.assertEqual(_get_local_tz_name(), "America/New_York")

    def test_reads_etc_timezone_when_symlink_fails(self):
        env = self._strip_tz_env()
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            with unittest.mock.patch("os.readlink", side_effect=OSError):
                with unittest.mock.patch(
                    "builtins.open",
                    unittest.mock.mock_open(read_data="America/Denver\n"),
                ):
                    self.assertEqual(_get_local_tz_name(), "America/Denver")

    def test_utc_fallback_when_all_sources_fail(self):
        env = self._strip_tz_env()
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            with unittest.mock.patch("os.readlink", side_effect=OSError):
                with unittest.mock.patch("builtins.open", side_effect=OSError):
                    self.assertEqual(_get_local_tz_name(), "UTC")

    def test_etc_utc_is_treated_as_utc_fallback(self):
        """Etc/UTC in /etc/timezone should not count as a real tz; fall through to UTC."""
        env = self._strip_tz_env()
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            with unittest.mock.patch("os.readlink", side_effect=OSError):
                with unittest.mock.patch(
                    "builtins.open",
                    unittest.mock.mock_open(read_data="Etc/UTC\n"),
                ):
                    self.assertEqual(_get_local_tz_name(), "UTC")


# ---------------------------------------------------------------------------
# cron_next_run — weekday schedule (Mon-Fri 7:30)
# ---------------------------------------------------------------------------


class TestWeekdaySchedule(unittest.TestCase):
    """cron '30 7 * * 1-5' must fire at 7:30 local, stored as UTC."""

    CRON = "30 7 * * 1-5"

    def _next(self, base_local: datetime) -> datetime:
        result = cron_next_run(self.CRON, _base_local=base_local)
        self.assertIsNotNone(result, "cron_next_run returned None")
        return datetime.fromisoformat(result.replace("Z", "+00:00"))

    def test_edt_offset_is_utc_minus_4(self):
        """EDT = UTC-4 → 7:30 AM EDT = 11:30 UTC."""
        # Monday 2026-03-30 08:00 EDT (past 7:30 → next run is Tuesday)
        base = datetime(2026, 3, 30, 8, 0, tzinfo=EASTERN)
        next_utc = self._next(base)
        next_local = next_utc.astimezone(EASTERN)
        self.assertEqual(next_local.hour, 7)
        self.assertEqual(next_local.minute, 30)
        self.assertEqual(next_utc.hour, 11, f"Expected 11 UTC for EDT, got {next_utc}")
        self.assertEqual(next_utc.minute, 30)
        # Should be Tuesday
        self.assertEqual(next_local.weekday(), 1)  # 0=Mon, 1=Tue

    def test_est_offset_is_utc_minus_5(self):
        """EST = UTC-5 → 7:30 AM EST = 12:30 UTC (winter/DST off)."""
        # Monday 2026-01-05 08:00 EST
        base = datetime(2026, 1, 5, 8, 0, tzinfo=EASTERN)
        next_utc = self._next(base)
        next_local = next_utc.astimezone(EASTERN)
        self.assertEqual(next_local.hour, 7)
        self.assertEqual(next_local.minute, 30)
        self.assertEqual(next_utc.hour, 12, f"Expected 12 UTC for EST, got {next_utc}")
        self.assertEqual(next_utc.minute, 30)

    def test_before_time_fires_same_day(self):
        """Before 7:30, next run should be today."""
        # Monday 2026-03-30 06:00 EDT
        base = datetime(2026, 3, 30, 6, 0, tzinfo=EASTERN)
        next_utc = self._next(base)
        next_local = next_utc.astimezone(EASTERN)
        self.assertEqual(next_local.date(), base.date())
        self.assertEqual(next_local.hour, 7)

    def test_pacific_timezone(self):
        """PDT = UTC-7 → 7:30 AM PDT = 14:30 UTC."""
        base = datetime(2026, 3, 30, 8, 0, tzinfo=PACIFIC)  # Monday, PDT
        next_utc = self._next(base)
        next_local = next_utc.astimezone(PACIFIC)
        self.assertEqual(next_local.hour, 7)
        self.assertEqual(next_local.minute, 30)
        self.assertEqual(next_utc.hour, 14, f"Expected 14 UTC for PDT, got {next_utc}")


# ---------------------------------------------------------------------------
# cron_next_run — weekend schedule (Sat/Sun 9:00)
# ---------------------------------------------------------------------------


class TestWeekendSchedule(unittest.TestCase):
    """cron '0 9 * * 0,6' must fire at 9:00 local on weekends, stored as UTC."""

    CRON = "0 9 * * 0,6"

    def _next(self, base_local: datetime) -> datetime:
        result = cron_next_run(self.CRON, _base_local=base_local)
        self.assertIsNotNone(result, "cron_next_run returned None")
        return datetime.fromisoformat(result.replace("Z", "+00:00"))

    def test_edt_9am_saturday(self):
        """EDT = UTC-4 → 9:00 AM EDT Saturday = 13:00 UTC."""
        # Monday 2026-03-30 08:00 EDT → next weekend is Sat Apr 4
        base = datetime(2026, 3, 30, 8, 0, tzinfo=EASTERN)
        next_utc = self._next(base)
        next_local = next_utc.astimezone(EASTERN)
        self.assertEqual(next_local.hour, 9)
        self.assertEqual(next_local.minute, 0)
        self.assertEqual(next_utc.hour, 13, f"Expected 13 UTC for EDT, got {next_utc}")
        # Saturday = weekday() 5
        self.assertEqual(next_local.weekday(), 5)

    def test_est_9am_weekend_utc_minus_5(self):
        """EST = UTC-5 → 9:00 AM EST = 14:00 UTC."""
        base = datetime(2026, 1, 5, 8, 0, tzinfo=EASTERN)  # Monday Jan 5
        next_utc = self._next(base)
        next_local = next_utc.astimezone(EASTERN)
        self.assertEqual(next_local.hour, 9)
        self.assertEqual(next_utc.hour, 14, f"Expected 14 UTC for EST, got {next_utc}")

    def test_fires_on_weekend_day(self):
        """Result must land on Saturday or Sunday."""
        base = datetime(2026, 3, 30, 8, 0, tzinfo=EASTERN)
        next_utc = self._next(base)
        next_local = next_utc.astimezone(EASTERN)
        self.assertIn(next_local.weekday(), (5, 6), "Must be Sat(5) or Sun(6)")


# ---------------------------------------------------------------------------
# cron_next_run — UTC host sanity
# ---------------------------------------------------------------------------


class TestUtcHost(unittest.TestCase):
    def test_no_offset_applied_for_utc_host(self):
        """On UTC, cron hour == UTC hour (no conversion needed)."""
        base = datetime(2026, 3, 30, 6, 0, tzinfo=UTC_TZ)
        result = cron_next_run("30 7 * * 1-5", _base_local=base)
        self.assertIsNotNone(result)
        dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
        self.assertEqual(dt.hour, 7)
        self.assertEqual(dt.minute, 30)


# ---------------------------------------------------------------------------
# cron_next_run — output format
# ---------------------------------------------------------------------------


class TestCronNextRunOutputFormat(unittest.TestCase):
    def test_always_ends_with_z(self):
        base = datetime(2026, 3, 30, 6, 0, tzinfo=EASTERN)
        result = cron_next_run("0 * * * *", _base_local=base)
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("Z"), f"Expected Z suffix, got {result}")

    def test_result_is_in_the_future_relative_to_base(self):
        base = datetime(2026, 3, 30, 6, 0, tzinfo=EASTERN)
        result = cron_next_run("* * * * *", _base_local=base)
        self.assertIsNotNone(result)
        dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
        self.assertGreater(dt, base.astimezone(timezone.utc))

    def test_invalid_expression_returns_none(self):
        self.assertIsNone(cron_next_run("not a cron expression"))

    def test_valid_every_minute_returns_string(self):
        result = cron_next_run("* * * * *")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
