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
    def client(self):
        app = create_api_app()
        return TestClient(app, raise_server_exceptions=False)

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
