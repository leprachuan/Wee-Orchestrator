"""Tests for scheduler clock drift handling (Issue #70).

Verifies that the task scheduler executor correctly handles:
- Forward clock jumps (catchup execution)
- Backward clock jumps (monotonic cooldown prevents double-exec)
- Stale job recalculation (jobs past MAX_CATCHUP_WINDOW)
- Clock drift detection (monotonic vs wall clock)
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
        # Manually set last_check_wall to 60 seconds ago while monotonic is recent
        executor._last_check_wall = time.time() - 60
        executor._last_check_mono = time.monotonic() - 1

        drift = executor._detect_clock_drift()
        # Wall moved ~60s, mono moved ~1s → drift ≈ +59s
        assert drift > 50, f"Expected large forward drift, got {drift}"

    def test_backward_drift_detection(self, executor):
        """Simulate a backward wall-clock jump."""
        executor._last_check_wall = time.time() + 60  # wall was "in the future"
        executor._last_check_mono = time.monotonic() - 1

        drift = executor._detect_clock_drift()
        # Wall moved ~-60s, mono moved ~1s → drift ≈ -61s
        assert drift < -50, f"Expected large backward drift, got {drift}"


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
        from scheduler.executor import _MIN_EXEC_INTERVAL_MONO

        job = _make_job(job_id="cooldown-test")
        # Mark as recently executed
        executor._job_last_exec_mono["cooldown-test"] = time.monotonic()

        assert executor._is_job_ready(job) is False, (
            "Job should be blocked by monotonic cooldown"
        )

    def test_monotonic_cooldown_expires(self, executor):
        """After the cooldown expires, the job should be ready again."""
        from scheduler.executor import _MIN_EXEC_INTERVAL_MONO

        job = _make_job(job_id="cooldown-expire")
        # Mark as executed long enough ago
        executor._job_last_exec_mono["cooldown-expire"] = (
            time.monotonic() - _MIN_EXEC_INTERVAL_MONO - 1
        )

        assert executor._is_job_ready(job) is True

    def test_stale_recurring_job_not_ready(self, executor):
        """A recurring job past MAX_CATCHUP_WINDOW should NOT be ready
        (it gets rescheduled instead)."""
        from scheduler.executor import _MAX_CATCHUP_WINDOW

        stale_dt = datetime.now(timezone.utc) - timedelta(
            seconds=_MAX_CATCHUP_WINDOW + 600
        )
        job = _make_job(next_run_dt=stale_dt, recurring=True)
        assert executor._is_job_ready(job) is False

    def test_stale_one_time_job_still_ready(self, executor):
        """A one-time job past MAX_CATCHUP_WINDOW should STILL be ready
        (one-time jobs should always execute eventually)."""
        from scheduler.executor import _MAX_CATCHUP_WINDOW

        stale_dt = datetime.now(timezone.utc) - timedelta(
            seconds=_MAX_CATCHUP_WINDOW + 600
        )
        job = _make_job(next_run_dt=stale_dt, recurring=False)
        assert executor._is_job_ready(job) is True


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
        # The new next_run should be in the future
        new_next = datetime.fromisoformat(
            modified["stale-1"]["next_run"].replace("Z", "+00:00")
        )
        assert new_next > datetime.now(timezone.utc), (
            f"Recalculated next_run should be in the future, got {new_next}"
        )

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

        # The stale job should NOT have been executed
        mock_exec.assert_not_called()

        # But its next_run should have been updated in jobs.json
        data = json.loads(jobs_file.read_text())
        updated_job = data["jobs"][0]
        new_next = datetime.fromisoformat(
            updated_job["next_run"].replace("Z", "+00:00")
        )
        assert new_next > datetime.now(timezone.utc), (
            "Stale job's next_run should have been advanced to the future"
        )

    def test_backward_jump_cooldown_prevents_rerun(
        self, executor, tmp_scheduler_dir
    ):
        """Simulate backward clock jump: job already ran (monotonic recorded),
        next_run recalculated to a past slot. Job should NOT re-execute."""
        _, jobs_file, _, _ = tmp_scheduler_dir
        job = _make_job(job_id="backward-test")
        _write_jobs(jobs_file, [job])

        # Record that we already executed this job very recently
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


# ---------------------------------------------------------------------------
# Tests: datetime.utcnow() removal verification
# ---------------------------------------------------------------------------

class TestNoUtcnow:
    """Verify that deprecated datetime.utcnow() is not used."""

    def test_no_utcnow_in_executor(self):
        executor_path = _REPO_ROOT / "scheduler" / "executor.py"
        content = executor_path.read_text()
        assert "datetime.utcnow()" not in content, (
            "datetime.utcnow() is deprecated — use datetime.now(timezone.utc)"
        )
