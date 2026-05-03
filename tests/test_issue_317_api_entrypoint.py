"""Regression test for Issue #317.

Bug: production `agent-manager-api.service` invokes
`python3 agent_manager.py --api`, but the deployed `agent_manager.py` on
`main` lost its `__main__` entrypoint during a bad merge. After any
restart, the process exited immediately with status 0 and port 8000 never
bound.

Fix: keep a module-level CLI entrypoint that routes `--api` to
`start_api_server()`.
"""

from pathlib import Path


def test_issue_317_agent_manager_has_api_entrypoint():
    source = Path("/opt/n8n-copilot-shim/agent_manager.py").read_text()

    assert 'if __name__ == "__main__":' in source
    assert 'if "--api" in sys.argv:' in source
    assert "start_api_server()" in source
