#!/usr/bin/env python3
"""Tests for issue #74: dispatch_wee_dev_work_queue uses subprocess instead of public API."""  # noqa: E501

import importlib.util
import json
import os
import subprocess
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

DISPATCHER_PATH = Path("/opt/bin/dispatch_wee_dev_work_queue.py")


def _load_dispatcher() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "dispatch_wee_dev_work_queue", DISPATCHER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def disp():
    return _load_dispatcher()


@pytest.fixture()
def tmp_lock(tmp_path, disp):
    original = disp.LOCK_PATH
    disp.LOCK_PATH = tmp_path / "test.lock.json"
    yield disp.LOCK_PATH
    disp.LOCK_PATH = original


# _is_pid_alive tests


def test_is_pid_alive_current_process(disp):
    assert disp._is_pid_alive(os.getpid()) is True


def test_is_pid_alive_nonexistent_pid(disp):
    assert disp._is_pid_alive(999999999) is False


def test_is_pid_alive_dead_process(disp):
    proc = subprocess.Popen(["true"])
    proc.wait()
    assert disp._is_pid_alive(proc.pid) is False


# dispatch_via_subprocess tests


def test_dispatch_via_subprocess_returns_pid(disp, tmp_path):
    with (
        patch.object(disp, "DISPATCH_LOG_DIR", tmp_path),
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc
        pid = disp.dispatch_via_subprocess(
            "wee-dev", "test prompt", "claude-opus-4.6", 3600
        )
    assert pid == 12345


def test_dispatch_via_subprocess_uses_start_new_session(disp, tmp_path):
    with (
        patch.object(disp, "DISPATCH_LOG_DIR", tmp_path),
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc
        disp.dispatch_via_subprocess("wee-dev", "prompt", "model", 60)
    _, kwargs = mock_popen.call_args
    assert kwargs.get("start_new_session") is True


def test_dispatch_via_subprocess_calls_agent_manager(disp, tmp_path):
    with (
        patch.object(disp, "DISPATCH_LOG_DIR", tmp_path),
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_proc = MagicMock()
        mock_proc.pid = 99
        mock_popen.return_value = mock_proc
        disp.dispatch_via_subprocess("wee-dev", "my prompt", "claude-sonnet-4.6", 1800)
    cmd = mock_popen.call_args[0][0]
    assert any("agent_manager.py" in str(a) for a in cmd)


def test_dispatch_via_subprocess_passes_agent_and_model(disp, tmp_path):
    with (
        patch.object(disp, "DISPATCH_LOG_DIR", tmp_path),
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_proc = MagicMock()
        mock_proc.pid = 7
        mock_popen.return_value = mock_proc
        disp.dispatch_via_subprocess("wee-dev", "p", "claude-opus-4.6", 3600)
    cmd = mock_popen.call_args[0][0]
    assert "--agent" in cmd
    assert "wee-dev" in cmd
    assert "--model" in cmd
    assert "claude-opus-4.6" in cmd


def test_dispatch_via_subprocess_no_api_call(disp, tmp_path):
    with (
        patch.object(disp, "DISPATCH_LOG_DIR", tmp_path),
        patch("subprocess.Popen") as mock_popen,
        patch.object(disp, "api_request") as mock_api,
    ):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_popen.return_value = mock_proc
        disp.dispatch_via_subprocess("wee-dev", "p", "m", 60)
    mock_api.assert_not_called()


# dispatch_wee_dev tests


@pytest.fixture()
def sample_item():
    return {
        "number": 74,
        "id": "#74",
        "title": "Bug: test issue",
        "body": "Test body content",
        "status": "queued",
        "labels": {"wee-dev"},
    }


def test_dispatch_wee_dev_returns_pid_dict(disp, sample_item):
    with (
        patch.object(disp, "DRY_RUN", False),
        patch.object(disp, "dispatch_via_subprocess", return_value=1001),
    ):
        result = disp.dispatch_wee_dev(sample_item)
    assert "pid" in result
    assert result["pid"] == 1001


def test_dispatch_wee_dev_no_background_task_api(disp, sample_item):
    with (
        patch.object(disp, "DRY_RUN", False),
        patch.object(disp, "dispatch_via_subprocess", return_value=2002),
        patch.object(disp, "api_request") as mock_api,
    ):
        disp.dispatch_wee_dev(sample_item)
    mock_api.assert_not_called()


def test_dispatch_wee_dev_dry_run(disp, sample_item):
    with patch.object(disp, "DRY_RUN", True), patch("subprocess.Popen") as mock_popen:
        result = disp.dispatch_wee_dev(sample_item)
    mock_popen.assert_not_called()
    assert result == {"pid": -1}


def test_dispatch_wee_dev_uses_opus_model(disp, sample_item):
    captured = {}

    def fake_dispatch(agent, prompt, model, timeout, session_id=None):
        captured["model"] = model
        captured["agent"] = agent
        return 555

    with (
        patch.object(disp, "DRY_RUN", False),
        patch.object(disp, "dispatch_via_subprocess", side_effect=fake_dispatch),
    ):
        disp.dispatch_wee_dev(sample_item)

    assert captured["agent"] == "wee-dev"
    assert captured["model"] == "claude-opus-4.6"


# dispatch_wee_qa tests removed — wee-qa consolidated into wee-dev


# has_running tests


def test_has_running_wee_dev_no_lock(disp, tmp_lock):
    assert not disp.has_running_wee_dev_task()


def test_has_running_wee_dev_with_alive_pid(disp, tmp_lock):
    disp.LOCK_PATH.write_text(json.dumps({"wee_dev_pid": os.getpid()}))
    assert disp.has_running_wee_dev_task() is True


def test_has_running_wee_dev_with_dead_pid(disp, tmp_lock):
    disp.LOCK_PATH.write_text(json.dumps({"wee_dev_pid": 999999999}))
    assert disp.has_running_wee_dev_task() is False


# Tests for wee_qa_pid removed — wee-qa consolidated into wee-dev


# Lock file / no-API-POST tests


def test_dispatch_wee_dev_result_has_pid_not_task_id(disp, sample_item):
    with (
        patch.object(disp, "DRY_RUN", False),
        patch.object(disp, "dispatch_via_subprocess", return_value=8888),
    ):
        result = disp.dispatch_wee_dev(sample_item)
    assert "task_id" not in result
    assert "pid" in result


# Test for dispatch_wee_qa removed — wee-qa consolidated into wee-dev


def test_background_tasks_url_not_posted_to(disp, sample_item):
    posted_urls = []

    def fake_api(method, url, *a, **kw):
        if method == "POST":
            posted_urls.append(url)
        return {}

    with (
        patch.object(disp, "DRY_RUN", False),
        patch.object(disp, "dispatch_via_subprocess", return_value=1),
        patch.object(disp, "api_request", side_effect=fake_api),
    ):
        disp.dispatch_wee_dev(sample_item)

    bg_url = disp.BACKGROUND_TASKS_URL
    assert (
        bg_url not in posted_urls
    ), "BACKGROUND_TASKS_URL was POSTed to — session leakage bug still present"
