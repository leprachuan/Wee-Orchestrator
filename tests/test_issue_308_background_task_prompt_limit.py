"""
Regression test for Issue #308:
Bug: BackgroundTaskRequest.validate_prompt 10,000-char limit blocks dispatcher

The dispatcher builds prompts that include GitHub issue bodies + comments,
often exceeding 10,000 characters. This test ensures:
1. Interactive endpoints (QueryRequest) enforce 10,000-char limit
2. Background tasks (BackgroundTaskRequest) allow up to 200,000 chars
3. Prompts exceeding 200,000 chars are rejected
"""

import pytest
import subprocess
import json
import sys


class TestPromptLimits:
    """Test prompt character limits for different request types."""

    def test_query_endpoint_rejects_10001_chars(self):
        """Interactive /api/v1/query endpoint should reject >10,000 chars."""
        prompt = "x" * 10001
        payload = {
            "prompt": prompt,
            "agent": "fosterbot"
        }
        
        # This should return 422 - Unprocessable Entity
        result = subprocess.run(
            ['curl', '-s', '-w', '\n%{http_code}', '-X', 'POST', 
             '-H', 'Content-Type: application/json',
             '-H', 'Authorization: Bearer shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU',
             '-d', json.dumps(payload),
             'https://127.0.0.1:8001/api/v1/query'],
            capture_output=True, text=True, cwd='/opt/n8n-copilot-shim-dev'
        )
        
        lines = result.stdout.strip().split('\n')
        status_code = lines[-1]
        assert status_code == '422', f"Expected 422, got {status_code}: {result.stdout}"

    def test_background_task_accepts_15000_chars(self):
        """Background task endpoint should accept >10,000 chars."""
        prompt = "x" * 15000
        payload = {
            "prompt": prompt,
            "agent": "fosterbot"
        }
        
        # This should return 202 - Accepted (or at least not 422)
        result = subprocess.run(
            ['curl', '-s', '-w', '\n%{http_code}', '-X', 'POST',
             '-H', 'Content-Type: application/json',
             '-H', 'Authorization: Bearer shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU',
             '-d', json.dumps(payload),
             'https://127.0.0.1:8001/api/v1/background-tasks'],
            capture_output=True, text=True, cwd='/opt/n8n-copilot-shim-dev'
        )
        
        lines = result.stdout.strip().split('\n')
        status_code = lines[-1]
        # Should be 202 Accepted or 200 OK, not 422
        assert status_code in ['200', '202'], f"Expected 200/202, got {status_code}: {result.stdout}"

    def test_background_task_accepts_200000_chars(self):
        """Background task endpoint should accept up to 200,000 chars."""
        prompt = "x" * 200000
        payload = {
            "prompt": prompt,
            "agent": "fosterbot"
        }
        
        result = subprocess.run(
            ['curl', '-s', '-w', '\n%{http_code}', '-X', 'POST',
             '-H', 'Content-Type: application/json',
             '-H', 'Authorization: Bearer shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU',
             '-d', json.dumps(payload),
             'https://127.0.0.1:8001/api/v1/background-tasks'],
            capture_output=True, text=True, cwd='/opt/n8n-copilot-shim-dev'
        )
        
        lines = result.stdout.strip().split('\n')
        status_code = lines[-1]
        assert status_code in ['200', '202'], f"Expected 200/202, got {status_code}: {result.stdout}"

    def test_background_task_rejects_200001_chars(self):
        """Background task endpoint should reject >200,000 chars."""
        prompt = "x" * 200001
        payload = {
            "prompt": prompt,
            "agent": "fosterbot"
        }
        
        result = subprocess.run(
            ['curl', '-s', '-w', '\n%{http_code}', '-X', 'POST',
             '-H', 'Content-Type: application/json',
             '-H', 'Authorization: Bearer shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU',
             '-d', json.dumps(payload),
             'https://127.0.0.1:8001/api/v1/background-tasks'],
            capture_output=True, text=True, cwd='/opt/n8n-copilot-shim-dev'
        )
        
        lines = result.stdout.strip().split('\n')
        status_code = lines[-1]
        assert status_code == '422', f"Expected 422, got {status_code}: {result.stdout}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
