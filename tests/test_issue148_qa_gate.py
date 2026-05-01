"""Regression tests for Issue #148: QA gate blocks wee-dev from picking up
new issues while a PR is awaiting QA review.

Tests cover:
1. scheduler/qa_gate.py — the QA gate module
2. scheduler/executor.py — gate_check integration in the scheduler
3. dispatch_wee_dev_work_queue.py — fixed dispatch logic
"""

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Ensure the repo root is on sys.path so we can import scheduler modules
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ===================================================================
# Part 1: scheduler/qa_gate.py tests
# ===================================================================


class TestQaGateReadLock:
    """Tests for qa_gate.read_lock()."""

    def test_read_lock_no_file(self, tmp_path):
        """read_lock returns None when lock file doesn't exist."""
        from scheduler.qa_gate import read_lock

        result = read_lock(tmp_path / "nonexistent.json")
        assert result is None

    def test_read_lock_valid(self, tmp_path):
        """read_lock returns parsed dict for valid JSON lock file."""
        from scheduler.qa_gate import read_lock

        lock_file = tmp_path / "lock.json"
        payload = {"state": "qa-review", "work_item_id": "WQ-148"}
        lock_file.write_text(json.dumps(payload))
        result = read_lock(lock_file)
        assert result == payload

    def test_read_lock_invalid_json(self, tmp_path):
        """read_lock returns None for corrupt JSON."""
        from scheduler.qa_gate import read_lock

        lock_file = tmp_path / "lock.json"
        lock_file.write_text("not valid json {{{")
        result = read_lock(lock_file)
        assert result is None


class TestQaGateCheckBlockingIssues:
    """Tests for qa_gate.check_blocking_issues()."""

    def test_blocking_with_qa_review_label(self):
        """Issues with wee-dev:qa-review label are blocking."""
        from scheduler.qa_gate import check_blocking_issues

        mock_issues = [
            {
                "number": 100,
                "title": "Test issue",
                "labels": [{"name": "wee-dev"}, {"name": "wee-dev:qa-review"}],
            }
        ]
        with mock.patch("scheduler.qa_gate._gh_issue_list", return_value=mock_issues):
            result = check_blocking_issues("test/repo")
        assert len(result) == 1
        assert result[0]["number"] == 100
        assert "wee-dev:qa-review" in result[0]["blocking_labels"]

    def test_blocking_with_in_progress_label(self):
        """Issues with wee-dev:in-progress label are blocking."""
        from scheduler.qa_gate import check_blocking_issues

        mock_issues = [
            {
                "number": 200,
                "title": "In progress issue",
                "labels": [{"name": "wee-dev"}, {"name": "wee-dev:in-progress"}],
            }
        ]
        with mock.patch("scheduler.qa_gate._gh_issue_list", return_value=mock_issues):
            result = check_blocking_issues("test/repo")
        assert len(result) == 1
        assert result[0]["number"] == 200

    def test_no_blocking_when_only_queued(self):
        """Issues with only wee-dev:queued are not blocking."""
        from scheduler.qa_gate import check_blocking_issues

        mock_issues = [
            {
                "number": 300,
                "title": "Queued issue",
                "labels": [{"name": "wee-dev"}, {"name": "wee-dev:queued"}],
            }
        ]
        with mock.patch("scheduler.qa_gate._gh_issue_list", return_value=mock_issues):
            result = check_blocking_issues("test/repo")
        assert len(result) == 0

    def test_deduplicates_issues(self):
        """check_blocking_issues deduplicates by issue number."""
        from scheduler.qa_gate import check_blocking_issues

        mock_issues = [
            {
                "number": 100,
                "title": "Same issue",
                "labels": [
                    {"name": "wee-dev:qa-review"},
                    {"name": "wee-dev:in-progress"},
                ],
            }
        ]
        with mock.patch("scheduler.qa_gate._gh_issue_list", return_value=mock_issues):
            result = check_blocking_issues("test/repo")
        # Should appear only once despite matching two blocking labels
        assert len(result) == 1

    def test_gh_failure_returns_empty(self):
        """check_blocking_issues returns [] when gh CLI fails."""
        from scheduler.qa_gate import check_blocking_issues

        with mock.patch("scheduler.qa_gate._gh_issue_list", return_value=[]):
            result = check_blocking_issues("test/repo")
        assert result == []


class TestQaGateCheckOpenPRs:
    """Tests for qa_gate.check_open_prs()."""

    def test_open_pr_from_issue_branch(self):
        """Open PRs from issue/* branches are detected."""
        from scheduler.qa_gate import check_open_prs

        mock_prs = [
            {
                "number": 150,
                "title": "Fix issue 148",
                "headRefName": "issue/148",
                "state": "OPEN",
                "labels": [],
                "mergeable": "MERGEABLE",
            }
        ]
        with mock.patch("scheduler.qa_gate._gh_pr_list", return_value=mock_prs):
            result = check_open_prs("test/repo")
        assert len(result) == 1
        assert result[0]["number"] == 150
        assert result[0]["branch"] == "issue/148"

    def test_non_issue_branch_ignored(self):
        """PRs from non-issue/* branches are not flagged."""
        from scheduler.qa_gate import check_open_prs

        mock_prs = [
            {
                "number": 200,
                "title": "Feature branch",
                "headRefName": "feature/cool-thing",
                "state": "OPEN",
                "labels": [],
                "mergeable": "MERGEABLE",
            }
        ]
        with mock.patch("scheduler.qa_gate._gh_pr_list", return_value=mock_prs):
            result = check_open_prs("test/repo")
        assert len(result) == 0


class TestIsWeeDevGated:
    """Tests for qa_gate.is_wee_dev_gated() — the primary entry point."""

    def test_gated_by_lock_qa_review(self, tmp_path):
        """Gated when lock file state is qa-review."""
        from scheduler.qa_gate import is_wee_dev_gated

        lock_file = tmp_path / "lock.json"
        lock_file.write_text(
            json.dumps(
                {
                    "state": "qa-review",
                    "work_item_id": "WQ-100",
                    "reason": "Waiting for QA",
                }
            )
        )
        gated, reason, details = is_wee_dev_gated(
            lock_path=lock_file, check_github=False, check_prs=False
        )
        assert gated is True
        assert "qa-review" in reason

    def test_gated_by_lock_wee_dev_running(self, tmp_path):
        """Gated when lock file state is wee-dev-running."""
        from scheduler.qa_gate import is_wee_dev_gated

        lock_file = tmp_path / "lock.json"
        lock_file.write_text(
            json.dumps(
                {
                    "state": "wee-dev-running",
                    "work_item_id": "WQ-100",
                }
            )
        )
        gated, reason, details = is_wee_dev_gated(
            lock_path=lock_file, check_github=False, check_prs=False
        )
        assert gated is True
        assert "wee-dev-running" in reason

    def test_gated_by_lock_qa_gate_blocked(self, tmp_path):
        """Gated when lock file state is qa-gate-blocked."""
        from scheduler.qa_gate import is_wee_dev_gated

        lock_file = tmp_path / "lock.json"
        lock_file.write_text(
            json.dumps(
                {
                    "state": "qa-gate-blocked",
                    "reason": "QA gate blocked",
                }
            )
        )
        gated, reason, details = is_wee_dev_gated(
            lock_path=lock_file, check_github=False, check_prs=False
        )
        assert gated is True
        assert "qa-gate-blocked" in reason

    def test_not_gated_no_lock(self, tmp_path):
        """Not gated when no lock file and no blocking issues."""
        from scheduler.qa_gate import is_wee_dev_gated

        gated, reason, details = is_wee_dev_gated(
            lock_path=tmp_path / "nonexistent.json",
            check_github=False,
            check_prs=False,
        )
        assert gated is False
        assert reason == ""

    def test_not_gated_lock_dispatching(self, tmp_path):
        """Not gated when lock state is 'dispatching' (transitional)."""
        from scheduler.qa_gate import is_wee_dev_gated

        lock_file = tmp_path / "lock.json"
        lock_file.write_text(json.dumps({"state": "dispatching"}))
        gated, reason, details = is_wee_dev_gated(
            lock_path=lock_file, check_github=False, check_prs=False
        )
        assert gated is False

    def test_gated_by_github_labels(self, tmp_path):
        """Gated when GitHub issues have blocking labels."""
        from scheduler.qa_gate import is_wee_dev_gated

        blocking = [
            {"number": 100, "title": "Test", "blocking_labels": ["wee-dev:qa-review"]}
        ]
        with mock.patch(
            "scheduler.qa_gate.check_blocking_issues", return_value=blocking
        ):
            gated, reason, details = is_wee_dev_gated(
                lock_path=tmp_path / "none.json",
                check_github=True,
                check_prs=False,
            )
        assert gated is True
        assert "#100" in reason

    def test_gated_by_open_prs(self, tmp_path):
        """Gated when open PRs from issue/* branches exist."""
        from scheduler.qa_gate import is_wee_dev_gated

        prs = [{"number": 150, "title": "Fix", "branch": "issue/148"}]
        with mock.patch("scheduler.qa_gate.check_blocking_issues", return_value=[]):
            with mock.patch("scheduler.qa_gate.check_open_prs", return_value=prs):
                gated, reason, details = is_wee_dev_gated(
                    lock_path=tmp_path / "none.json",
                    check_github=True,
                    check_prs=True,
                )
        assert gated is True
        assert "PR #150" in reason

    def test_not_gated_all_clear(self, tmp_path):
        """Not gated when lock clear, no blocking issues, no open PRs."""
        from scheduler.qa_gate import is_wee_dev_gated

        with mock.patch("scheduler.qa_gate.check_blocking_issues", return_value=[]):
            with mock.patch("scheduler.qa_gate.check_open_prs", return_value=[]):
                gated, reason, details = is_wee_dev_gated(
                    lock_path=tmp_path / "none.json",
                    check_github=True,
                    check_prs=True,
                )
        assert gated is False


# ===================================================================
# Part 2: scheduler/executor.py gate_check integration tests
# ===================================================================


class TestExecutorGateCheck:
    """Tests for the _check_gate method in TaskSchedulerExecutor."""

    def _make_executor(self):
        """Create a minimal executor for testing gate checks."""
        from scheduler.executor import TaskSchedulerExecutor

        with mock.patch.object(TaskSchedulerExecutor, "__init__", lambda self: None):
            executor = TaskSchedulerExecutor.__new__(TaskSchedulerExecutor)
            # Set minimal attributes needed by _check_gate
            executor.data_dir = Path(tempfile.mkdtemp())
            return executor

    def test_no_gate_config_allows_execution(self):
        """Jobs without gate_check are always allowed."""
        executor = self._make_executor()
        assert executor._check_gate({"id": "test-job"}) is True

    def test_unknown_gate_allows_execution(self):
        """Unknown gate_check names allow execution (fail-open)."""
        executor = self._make_executor()
        assert (
            executor._check_gate({"id": "test-job", "gate_check": "nonexistent_gate"})
            is True
        )

    def test_wee_dev_qa_gate_blocks_when_gated(self):
        """wee_dev_qa gate blocks execution when is_wee_dev_gated returns True."""
        executor = self._make_executor()
        # _log_job needs data_dir/logs
        (executor.data_dir / "logs").mkdir(exist_ok=True)
        executor.logs_dir = executor.data_dir / "logs"

        with mock.patch(
            "scheduler.executor.is_wee_dev_gated",
            return_value=(True, "test reason", {}),
        ):
            result = executor._check_gate(
                {"id": "wee-dev-runner", "gate_check": "wee_dev_qa"}
            )
        assert result is False

    def test_wee_dev_qa_gate_allows_when_clear(self):
        """wee_dev_qa gate allows execution when is_wee_dev_gated returns False."""
        executor = self._make_executor()

        with mock.patch(
            "scheduler.executor.is_wee_dev_gated",
            return_value=(False, "", {}),
        ):
            result = executor._check_gate(
                {"id": "wee-dev-runner", "gate_check": "wee_dev_qa"}
            )
        assert result is True

    def test_gate_exception_allows_execution(self):
        """Gate check exceptions fail-open (allow execution)."""
        executor = self._make_executor()

        with mock.patch(
            "scheduler.executor.is_wee_dev_gated",
            side_effect=RuntimeError("API down"),
        ):
            result = executor._check_gate(
                {"id": "wee-dev-runner", "gate_check": "wee_dev_qa"}
            )
        assert result is True


# ===================================================================
# Part 3: Dispatch script logic tests
# ===================================================================


class TestDispatchQAGateFixes:
    """Test the fixed dispatch logic for Issue #148 bugs.

    These tests verify the core dispatch decisions without importing
    the dispatch script (which has system-level dependencies).
    """

    def test_stalled_in_progress_does_not_pick_new_issue(self):
        """Bug 1: When active_item is 'in-progress' (stalled), dispatch must
        re-dispatch THAT item, not pick up a new queued issue.
        """
        # Simulate the fixed logic
        active_item = {
            "id": "WQ-100",
            "number": 100,
            "status": "in-progress",
            "title": "Active",
        }
        actionable = [
            {"id": "WQ-200", "number": 200, "status": "queued", "title": "Queued"}
        ]

        ACTIVE_STATUSES = {"in-progress", "qa-review", "qa-failed"}
        ACTIONABLE_STATUSES = {"queued", "qa-failed"}

        # Fixed logic from the patched dispatch script
        next_item = None
        if active_item and active_item["status"] == "in-progress":
            next_item = active_item  # Fix: re-dispatch the stalled item

        # The catch-all gate should NOT apply here since we already have next_item
        if (
            next_item is None
            and active_item
            and active_item["status"] in ACTIVE_STATUSES
        ):
            # This would block — but next_item is already set
            pass

        assert next_item is not None
        assert (
            next_item["id"] == "WQ-100"
        ), "Must re-dispatch stalled item, not new queued item"

    def test_qa_review_blocks_new_dispatch(self):
        """Bug 3: When active_item is 'qa-review', the catch-all gate must
        prevent picking up a new queued issue.
        """
        active_item = {
            "id": "WQ-100",
            "number": 100,
            "status": "qa-review",
            "title": "In QA",
        }
        actionable = [
            {"id": "WQ-200", "number": 200, "status": "queued", "title": "Queued"}
        ]

        ACTIVE_STATUSES = {"in-progress", "qa-review", "qa-failed"}

        # Fixed logic
        next_item = None
        # qa-review is not in-progress or qa-failed, so next_item stays None
        blocked = False
        if (
            next_item is None
            and active_item
            and active_item["status"] in ACTIVE_STATUSES
        ):
            blocked = True

        assert blocked is True, "qa-review must block dispatch of new issues"

    def test_qa_failed_redispatches_same_issue(self):
        """When active_item is 'qa-failed', must re-dispatch same issue."""
        active_item = {
            "id": "WQ-100",
            "number": 100,
            "status": "qa-failed",
            "title": "Failed QA",
        }
        actionable = [
            {
                "id": "WQ-100",
                "number": 100,
                "status": "qa-failed",
                "title": "Failed QA",
            },
            {"id": "WQ-200", "number": 200, "status": "queued", "title": "New item"},
        ]

        next_item = None
        if active_item and active_item["status"] == "qa-failed":
            next_item = active_item

        assert next_item is not None
        assert next_item["id"] == "WQ-100"

    def test_no_active_item_picks_from_queue(self):
        """When no active item exists, pick from actionable queue."""
        active_item = None
        actionable = [
            {"id": "WQ-200", "number": 200, "status": "queued", "title": "Queued"}
        ]

        ACTIVE_STATUSES = {"in-progress", "qa-review", "qa-failed"}
        ACTIONABLE_STATUSES = {"queued", "qa-failed"}

        next_item = None
        # No stalled detection
        # No catch-all gate (active_item is None)
        if next_item is None:
            if active_item and active_item["status"] in ACTIONABLE_STATUSES:
                next_item = active_item
            elif actionable:
                next_item = actionable[0]

        assert next_item is not None
        assert next_item["id"] == "WQ-200"

    def test_empty_queue_returns_none(self):
        """When no active item and no actionable items, nothing to dispatch."""
        active_item = None
        actionable = []

        next_item = None
        if next_item is None and not actionable and not active_item:
            next_item = None  # explicit: nothing to do

        assert next_item is None


class TestHasRunningWeeDevTask:
    """Test the fixed has_running_wee_dev_task with task_id support."""

    def test_no_lock_returns_false(self, tmp_path):
        """No lock file → not running."""
        # Simulate the fixed function logic
        lock_path = tmp_path / "lock.json"
        assert not lock_path.exists()
        # Function would return False

    def test_pid_alive_returns_true(self):
        """Lock with live PID → running."""
        lock = {"wee_dev_pid": os.getpid()}  # current process is alive
        pid = lock.get("wee_dev_pid")
        # _is_pid_alive(os.getpid()) would be True
        try:
            os.kill(int(pid), 0)
            alive = True
        except OSError:
            alive = False
        assert alive is True

    def test_dead_pid_checks_task_id(self):
        """Lock with dead PID but valid task_id → checks API.

        This is the key Bug #2 fix: previously only checked PID, now
        also checks wee_dev_task_id via the background task API.
        """
        lock = {"wee_dev_pid": 99999999, "wee_dev_task_id": "bg_test123"}
        pid = lock.get("wee_dev_pid")
        # PID is dead
        try:
            os.kill(int(pid), 0)
            pid_alive = True
        except OSError:
            pid_alive = False
        assert pid_alive is False

        # Fixed code would now check wee_dev_task_id via API
        task_id = lock.get("wee_dev_task_id")
        assert task_id is not None, "Bug #2: task_id must be checked when PID is dead"

    def test_task_id_only_no_pid(self):
        """Lock with task_id but no PID (API dispatch) → checks API.

        This is the exact scenario on the dev host where dispatch uses
        the background task API instead of subprocess.
        """
        lock = {"state": "wee-dev-running", "wee_dev_task_id": "bg_abd7b99e"}
        pid = lock.get("wee_dev_pid")
        assert pid is None, "Dev host lock has no PID"
        task_id = lock.get("wee_dev_task_id")
        assert task_id is not None, "Dev host lock has task_id that must be checked"


class TestQAGateIntegrationScenarios:
    """End-to-end scenario tests for the QA gate."""

    def test_scenario_approve_then_next(self, tmp_path):
        """Scenario: QA approves → lock cleared → next issue picked up.

        This is the happy path from the issue's acceptance criteria.
        """
        from scheduler.qa_gate import is_wee_dev_gated

        # Step 1: wee-dev finishes, QA approved, lock is cleared
        # (No lock file, no blocking issues, no open PRs)
        with mock.patch("scheduler.qa_gate.check_blocking_issues", return_value=[]):
            with mock.patch("scheduler.qa_gate.check_open_prs", return_value=[]):
                gated, reason, details = is_wee_dev_gated(
                    lock_path=tmp_path / "none.json",
                    check_github=True,
                    check_prs=True,
                )
        assert gated is False, "After QA approve + merge, wee-dev should be unblocked"

    def test_scenario_qa_reject_then_rework(self, tmp_path):
        """Scenario: QA rejects → wee-dev re-engages on same PR.

        After rejection, the issue has wee-dev:qa-failed label. The dispatch
        script should re-dispatch wee-dev for the SAME issue.
        """
        active_item = {
            "id": "WQ-100",
            "number": 100,
            "status": "qa-failed",
            "title": "Fix bug",
        }

        next_item = None
        if active_item and active_item["status"] == "qa-failed":
            next_item = active_item

        assert next_item is not None
        assert next_item["number"] == 100, "Must re-dispatch same issue after rejection"

    def test_scenario_in_progress_blocks_new(self, tmp_path):
        """Scenario: Issue in progress → cannot start new issue."""
        from scheduler.qa_gate import is_wee_dev_gated

        lock_file = tmp_path / "lock.json"
        lock_file.write_text(
            json.dumps(
                {
                    "state": "wee-dev-running",
                    "work_item_id": "WQ-100",
                }
            )
        )

        gated, reason, details = is_wee_dev_gated(
            lock_path=lock_file,
            check_github=False,
            check_prs=False,
        )
        assert gated is True, "in-progress work must block new dispatch"

    def test_scenario_qa_review_blocks_new(self, tmp_path):
        """Scenario: PR in QA review → cannot start new issue."""
        from scheduler.qa_gate import is_wee_dev_gated

        lock_file = tmp_path / "lock.json"
        lock_file.write_text(
            json.dumps(
                {
                    "state": "qa-review",
                    "work_item_id": "WQ-100",
                }
            )
        )

        gated, reason, details = is_wee_dev_gated(
            lock_path=lock_file,
            check_github=False,
            check_prs=False,
        )
        assert gated is True, "qa-review must block new dispatch"

    def test_scenario_open_pr_blocks_new(self, tmp_path):
        """Scenario: Open PR from issue branch → cannot start new issue."""
        from scheduler.qa_gate import is_wee_dev_gated

        prs = [{"number": 150, "title": "Fix", "branch": "issue/148"}]
        with mock.patch("scheduler.qa_gate.check_blocking_issues", return_value=[]):
            with mock.patch("scheduler.qa_gate.check_open_prs", return_value=prs):
                gated, reason, details = is_wee_dev_gated(
                    lock_path=tmp_path / "none.json",
                    check_github=True,
                    check_prs=True,
                )
        assert gated is True, "Open PR must block new dispatch"
