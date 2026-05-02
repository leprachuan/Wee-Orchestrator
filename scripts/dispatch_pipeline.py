#!/usr/bin/env python3
"""Wee-Dev dispatcher - picks up issues, no QA pipeline.

wee-dev: GitHub issue → dev branch → close issue
wee-qa: Scheduled gate for dev→main (separate process)
"""

import argparse, json, os, ssl, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import request

REPO = "leprachuan/Wee-Orchestrator"
OWNER_LOGIN = "leprachuan"
PIPELINE_STATE_PATH = Path("/opt/wee-dev/pipeline_state.json")
ENV_PATH = Path("/opt/n8n-copilot-shim/.env")
BACKGROUND_TASKS_URL = "https://127.0.0.1:8000/api/v1/background-tasks"
AGENTS_CONFIG_PATH = Path("/opt/n8n-copilot-shim/agents.json")
USER_IDENTITY = "8193231291"
AUTH_CHANNEL = "telegram"
RUNNING_STATUSES = {"created", "queued", "pending", "running", "in_progress"}
REQUIRED_LABELS = {"wee-dev": "0075ca", "wee-dev:in-progress": "e4e669"}
STALL_TIMEOUT_MINUTES = 30
DRY_RUN = False

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] {msg}")

def load_state() -> dict:
    if PIPELINE_STATE_PATH.exists():
        with open(PIPELINE_STATE_PATH) as f:
            return json.load(f)
    return {}

def save_state(state: dict) -> None:
    PIPELINE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PIPELINE_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

def get_issue_state(state: dict, issue_number: int) -> dict:
    key = str(issue_number)
    if key not in state:
        state[key] = {}
    return state[key]

def set_issue_field(state: dict, issue_number: int, field: str, value) -> None:
    get_issue_state(state, issue_number)[field] = value
    save_state(state)

def _load_api_key() -> str:
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                if line.startswith("API_KEY="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("API_KEY not found in .env")

def minutes_since(iso_time: str) -> float:
    if not iso_time:
        return None
    try:
        past = datetime.fromisoformat(iso_time)
        now = datetime.now(timezone.utc) if past.tzinfo else datetime.now()
        return (now - past).total_seconds() / 60
    except:
        return None

def get_open_wee_dev_issues() -> list:
    cmd = ["gh", "issue", "list", "--repo", REPO, "--label", "wee-dev", "--state", "open", "--json", "number,title,body,author,labels"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)

def get_issue_status(item: dict) -> str:
    labels = [l["name"] for l in item.get("labels", [])]
    return "in-progress" if "wee-dev:in-progress" in labels else "queued"

def add_label(issue_number: int, label: str) -> None:
    subprocess.run(["gh", "issue", "edit", str(issue_number), "--repo", REPO, "--add-label", label], check=True)

def remove_label(issue_number: int, label: str) -> None:
    subprocess.run(["gh", "issue", "edit", str(issue_number), "--repo", REPO, "--remove-label", label], check=True)

def close_issue(issue_number: int) -> None:
    subprocess.run(["gh", "issue", "close", str(issue_number), "--repo", REPO], check=True)

def ensure_labels_exist() -> None:
    for label, color in REQUIRED_LABELS.items():
        subprocess.run(["gh", "label", "create", label, "--repo", REPO, "--color", color, "--force"], capture_output=True)

def _load_agents_config() -> dict:
    with open(AGENTS_CONFIG_PATH) as f:
        return json.load(f)

def get_agent_dispatch_config(agent_name: str) -> dict:
    agents = _load_agents_config()
    agent = agents.get(agent_name, {})
    return {
        "runtime": agent.get("primary_runtime", "copilot"),
        "model": agent.get("primary_model", "auto"),
        "timeout": agent.get("timeout", 3600),
        "permission_mode": agent.get("permission_mode", "restricted"),
        "yolo": agent.get("yolo", False),
        "fallback_runtime": agent.get("fallback_runtime"),
        "fallback_model": agent.get("fallback_model"),
    }

def dispatch_via_api(agent: str, prompt: str, cfg: dict) -> str:
    import hashlib, hmac
    api_key = _load_api_key()
    resolved_model = None if (cfg.get("model") or "").lower() in ("auto", "") else cfg.get("model")
    resolved_fallback_model = None if (cfg.get("fallback_model") or "").lower() in ("auto", "") else cfg.get("fallback_model")
    body = {"prompt": prompt, "agent": agent, "runtime": cfg["runtime"], "timeout": cfg.get("timeout", 3600), "notify": False}
    if resolved_model:
        body["model"] = resolved_model
    if cfg.get("permission_mode"):
        body["permission_mode"] = cfg["permission_mode"]
    if cfg.get("yolo"):
        body["yolo"] = cfg["yolo"]
    if cfg.get("fallback_runtime"):
        body["fallback_runtime"] = cfg["fallback_runtime"]
    if resolved_fallback_model:
        body["fallback_model"] = resolved_fallback_model
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

def is_task_running(task_id: str) -> bool:
    try:
        api_key = _load_api_key()
        url = f"{BACKGROUND_TASKS_URL}/{task_id}"
        headers = {"Authorization": f"Bearer shared_{api_key}"}
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        req = request.Request(url, headers=headers)
        with request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get("status", "").lower() in RUNNING_STATUSES
    except:
        return False

def build_wee_dev_prompt(item: dict) -> str:
    return (f"Work on GitHub issue #{item['number']} in {REPO}: {item['title']}.\n\n"
        f"Issue body:\n{item['body']}\n\n"
        "## Your task:\n"
        f"1. Read: gh issue view {item['number']} --repo {REPO} --comments\n"
        "2. Develop on dev host (192.168.1.100): /opt/n8n-copilot-shim-dev/\n"
        "3. Work on dev branch (git checkout dev && git pull)\n"
        "4. Test your changes locally\n"
        "5. Commit & push to dev branch\n"
        "6. Leave notes on GitHub (commit SHA, test results)\n"
        "7. Remove 'wee-dev' label when done\n"
        "8. Close the issue\n\n"
        "⚠️ Never put secrets/keys in GitHub. Work on ONE issue at a time.")

def dispatch_wee_dev(item: dict, state: dict) -> None:
    cfg = get_agent_dispatch_config("wee-dev")
    prompt = build_wee_dev_prompt(item)
    if DRY_RUN:
        log(f"[dry-run] Would dispatch wee-dev for #{item['number']}")
        return
    task_id = dispatch_via_api("wee-dev", prompt, cfg)
    log(f"Dispatched wee-dev task_id={task_id} for #{item['number']}: {item['title']}")
    set_issue_field(state, item["number"], "wee_dev_task_id", task_id)
    set_issue_field(state, item["number"], "wee_dev_dispatched_at", datetime.now(timezone.utc).isoformat())

def run_pipeline() -> None:
    state = load_state()
    ensure_labels_exist()
    items = get_open_wee_dev_issues()
    log(f"Found {len(items)} open wee-dev issue(s)")
    in_progress = [i for i in items if get_issue_status(i) == "in-progress"]
    queued = [i for i in items if get_issue_status(i) == "queued"]
    log(f"  in-progress: {', '.join(f'#{i['number']}' for i in in_progress) or '(none)'}")
    log(f"  queued: {', '.join(f'#{i['number']}' for i in queued) or '(none)'}")
    
    # Check in-progress for stall
    if in_progress:
        item = in_progress[0]
        issue_state = get_issue_state(state, item["number"])
        task_id = issue_state.get("wee_dev_task_id")
        if task_id and is_task_running(task_id):
            log(f"wee-dev running task={task_id} for #{item['number']} — waiting")
            return
        dispatched_at = issue_state.get("wee_dev_dispatched_at")
        mins = minutes_since(dispatched_at)
        if mins is not None and mins < STALL_TIMEOUT_MINUTES:
            log(f"wee-dev task for #{item['number']} ended within {mins:.1f}min — may still be processing")
            return
        log(f"Re-dispatching stalled #{item['number']} (no running task after {mins:.1f}min)")
        add_label(item["number"], "wee-dev:in-progress")
        try:
            dispatch_wee_dev(item, state)
        except Exception as exc:
            log(f"ERROR: Failed to re-dispatch: {exc}")
            remove_label(item["number"], "wee-dev:in-progress")
        return
    
    # Dispatch next queued
    if queued:
        item = queued[0]
        log(f"Dispatching wee-dev for #{item['number']}: {item['title']}")
        add_label(item["number"], "wee-dev:in-progress")
        try:
            dispatch_wee_dev(item, state)
        except Exception as exc:
            log(f"ERROR: Failed to dispatch: {exc}")
            remove_label(item["number"], "wee-dev:in-progress")
        return
    
    log("No wee-dev work to do.")

def main() -> None:
    parser = argparse.ArgumentParser(description="Wee-Dev dispatcher")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    global DRY_RUN
    DRY_RUN = args.dry_run
    run_pipeline()

if __name__ == "__main__":
    main()
