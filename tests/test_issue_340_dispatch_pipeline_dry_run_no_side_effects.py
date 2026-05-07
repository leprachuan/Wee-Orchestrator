"""Regression test for issue #340 dry-run side effects in dispatch_pipeline."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dispatch_pipeline():
    spec = importlib.util.spec_from_file_location(
        "dispatch_pipeline_issue340",
        REPO_ROOT / "scripts" / "dispatch_pipeline.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestIssue340DispatchPipelineDryRunNoSideEffects(unittest.TestCase):
    def _run_and_assert_no_side_effects(
        self,
        item: dict,
        state: dict,
        running_task: bool = False,
        elapsed_minutes: float | None = 120.0,
    ):
        dispatch_pipeline = _load_dispatch_pipeline()
        dispatch_pipeline.DRY_RUN = True

        with patch.object(
            dispatch_pipeline, "get_open_wee_dev_issues", return_value=[item]
        ), patch.object(
            dispatch_pipeline, "load_state", return_value=state
        ), patch.object(
            dispatch_pipeline, "is_task_running", return_value=running_task
        ), patch.object(
            dispatch_pipeline, "minutes_since", return_value=elapsed_minutes
        ), patch.object(
            dispatch_pipeline, "dispatch_via_api"
        ) as mock_dispatch, patch.object(
            dispatch_pipeline, "save_state"
        ) as mock_save_state, patch.object(
            dispatch_pipeline.subprocess, "run"
        ) as mock_subprocess:
            dispatch_pipeline.run_pipeline()

        mock_dispatch.assert_not_called()
        mock_save_state.assert_not_called()
        mock_subprocess.assert_not_called()

    def test_issue_340_dispatch_pipeline_dry_run_has_no_side_effects(self):
        queued_owner_issue = {
            "number": 3401,
            "title": "Queued issue",
            "body": "queued",
            "author": {"login": "leprachuan"},
            "labels": [{"name": "wee-dev"}],
        }
        needs_approval_issue = {
            "number": 3402,
            "title": "Needs approval issue",
            "body": "approval",
            "author": {"login": "external-contributor"},
            "labels": [{"name": "wee-dev"}],
        }
        stalled_in_progress_issue = {
            "number": 3403,
            "title": "In-progress issue",
            "body": "stalled",
            "author": {"login": "leprachuan"},
            "labels": [{"name": "wee-dev"}, {"name": "wee-dev:in-progress"}],
        }

        self._run_and_assert_no_side_effects(queued_owner_issue, state={})
        self._run_and_assert_no_side_effects(needs_approval_issue, state={})
        self._run_and_assert_no_side_effects(
            stalled_in_progress_issue,
            state={
                str(stalled_in_progress_issue["number"]): {
                    "wee_dev_task_id": "bg_fake_340",
                    "wee_dev_dispatched_at": "2026-05-01T00:00:00+00:00",
                }
            },
            running_task=False,
            elapsed_minutes=120.0,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
