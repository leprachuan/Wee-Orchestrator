"""Regression test for issue #325: Scheduled task fallback runtime does not persist after save.

The bug was in the WebUI frontend (app.js): buildJobForm() rendered fallback runtime/model
<select> elements without data-current attributes. When populateFallbackRuntimeDropdown()
ran asynchronously, it had no stored value to pre-select, defaulting to "None (no fallback)".

This test validates the full API round-trip: set fallback_runtime via PUT, verify GET
returns the correct value on subsequent retrieval, simulating the "save and reopen" flow.
"""

import os
import sys
from pathlib import Path
import pytest

os.environ.setdefault("API_SHARED_KEY", "test_key_123")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_manager import create_api_app

try:
    from starlette.testclient import TestClient
except ImportError:
    from fastapi.testclient import TestClient


class TestFallbackRuntimeDisplayPersistence:
    """Tests for issue #325: fallback runtime display persistence after save."""

    @pytest.fixture
    def client(self, tmp_path):
        # Issues #499/#500/#501/#504: every job this suite creates used to land
        # in the real .task-scheduler/jobs.json (create_api_app() and
        # TaskScheduler both fall back to that shared file when
        # SCHEDULER_JOBS_FILE is unset). With no fixed IDs and no teardown,
        # each test run -- daily in CI, or any time a developer runs pytest --
        # added a fresh, permanent, real recurring job. Pointing
        # SCHEDULER_JOBS_FILE/SCHEDULER_LOGS_DIR/SCHEDULER_RESULTS_DIR at a
        # tmp_path for the duration of the test isolates every job this suite
        # creates to storage that is deleted when the test ends.
        jobs_file = tmp_path / "jobs.json"
        logs_dir = tmp_path / "logs"
        results_dir = tmp_path / "results"
        logs_dir.mkdir()
        results_dir.mkdir()
        jobs_file.write_text('{"jobs": []}')

        os.environ["SCHEDULER_JOBS_FILE"] = str(jobs_file)
        os.environ["SCHEDULER_LOGS_DIR"] = str(logs_dir)
        os.environ["SCHEDULER_RESULTS_DIR"] = str(results_dir)
        try:
            app = create_api_app()
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            os.environ.pop("SCHEDULER_JOBS_FILE", None)
            os.environ.pop("SCHEDULER_LOGS_DIR", None)
            os.environ.pop("SCHEDULER_RESULTS_DIR", None)

    @pytest.fixture
    def auth_headers(self):
        return {
            "Authorization": "Bearer shared_test_key_123",
            "X-User-Identity": "test-user",
            "X-Auth-Channel": "api",
        }

    def _create_base_job(self, client, auth_headers, name="Base Task #325"):
        payload = {
            "name": name,
            "schedule": "every day at 10am",
            "agent": "orchestrator",
            "runtime": "claude",
            "task": "Test task for issue 325",
            "notify": False,
            "recurring": True,
        }
        response = client.post("/api/v1/scheduler/jobs", json=payload, headers=auth_headers)
        assert response.status_code == 200
        return response.json()["result"]["id"]

    def test_fallback_runtime_persists_after_update_and_reopen(self, client, auth_headers):
        """Simulate the exact reproduce steps from issue #325.

        Steps:
        1. Open existing scheduled task (create one without fallback)
        2. Set fallback_runtime to 'copilot'
        3. Save (PUT)
        4. Navigate away and reopen (GET)
        Expected: fallback_runtime == 'copilot', not None
        """
        job_id = self._create_base_job(client, auth_headers, "Issue 325 Regression")

        # Step 2 & 3: Set fallback_runtime and save
        update = {"fallback_runtime": "copilot"}
        resp = client.put(f"/api/v1/scheduler/jobs/{job_id}", json=update, headers=auth_headers)
        assert resp.status_code == 200, f"PUT failed: {resp.text}"
        assert resp.json()["result"]["fallback_runtime"] == "copilot"

        # Step 4: Reopen — GET the job and verify fallback_runtime persisted
        resp = client.get(f"/api/v1/scheduler/jobs/{job_id}", headers=auth_headers)
        assert resp.status_code == 200
        job = resp.json()["result"]
        assert job["fallback_runtime"] == "copilot", (
            f"Bug #325: fallback_runtime reverted to {job.get('fallback_runtime')!r} after save+reopen"
        )

    def test_fallback_model_persists_after_update_and_reopen(self, client, auth_headers):
        """Verify fallback_model also persists correctly alongside fallback_runtime."""
        job_id = self._create_base_job(client, auth_headers, "Issue 325 Model Regression")

        update = {"fallback_runtime": "copilot", "fallback_model": "gpt-5.4-mini"}
        resp = client.put(f"/api/v1/scheduler/jobs/{job_id}", json=update, headers=auth_headers)
        assert resp.status_code == 200

        resp = client.get(f"/api/v1/scheduler/jobs/{job_id}", headers=auth_headers)
        assert resp.status_code == 200
        job = resp.json()["result"]
        assert job["fallback_runtime"] == "copilot", (
            f"Bug #325: fallback_runtime reverted to {job.get('fallback_runtime')!r}"
        )
        assert job["fallback_model"] == "gpt-5.4-mini", (
            f"Bug #325: fallback_model reverted to {job.get('fallback_model')!r}"
        )

    def test_fallback_runtime_survives_multiple_updates(self, client, auth_headers):
        """Verify fallback_runtime is not lost when other fields are updated later."""
        job_id = self._create_base_job(client, auth_headers, "Issue 325 Multi-Update")

        # Set fallback_runtime
        resp = client.put(
            f"/api/v1/scheduler/jobs/{job_id}",
            json={"fallback_runtime": "wee"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # Update an unrelated field (task prompt) — simulates saving other edits
        resp = client.put(
            f"/api/v1/scheduler/jobs/{job_id}",
            json={"task": "Updated task prompt"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # Verify fallback_runtime is still present
        resp = client.get(f"/api/v1/scheduler/jobs/{job_id}", headers=auth_headers)
        assert resp.status_code == 200
        job = resp.json()["result"]
        assert job["fallback_runtime"] == "wee", (
            f"Bug #325: fallback_runtime lost after unrelated update, got {job.get('fallback_runtime')!r}"
        )

    def test_create_with_fallback_runtime_persists(self, client, auth_headers):
        """Verify fallback_runtime set at creation time persists on GET."""
        payload = {
            "name": "Issue 325 Create Test",
            "schedule": "every day at 11am",
            "agent": "orchestrator",
            "runtime": "claude",
            "fallback_runtime": "copilot",
            "fallback_model": "gpt-5.4-mini",
            "task": "Test with fallback at creation",
            "notify": False,
            "recurring": True,
        }
        resp = client.post("/api/v1/scheduler/jobs", json=payload, headers=auth_headers)
        assert resp.status_code == 200
        job_id = resp.json()["result"]["id"]

        resp = client.get(f"/api/v1/scheduler/jobs/{job_id}", headers=auth_headers)
        assert resp.status_code == 200
        job = resp.json()["result"]
        assert job["fallback_runtime"] == "copilot"
        assert job["fallback_model"] == "gpt-5.4-mini"

    def test_issue_499_creating_jobs_does_not_leak_into_the_shared_jobs_file(
        self, client, auth_headers
    ):
        """Regression for #499/#500/#501/#504: every job this suite creates
        must land only in this test's isolated SCHEDULER_JOBS_FILE, never in
        the real .task-scheduler/jobs.json shared by dev/prod. Before the
        `client` fixture set SCHEDULER_JOBS_FILE, every run of this suite
        (daily in CI, or any local pytest run) added a new, permanent,
        recurring job to that shared file -- 222 such jobs had accumulated in
        this checkout alone by the time this test was written.
        """
        shared_jobs_file = (
            Path(__file__).resolve().parent.parent / ".task-scheduler" / "jobs.json"
        )
        before = shared_jobs_file.read_text() if shared_jobs_file.exists() else None

        self._create_base_job(client, auth_headers, "Issue 499 Isolation Check")

        after = shared_jobs_file.read_text() if shared_jobs_file.exists() else None
        assert after == before, (
            "Creating a job in this suite modified the shared jobs.json -- "
            "SCHEDULER_JOBS_FILE isolation regressed (see #499/#500/#501/#504)"
        )

        isolated_jobs_file = Path(os.environ["SCHEDULER_JOBS_FILE"])
        assert "Issue 499 Isolation Check" in isolated_jobs_file.read_text(), (
            "The job should still have been created -- just isolated to this "
            "test's own tmp_path file, not skipped entirely"
        )
