#!/usr/bin/env python3
"""wee-qa scheduled gate: Reviews dev branch before merging to main (prod).

Runs on a schedule (e.g., daily). Checks dev branch commits since last release,
runs full test suite, and gates merge to main.
"""

import subprocess, sys, json
from pathlib import Path
from urllib import request
import ssl, hashlib, hmac

REPO = "leprachuan/Wee-Orchestrator"
ENV_PATH = Path("/opt/n8n-copilot-shim/.env")
BACKGROUND_TASKS_URL = "https://127.0.0.1:8000/api/v1/background-tasks"
AGENTS_CONFIG_PATH = Path("/opt/n8n-copilot-shim/agents.json")
USER_IDENTITY = "8193231291"
AUTH_CHANNEL = "telegram"

def _load_api_key():
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                if line.startswith("API_KEY="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("API_KEY not found in .env")

def _load_agents_config():
    with open(AGENTS_CONFIG_PATH) as f:
        return json.load(f)

def get_agent_dispatch_config(agent_name: str) -> dict:
    agents = _load_agents_config()
    agent = agents.get(agent_name, {})
    return {
        "runtime": agent.get("primary_runtime", "copilot"),
        "model": agent.get("primary_model", "auto"),
        "timeout": agent.get("timeout", 7200),  # 2hr for full QA
        "permission_mode": agent.get("permission_mode", "restricted"),
    }

def dispatch_via_api(agent: str, prompt: str, cfg: dict) -> str:
    api_key = _load_api_key()
    resolved_model = None if (cfg.get("model") or "").lower() in ("auto", "") else cfg.get("model")
    body = {
        "prompt": prompt,
        "agent": agent,
        "runtime": cfg["runtime"],
        "timeout": cfg.get("timeout", 7200),
        "notify": False,
    }
    if resolved_model:
        body["model"] = resolved_model
    payload_json = json.dumps(body, sort_keys=True)
    signature = hmac.new(api_key.encode(), payload_json.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer shared_{api_key}",
        "X-User-Identity": USER_IDENTITY,
        "X-Auth-Channel": AUTH_CHANNEL,
        "X-Wee-Executor-Signature": signature,
    }
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    req = request.Request(BACKGROUND_TASKS_URL, data=json.dumps(body).encode(), headers=headers, method="POST")
    with request.urlopen(req, context=ssl_ctx, timeout=30) as resp:
        result = json.loads(resp.read())
        task_id = result.get("task_id", "")
        if not task_id:
            raise RuntimeError(f"No task_id in response: {result}")
        return task_id

def main():
    """Dispatch wee-qa to gate dev→main release."""
    cfg = get_agent_dispatch_config("wee-qa")
    prompt = f"""Review dev branch and gate merge to main (production).

Repository: {REPO}

Your task:
1. Check dev branch commits since last main release
2. Run full test suite on dev
3. Check code quality (flake8, black, mypy)
4. Review for any obvious bugs or issues

If all clear: 
  - Merge dev→main via PR
  - Post comment: "VERDICT: APPROVED — ready for production"
  
If issues found:
  - Post comment: "VERDICT: BLOCKED — fix these issues before releasing"
  - List specific blockers

This is the dev→prod gate. Be thorough but trust wee-dev's work quality."""
    
    task_id = dispatch_via_api("wee-qa", prompt, cfg)
    print(f"Dispatched wee-qa scheduled gate task: {task_id}")

if __name__ == "__main__":
    main()
