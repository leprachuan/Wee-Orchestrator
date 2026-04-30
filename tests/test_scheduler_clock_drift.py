"""Tests for scheduler clock drift handling (Issues #70, #71).

Verifies that the task scheduler executor correctly handles:
- Forward clock jumps (catchup execution)
- Backward clock jumps (wall-clock debt compensation, Issue #71)
- Stale job recalculation (jobs past MAX_CATCHUP_WINDOW)
- Clock drift detection (monotonic vs wall clock)
- Drift diagnostics reporting
- Silent exception prevention
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the repo root is on sys.path so scheduler package is importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("API_SHARED_KEY", "test_key_123")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_scheduler_dir(tmp_path):
    """Create a temporary scheduler data directory with jobs.json."""
    jobs_file = tmp_path / "jobs.json"
    logs_dir = tmp_path / "logs"
    results_dir = tmp_path / "results"
    logs_dir.mkdir()
    results_dir.mkdir()
    jobs_file.write_text(json.dumps({"jobs": []}))
    return tmp_path, jobs_file, logs_dir, results_dir


@pytest.fixture
def executor(tmp_scheduler_dir):
    """Build a TaskSchedulerExecutor with paths pointing at tmp dir."""
    tmp_path, jobs_file, logs_dir, results_dir = tmp_scheduler_dir

    # Patch env vars so the executor uses our temp paths
    env = {
        "SCHEDULER_JOBS_FILE": str(jobs_file),
        "SCHEDULER_LOGS_DIR": str(logs_dir),
        "SCHEDULER_RESULTS_DIR": str(results_dir),
    }
    with patch.dict(os.environ, env):
        from scheduler.executor import TaskSchedulerExecutor

        exe = TaskSchedulerExecutor()
        exe.jobs_file = jobs_file
        exe.logs_dir = logs_dir
        exe.results_dir = results_dir
    return exe


def _make_job(
    job_id="test-job-1",
    name="Test Job",
    next_run_dt=None,
    enabled=True,
    recurring=True,
    cron="*/15 * * * *",
):
    """Helper to build a job dict."""
    if next_run_dt is None:
        next_run_dt = datetime.now(timezone.utc) - timedelta(seconds=5)
    next_run_str = next_run_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": job_id,
        "name": name,
        "task": "echo test",
        "mode": "command",
        "enabled": enabled,
        "recurring": recurring,
        "cron": cron,
        "schedule": "every 15 minutes",
        "next_run": next_run_str,
        "notify": False,
    }


def _write_jobs(jobs_file: Path, jobs: list):
    jobs_file.write_text(json.dumps({"jobs": jobs}, indent=2))


# ---------------------------------------------------------------------------
# Tests: drift detection
# ---------------------------------------------------------------------------


class TestDriftDetection:
    """_detect_clock_drift should measure wall vs monotonic delta."""

    def test_no_drift(self, executor):
        """With no simulated drift the result should be near zero."""
        time.sleep(0.05)
        drift = executor._detect_clock_drift()
        assert abs(drift) < 2.0, f"Expected near-zero drift, got {drift}"

    def test_forward_drift_detection(self, executor):
        """Simulate a forward wall-clock jump."""
        executor._last_check_wall = time.time() - 60
        executor._last_check_mono = time.monotonic() - 1

        drift = executor._detect_clock_drift()
        assert drift > 50, f"Expected large forward drift, got {drift}"

    def test_backward_drift_detection(self, executor):
        """Simulate a backward wall-clock jump."""
        executor._last_check_wall = time.time() + 60
        executor._last_check_mono = time.monotonic() - 1

        drift = executor._detect_clock_drift()
        assert drift < -50, f"Expected large backward drift, got {drift}"

    def test_backward_drift_increases_debt(self, executor):
        """Backward drift should increase wall_clock_debt (Issue #71)."""
        assert executor._wall_clock_debt == 0.0

        # Simulate backward jump of ~60s
        executor._last_check_wall = time.time() + 60
        executor._last_check_mono = time.monotonic() - 1
        executor._detect_clock_drift()

        assert (
            executor._wall_clock_debt > 50
        ), f"Expected debt >50s after backward jump, got {executor._wall_clock_debt}"

    def test_forward_drift_reduces_debt(self, executor):
        """Forward drift should reduce existing wall_clock_debt (Issue #71)."""
        executor._wall_clock_debt = 100.0

        # Simulate forward jump of ~60s
        executor._last_check_wall = time.time() - 60
        executor._last_check_mono = time.monotonic() - 1
        executor._detect_clock_drift()

        assert (
            executor._wall_clock_debt < 50
        ), f"Expected debt to reduce after forward drift, got {executor._wall_clock_debt}"

    def test_debt_capped_at_maximum(self, executor):
        """Wall-clock debt should not exceed _DRIFT_COMPENSATION_CAP."""
        from scheduler.executor import _DRIFT_COMPENSATION_CAP

        # Simulate huge backward jump
        executor._last_check_wall = time.time() + 2000
        executor._last_check_mono = time.monotonic() - 1
        executor._detect_clock_drift()

        assert (
            executor._wall_clock_debt <= _DRIFT_COMPENSATION_CAP
        ), f"Debt {executor._wall_clock_debt} exceeds cap {_DRIFT_COMPENSATION_CAP}"

    def test_debt_does_not_go_negative(self, executor):
        """Forward drift should not push debt below zero."""
        executor._wall_clock_debt = 10.0

        # Simulate forward jump of ~100s (more than the debt)
        executor._last_check_wall = time.time() - 100
        executor._last_check_mono = time.monotonic() - 1
        executor._detect_clock_drift()

        assert (
            executor._wall_clock_debt >= 0.0
        ), f"Debt went negative: {executor._wall_clock_debt}"

    def test_drift_events_tracked(self, executor):
        """Significant drift events should be recorded in _drift_events."""
        assert len(executor._drift_events) == 0

        # Simulate backward jump
        executor._last_check_wall = time.time() + 60
        executor._last_check_mono = time.monotonic() - 1
        executor._detect_clock_drift()

        assert len(executor._drift_events) == 1
        ts, drift_val = executor._drift_events[0]
        assert drift_val < -50

    def test_drift_events_capped(self, executor):
        """Drift events list should not grow unbounded."""
        from scheduler.executor import _DRIFT_EVENT_HISTORY

        for i in range(_DRIFT_EVENT_HISTORY + 20):
            executor._last_check_wall = time.time() + 60
            executor._last_check_mono = time.monotonic() - 1
            executor._detect_clock_drift()
            executor._last_check_wall = time.time()
            executor._last_check_mono = time.monotonic()

        assert len(executor._drift_events) <= _DRIFT_EVENT_HISTORY


# ---------------------------------------------------------------------------
# Tests: _is_job_ready with clock drift scenarios
# ---------------------------------------------------------------------------


class TestIsJobReady:
    """_is_job_ready with drift-aware enhancements."""

    def test_job_due_normally(self, executor):
        """Job whose next_run is 5 seconds in the past should be ready."""
        job = _make_job(next_run_dt=datetime.now(timezone.utc) - timedelta(seconds=5))
        assert executor._is_job_ready(job) is True

    def test_job_not_due(self, executor):
        """Job whose next_run is in the future should not be ready."""
        job = _make_job(next_run_dt=datetime.now(timezone.utc) + timedelta(minutes=5))
        assert executor._is_job_ready(job) is False

    def test_disabled_job(self, executor):
        """Disabled job should not be ready."""
        job = _make_job(enabled=False)
        assert executor._is_job_ready(job) is False

    def test_monotonic_cooldown_prevents_double_exec(self, executor):
        """After recording a monotonic execution, the same job should be
        blocked within the cooldown window."""
        job = _make_job(job_id="cooldown-test")
        executor._job_last_exec_mono["cooldown-test"] = time.monotonic()
        assert executor._is_job_ready(job) is False

    def test_monotonic_cooldown_expires(self, executor):
        """After the cooldown expires, the job should be ready again."""
        from scheduler.executor import _MIN_EXEC_INTERVAL_MONO

        job = _make_job(job_id="cooldown-expire")
        executor._job_last_exec_mono["cooldown-expire"] = (
            time.monotonic() - _MIN_EXEC_INTERVAL_MONO - 1
        )
        assert executor._is_job_ready(job) is True

    def test_stale_recurring_job_not_ready(self, executor):
        """A recurring job past MAX_CATCHUP_WINDOW should NOT be ready."""
        from scheduler.executor import _MAX_CATCHUP_WINDOW

        stale_dt = datetime.now(timezone.utc) - timedelta(
            seconds=_MAX_CATCHUP_WINDOW + 600
        )
        job = _make_job(next_run_dt=stale_dt, recurring=True)
        assert executor._is_job_ready(job) is False

    def test_stale_one_time_job_still_ready(self, executor):
        """A one-time job past MAX_CATCHUP_WINDOW should STILL be ready."""
        from scheduler.executor import _MAX_CATCHUP_WINDOW

        stale_dt = datetime.now(timezone.utc) - timedelta(
            seconds=_MAX_CATCHUP_WINDOW + 600
        )
        job = _make_job(next_run_dt=stale_dt, recurring=False)
        assert executor._is_job_ready(job) is True

    # --- Issue #71: backward drift compensation ---

    def test_backward_drift_compensation_recovers_job(self, executor):
        """A job with next_run slightly in the future should be recovered
        when wall_clock_debt covers the gap (Issue #71)."""
        job = _make_job(
            job_id="drift-recover",
            next_run_dt=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        assert executor._is_job_ready(job) is False

        executor._wall_clock_debt = 60.0
        assert executor._is_job_ready(job) is True

    def test_backward_drift_compensation_increments_counter(self, executor):
        """Drift-recovered jobs should increment _drift_recovered_count."""
        assert executor._drift_recovered_count == 0

        executor._wall_clock_debt = 60.0
        job = _make_job(
            job_id="drift-count",
            next_run_dt=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        executor._is_job_ready(job)
        assert executor._drift_recovered_count == 1

    def test_compensation_insufficient_for_far_future_job(self, executor):
        """If next_run is further in the future than the debt, job stays not-ready."""
        executor._wall_clock_debt = 30.0
        job = _make_job(
            job_id="too-far",
            next_run_dt=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
        assert executor._is_job_ready(job) is False

    def test_no_compensation_when_no_debt(self, executor):
        """Without wall_clock_debt, future jobs should not be ready."""
        assert executor._wall_clock_debt == 0.0
        job = _make_job(
            job_id="no-debt",
            next_run_dt=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
        assert executor._is_job_ready(job) is False

    def test_compensation_with_stale_guard(self, executor):
        """Stale recurring jobs should still be blocked even with compensation."""
        from scheduler.executor import _MAX_CATCHUP_WINDOW

        executor._wall_clock_debt = 600.0
        stale_dt = datetime.now(timezone.utc) - timedelta(
            seconds=_MAX_CATCHUP_WINDOW + 3600
        )
        job = _make_job(next_run_dt=stale_dt, recurring=True)
        assert executor._is_job_ready(job) is False

    def test_compensation_with_monotonic_cooldown(self, executor):
        """Monotonic cooldown should still block even if drift compensation applies."""
        executor._wall_clock_debt = 60.0
        job = _make_job(
            job_id="cooldown-drift",
            next_run_dt=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        executor._job_last_exec_mono["cooldown-drift"] = time.monotonic()
        assert executor._is_job_ready(job) is False

    def test_invalid_next_run_logs_job_id(self, executor):
        """Invalid next_run should log the job ID, not just the format (Issue #71)."""
        job = _make_job(job_id="bad-format")
        job["next_run"] = "not-a-date"
        assert executor._is_job_ready(job) is False


# ---------------------------------------------------------------------------
# Tests: _recalculate_stale_jobs
# ---------------------------------------------------------------------------


class TestRecalculateStaleJobs:
    """Stale recurring jobs should get their next_run advanced."""

    def test_stale_job_gets_recalculated(self, executor):
        from scheduler.executor import _MAX_CATCHUP_WINDOW

        stale_dt = datetime.now(timezone.utc) - timedelta(
            seconds=_MAX_CATCHUP_WINDOW + 3600
        )
        job = _make_job(job_id="stale-1", next_run_dt=stale_dt, recurring=True)
        data = {"jobs": [job]}

        modified = executor._recalculate_stale_jobs(data)
        assert "stale-1" in modified
        assert "next_run" in modified["stale-1"]
        new_next = datetime.fromisoformat(
            modified["stale-1"]["next_run"].replace("Z", "+00:00")
        )
        assert new_next > datetime.now(timezone.utc)

    def test_non_stale_job_untouched(self, executor):
        """Jobs within the catchup window should not be recalculated."""
        recent_dt = datetime.now(timezone.utc) - timedelta(seconds=30)
        job = _make_job(job_id="recent-1", next_run_dt=recent_dt, recurring=True)
        data = {"jobs": [job]}

        modified = executor._recalculate_stale_jobs(data)
        assert "recent-1" not in modified

    def test_one_time_job_not_recalculated(self, executor):
        """One-time jobs should not be recalculated even if stale."""
        from scheduler.executor import _MAX_CATCHUP_WINDOW

        stale_dt = datetime.now(timezone.utc) - timedelta(
            seconds=_MAX_CATCHUP_WINDOW + 3600
        )
        job = _make_job(job_id="onetime-stale", next_run_dt=stale_dt, recurring=False)
        data = {"jobs": [job]}

        modified = executor._recalculate_stale_jobs(data)
        assert "onetime-stale" not in modified

    def test_invalid_next_run_logs_warning(self, executor):
        """Invalid next_run in _recalculate_stale_jobs should log, not silently skip."""
        job = _make_job(job_id="bad-recalc", recurring=True)
        job["next_run"] = "garbage-date"
        data = {"jobs": [job]}

        modified = executor._recalculate_stale_jobs(data)
        assert "bad-recalc" not in modified


# ---------------------------------------------------------------------------
# Tests: drift diagnostics
# ---------------------------------------------------------------------------


class TestDriftDiagnostics:
    """get_drift_diagnostics should return accurate state (Issue #71)."""

    def test_initial_diagnostics(self, executor):
        """Fresh executor should report no drift activity."""
        diag = executor.get_drift_diagnostics()
        assert diag["wall_clock_debt_seconds"] == 0.0
        assert diag["drift_compensation_active"] is False
        assert diag["drift_recovered_jobs"] == 0
        assert diag["recent_drift_events"] == []

    def test_diagnostics_after_backward_drift(self, executor):
        """After backward drift, diagnostics should reflect active compensation."""
        executor._wall_clock_debt = 45.0
        executor._drift_recovered_count = 3
        executor._drift_events.append(("2026-04-05T20:00:00Z", -45.0))

        diag = executor.get_drift_diagnostics()
        assert diag["wall_clock_debt_seconds"] == 45.0
        assert diag["drift_compensation_active"] is True
        assert diag["drift_recovered_jobs"] == 3
        assert len(diag["recent_drift_events"]) == 1

    def test_diagnostics_cap_reported(self, executor):
        """Diagnostics should include the compensation cap value."""
        from scheduler.executor import _DRIFT_COMPENSATION_CAP

        diag = executor.get_drift_diagnostics()
        assert diag["compensation_cap_seconds"] == _DRIFT_COMPENSATION_CAP


# ---------------------------------------------------------------------------
# Tests: check_and_execute integration
# ---------------------------------------------------------------------------


class TestCheckAndExecuteClockDrift:
    """Integration tests for the check_and_execute cycle with drift."""

    def test_normal_execution_records_monotonic(self, executor, tmp_scheduler_dir):
        """After executing a job, _job_last_exec_mono should be set."""
        _, jobs_file, _, _ = tmp_scheduler_dir
        job = _make_job(job_id="mono-track")
        _write_jobs(jobs_file, [job])

        with patch.object(executor, "_execute_task", return_value="ok"):
            executor.check_and_execute()

        assert "mono-track" in executor._job_last_exec_mono
        assert executor._job_last_exec_mono["mono-track"] > 0

    def test_stale_job_recalculated_during_check(self, executor, tmp_scheduler_dir):
        """Stale recurring jobs should be rescheduled during check_and_execute."""
        from scheduler.executor import _MAX_CATCHUP_WINDOW

        _, jobs_file, _, _ = tmp_scheduler_dir
        stale_dt = datetime.now(timezone.utc) - timedelta(
            seconds=_MAX_CATCHUP_WINDOW + 7200
        )
        job = _make_job(job_id="stale-check", next_run_dt=stale_dt)
        _write_jobs(jobs_file, [job])

        with patch.object(executor, "_execute_task", return_value="ok") as mock_exec:
            executor.check_and_execute()

        mock_exec.assert_not_called()

        data = json.loads(jobs_file.read_text())
        updated_job = data["jobs"][0]
        new_next = datetime.fromisoformat(
            updated_job["next_run"].replace("Z", "+00:00")
        )
        assert new_next > datetime.now(timezone.utc)

    def test_backward_jump_cooldown_prevents_rerun(self, executor, tmp_scheduler_dir):
        """Simulate backward clock jump: job already ran, should NOT re-execute."""
        _, jobs_file, _, _ = tmp_scheduler_dir
        job = _make_job(job_id="backward-test")
        _write_jobs(jobs_file, [job])

        executor._job_last_exec_mono["backward-test"] = time.monotonic()

        with patch.object(executor, "_execute_task", return_value="ok") as mock_exec:
            executor.check_and_execute()

        mock_exec.assert_not_called()

    def test_drift_detection_called(self, executor, tmp_scheduler_dir):
        """check_and_execute should call _detect_clock_drift."""
        _, jobs_file, _, _ = tmp_scheduler_dir
        _write_jobs(jobs_file, [])

        with patch.object(executor, "_detect_clock_drift", return_value=0.0) as mock:
            executor.check_and_execute()

        mock.assert_called_once()

    def test_backward_drift_recovers_job_in_check(self, executor, tmp_scheduler_dir):
        """End-to-end: backward drift + future job -> job should execute (Issue #71)."""
        _, jobs_file, _, _ = tmp_scheduler_dir

        job = _make_job(
            job_id="drift-e2e",
            next_run_dt=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        _write_jobs(jobs_file, [job])

        executor._wall_clock_debt = 60.0

        with patch.object(executor, "_execute_task", return_value="ok") as mock_exec:
            executor.check_and_execute()

        mock_exec.assert_called_once()
        assert executor._drift_recovered_count >= 1

    def test_no_false_recovery_without_debt(self, executor, tmp_scheduler_dir):
        """Without wall_clock_debt, future jobs should NOT execute."""
        _, jobs_file, _, _ = tmp_scheduler_dir

        job = _make_job(
            job_id="no-false-recover",
            next_run_dt=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        _write_jobs(jobs_file, [job])

        with patch.object(executor, "_execute_task", return_value="ok") as mock_exec:
            executor.check_and_execute()

        mock_exec.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: datetime.utcnow() removal verification
# ---------------------------------------------------------------------------


class TestNoUtcnow:
    """Verify that deprecated datetime.utcnow() is not used."""

    def test_no_utcnow_in_executor(self):
        executor_path = _REPO_ROOT / "scheduler" / "executor.py"
        content = executor_path.read_text()
        assert (
            "datetime.utcnow()" not in content
        ), "datetime.utcnow() is deprecated - use datetime.now(timezone.utc)"
