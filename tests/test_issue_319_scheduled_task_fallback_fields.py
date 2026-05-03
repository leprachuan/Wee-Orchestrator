"""Regression test for issue #319: Scheduled task fallback runtime and model persistence."""

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


class TestSchedulerFallbackFields:
    """Tests for issue #319: Fallback field persistence."""

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

    def test_scheduled_task_fallback_fields_persist_on_create(self, client, auth_headers):
        """Test that fallback_runtime and fallback_model persist when creating a scheduled task."""
        payload = {
            "name": "Test Task with Fallback",
            "schedule": "every day at 2pm",
            "agent": "orchestrator",
            "runtime": "claude",
            "model": "claude-haiku-4.5",
            "fallback_runtime": "copilot",
            "fallback_model": "gpt-5.4-mini",
            "task": "Test task",
            "notify": False,
            "recurring": True,
        }
        
        # Create the task
        response = client.post("/api/v1/scheduler/jobs", json=payload, headers=auth_headers)
        assert response.status_code == 200, f"Failed to create task: {response.text}"
        
        result = response.json()
        assert result["success"]
        assert result["result"]["fallback_runtime"] == "copilot"
        assert result["result"]["fallback_model"] == "gpt-5.4-mini"
        
        job_id = result["result"]["id"]
        
        # Verify the task is persisted correctly by retrieving it
        response = client.get(f"/api/v1/scheduler/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200
        
        result = response.json()
        assert result["success"]
        assert result["result"]["fallback_runtime"] == "copilot", (
            f"Expected fallback_runtime to be 'copilot', got {result['result'].get('fallback_runtime')}"
        )
        assert result["result"]["fallback_model"] == "gpt-5.4-mini", (
            f"Expected fallback_model to be 'gpt-5.4-mini', got {result['result'].get('fallback_model')}"
        )

    def test_scheduled_task_fallback_fields_persist_on_update(self, client, auth_headers):
        """Test that fallback_runtime and fallback_model persist when updating a scheduled task."""
        # Create initial task without fallback fields
        payload = {
            "name": "Test Task Update Fallback",
            "schedule": "every day at 3pm",
            "agent": "orchestrator",
            "runtime": "claude",
            "model": "claude-haiku-4.5",
            "task": "Original task",
            "notify": False,
            "recurring": True,
        }
        
        response = client.post("/api/v1/scheduler/jobs", json=payload, headers=auth_headers)
        assert response.status_code == 200
        job_id = response.json()["result"]["id"]
        
        # Update the task to add fallback fields
        update_payload = {
            "fallback_runtime": "copilot",
            "fallback_model": "gpt-5.2",
        }
        
        response = client.put(f"/api/v1/scheduler/jobs/{job_id}", json=update_payload, headers=auth_headers)
        assert response.status_code == 200, f"Failed to update task: {response.text}"
        
        result = response.json()
        assert result["success"]
        assert result["result"]["fallback_runtime"] == "copilot"
        assert result["result"]["fallback_model"] == "gpt-5.2"
        
        # Verify the fields persist after retrieval
        response = client.get(f"/api/v1/scheduler/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200
        
        result = response.json()
        assert result["success"]
        assert result["result"]["fallback_runtime"] == "copilot"
        assert result["result"]["fallback_model"] == "gpt-5.2"

    def test_scheduled_task_fallback_fields_optional(self, client, auth_headers):
        """Test that fallback_runtime and fallback_model are optional."""
        payload = {
            "name": "Test Task Optional Fallback",
            "schedule": "every day at 5pm",
            "agent": "orchestrator",
            "runtime": "claude",
            "model": "claude-haiku-4.5",
            "task": "Test without fallback",
            "notify": False,
            "recurring": True,
        }
        
        response = client.post("/api/v1/scheduler/jobs", json=payload, headers=auth_headers)
        assert response.status_code == 200
        
        result = response.json()
        assert result["success"]
        job_id = result["result"]["id"]
        
        # Verify defaults are None when not specified
        assert result["result"].get("fallback_runtime") is None
        assert result["result"].get("fallback_model") is None
        
        # Verify retrieval also has None values
        response = client.get(f"/api/v1/scheduler/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200
        result = response.json()
        assert result["success"]
        assert result["result"].get("fallback_runtime") is None
        assert result["result"].get("fallback_model") is None
