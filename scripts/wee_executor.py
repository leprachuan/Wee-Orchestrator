#!/usr/bin/env python3
"""
wee_executor.py — Unified executor for Wee agents to safely perform privileged operations.

Provides a secure, audited CLI for agents to execute privileged operations
(background task creation, etc.) without direct access to API tokens or
internal infrastructure details.

Usage:
    python3 scripts/wee_executor.py --capability create_background_task \
        --args '{"agent": "research", "prompt": "look up X"}'

    python3 scripts/wee_executor.py --list-capabilities

Session Detection (priority order):
    1. WEE_SESSION_ID env var
    2. SESSION_ID env var
    3. Most recent active session from .task-scheduler/sessions.json

Mode Detection:
    - background: WEE_TASK_ID env var is set
    - api: no session detected
    - interactive: default (session detected, not background)

Exit codes:
    0 — success
    1 — error (details in JSON output)
"""

import argparse
import hashlib
import hmac
import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Constants ──────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "wee_executor.log"
RATE_LIMIT_FILE = LOG_DIR / ".wee_executor_rate_limits.json"
ENV_FILE = BASE_DIR / ".env"
DEFAULT_AGENTS_JSON = BASE_DIR / "agents.json"
SESSIONS_JSON = BASE_DIR / ".task-scheduler" / "sessions.json"

MAX_RATE_PER_MINUTE = 10
TASK_VERIFY_TIMEOUT = 3

MODE_INTERACTIVE = "interactive"
MODE_BACKGROUND = "background"
MODE_API = "api"


# ── Logging ────────────────────────────────────────────────────────────


def _setup_logging() -> logging.Logger:
    """Configure audit logger: debug to file, warnings to stderr."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _logger = logging.getLogger("wee_executor")
    _logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    _logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    _logger.addHandler(sh)

    return _logger


logger = _setup_logging()


# ── Config Loading ─────────────────────────────────────────────────────


def _load_env_file() -> Dict[str, str]:
    """Parse .env file for key=value pairs."""
    env: Dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                val = v.strip()
                # Strip surrounding quotes
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                env[k.strip()] = val
    return env


def _get_api_key() -> str:
    """Retrieve API shared key from env or .env — never logged."""
    key = os.environ.get("API_SHARED_KEY", "")
    if not key:
        key = _load_env_file().get("API_SHARED_KEY", "")
    return key


def _get_api_url() -> str:
    """Determine API base URL from env or auto-detect."""
    explicit = os.environ.get("WEE_API_URL")
    if explicit:
        return explicit.rstrip("/")

    port = os.environ.get("API_PORT", "")
    if not port:
        port = "8001" if "dev" in str(BASE_DIR) else "8000"

    return f"https://127.0.0.1:{port}"


def _load_agents_json() -> Dict:
    """Load agents.json configuration."""
    config_path = os.environ.get("AGENT_CONFIG_FILE", str(DEFAULT_AGENTS_JSON))
    path = Path(config_path)
    if not path.exists():
        return {"agents": []}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"agents": []}


def _get_valid_agent_names() -> List[str]:
    """Return list of valid agent names from agents.json."""
    agents = _load_agents_json()
    return [a["name"] for a in agents.get("agents", [])]


# ── Session Detection ──────────────────────────────────────────────────


def detect_session() -> Tuple[Optional[str], str, str]:
    """Detect current session ID, mode, and runtime.

    Returns:
        (session_id, session_mode, runtime)
    """
    session_id = os.environ.get("WEE_SESSION_ID") or os.environ.get("SESSION_ID")

    if not session_id:
        session_id = _detect_from_sessions_json()

    mode = _detect_mode(session_id)
    runtime = os.environ.get("WEE_RUNTIME", "copilot")

    return session_id, mode, runtime


def _detect_from_sessions_json() -> Optional[str]:
    """Find most recent active session from sessions.json."""
    if not SESSIONS_JSON.exists():
        return None
    try:
        data = json.loads(SESSIONS_JSON.read_text())
        if not data:
            return None
        active = [
            (sid, info)
            for sid, info in data.items()
            if isinstance(info, dict) and info.get("identity")
        ]
        if not active:
            return None
        latest = max(active, key=lambda x: x[1].get("created_at", 0))
        return latest[0]
    except Exception:
        return None


def _detect_mode(session_id: Optional[str]) -> str:
    """Determine session mode from env context."""
    if os.environ.get("WEE_TASK_ID"):
        return MODE_BACKGROUND
    if not session_id:
        return MODE_API
    return MODE_INTERACTIVE


# ── Security ───────────────────────────────────────────────────────────


def _validate_agent(agent_name: str) -> bool:
    """Check agent name exists in agents.json."""
    return agent_name in _get_valid_agent_names()


def _hmac_sign(payload: str, key: str) -> str:
    """HMAC-SHA256 signature of payload for integrity verification."""
    return hmac.new(
        key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _check_rate_limit(session_id: str) -> bool:
    """Enforce rate limit of MAX_RATE_PER_MINUTE calls per session."""
    now = time.time()
    limits: Dict[str, list] = {}

    try:
        if RATE_LIMIT_FILE.exists():
            limits = json.loads(RATE_LIMIT_FILE.read_text())
    except Exception:
        limits = {}

    calls = [t for t in limits.get(session_id, []) if now - t < 60]

    if len(calls) >= MAX_RATE_PER_MINUTE:
        return False

    calls.append(now)
    limits[session_id] = calls

    try:
        RATE_LIMIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RATE_LIMIT_FILE.write_text(json.dumps(limits))
    except Exception:
        pass

    return True


# ── HTTP Client ────────────────────────────────────────────────────────


def _api_request(
    method: str,
    path: str,
    body: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    timeout: int = 10,
) -> Dict:
    """Make an authenticated API request to the Wee Orchestrator."""
    url = f"{_get_api_url()}{path}"
    api_key = _get_api_key()

    if not api_key:
        return {"error": "API_SHARED_KEY not configured", "code": "AUTH_MISSING"}

    req_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer shared_{api_key}",
    }
    if headers:
        req_headers.update(headers)

    data = json.dumps(body).encode("utf-8") if body else None

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("API error %d on %s %s", e.code, method, path)
        return {"error": f"HTTP {e.code}: {body_text}", "code": f"HTTP_{e.code}"}
    except urllib.error.URLError as e:
        logger.error("Connection failed: %s", e.reason)
        return {"error": f"Connection failed: {e.reason}", "code": "CONNECTION_FAILED"}
    except Exception as e:
        logger.error("Request error: %s", e)
        return {"error": str(e), "code": "UNKNOWN_ERROR"}


# ── Resolve Identity ───────────────────────────────────────────────────


def _resolve_identity() -> Tuple[str, str]:
    """Resolve user identity from env vars or sessions.json."""
    identity = os.environ.get("WEE_IDENTITY", "")
    channel = os.environ.get("WEE_CHANNEL", "api")
    if identity:
        return identity, channel

    try:
        if SESSIONS_JSON.exists():
            data = json.loads(SESSIONS_JSON.read_text())
            active = [
                s
                for s in data.values()
                if isinstance(s, dict) and s.get("identity")
            ]
            if active:
                latest = max(active, key=lambda s: s.get("created_at", 0))
                return latest.get("identity", ""), latest.get("channel", "api")
    except Exception:
        pass

    return "", "api"


# ── Capability: create_background_task ─────────────────────────────────


def cap_create_background_task(
    args: Dict, session_id: Optional[str], mode: str
) -> Dict:
    """Create a background task via the orchestrator API.

    Args (in args dict):
        agent:   Target agent name (required)
        prompt:  Task prompt text (required)
        runtime: Execution runtime (default: copilot)
        model:   AI model to use (default: claude-haiku-4.5)
        timeout: Max execution time in seconds (default: 900)
        notify:  Send completion notification (default: true)

    Returns:
        {task_id, status, agent, monitor_url} on success
        {error, code} on failure
    """
    if mode == MODE_API:
        return {
            "error": "create_background_task not available in api mode",
            "code": "MODE_RESTRICTED",
        }

    agent = args.get("agent")
    prompt = args.get("prompt")
    if not agent or not prompt:
        return {
            "error": "Missing required fields: agent, prompt",
            "code": "MISSING_FIELDS",
        }

    if not _validate_agent(agent):
        valid = _get_valid_agent_names()
        return {
            "error": f"Unknown agent '{agent}'. Valid: {', '.join(valid)}",
            "code": "INVALID_AGENT",
        }

    rate_key = session_id or "anonymous"
    if not _check_rate_limit(rate_key):
        return {
            "error": f"Rate limit exceeded ({MAX_RATE_PER_MINUTE}/min)",
            "code": "RATE_LIMITED",
        }

    body = {
        "prompt": prompt,
        "agent": agent,
        "runtime": args.get("runtime", "copilot"),
        "model": args.get("model", "claude-haiku-4.5"),
        "timeout": args.get("timeout", 900),
        "notify": args.get("notify", True),
    }

    api_key = _get_api_key()
    payload_json = json.dumps(body, sort_keys=True)
    signature = _hmac_sign(payload_json, api_key)

    identity, channel = _resolve_identity()

    extra_headers = {
        "X-User-Identity": identity,
        "X-Auth-Channel": channel,
        "X-Wee-Executor-Signature": signature,
    }

    logger.info(
        "Creating background task: agent=%s, runtime=%s, model=%s",
        agent,
        body["runtime"],
        body["model"],
    )

    result = _api_request(
        "POST", "/api/v1/background-tasks", body=body, headers=extra_headers
    )

    if "error" in result:
        logger.error("Failed to create task: %s", result["error"])
        return result

    task_id = result.get("task_id", "")

    if task_id:
        time.sleep(0.5)
        verify = _api_request("GET", f"/api/v1/background-tasks/{task_id}")
        status = verify.get("status", "unknown")
        if status in ("running", "queued"):
            api_url = _get_api_url()
            logger.info("Task %s verified: status=%s", task_id, status)
            return {
                "task_id": task_id,
                "status": status,
                "agent": agent,
                "monitor_url": f"{api_url}/api/v1/background-tasks/{task_id}",
            }
        else:
            logger.warning(
                "Task %s verification returned status=%s", task_id, status
            )

    return {
        "task_id": task_id,
        "status": result.get("status", "created"),
        "raw_response": result,
    }


# ── Capability Registry ────────────────────────────────────────────────

CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "create_background_task": {
        "handler": cap_create_background_task,
        "description": "Create a background task via the orchestrator API",
        "modes": [MODE_INTERACTIVE, MODE_BACKGROUND],
        "required_args": ["agent", "prompt"],
        "optional_args": ["runtime", "model", "timeout", "notify"],
        "example": {
            "agent": "research",
            "prompt": "Look up the latest Python 3.13 features",
            "runtime": "copilot",
            "model": "claude-haiku-4.5",
            "timeout": 600,
        },
    },
    # Phase B stubs:
    # "list_tasks": { ... },
    # "cancel_task": { ... },
    # "get_task_result": { ... },
}


def list_capabilities(mode: str = MODE_INTERACTIVE) -> List[Dict]:
    """List capabilities available in the given session mode."""
    result = []
    for name, cap in CAPABILITIES.items():
        if mode in cap["modes"]:
            result.append(
                {
                    "name": name,
                    "description": cap["description"],
                    "required_args": cap.get("required_args", []),
                    "optional_args": cap.get("optional_args", []),
                    "example": cap.get("example"),
                }
            )
    return result


# ── Main CLI ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wee Executor — Unified privileged operations for Wee agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Create a background task\n"
            "  python3 %(prog)s --capability create_background_task \\\n"
            '      --args \'{"agent": "research", "prompt": "look up X"}\'\n'
            "\n"
            "  # List available capabilities\n"
            "  python3 %(prog)s --list-capabilities\n"
            "\n"
            "  # With explicit session\n"
            "  WEE_SESSION_ID=abc123 python3 %(prog)s -c create_background_task \\\n"
            '      -a \'{"agent": "devops", "prompt": "check cluster health"}\'\n'
        ),
    )
    parser.add_argument("--capability", "-c", help="Capability to execute")
    parser.add_argument("--args", "-a", help="JSON arguments for the capability")
    parser.add_argument(
        "--list-capabilities", action="store_true", help="List available capabilities"
    )
    parser.add_argument("--session-id", help="Override session ID")
    parser.add_argument("--json", action="store_true", help="Force JSON output")

    parsed = parser.parse_args()

    session_id, mode, runtime = detect_session()
    if parsed.session_id:
        session_id = parsed.session_id

    logger.info("Session: id=%s, mode=%s, runtime=%s", session_id, mode, runtime)

    if parsed.list_capabilities:
        caps = list_capabilities(mode)
        if parsed.json:
            print(json.dumps(caps, indent=2))
        else:
            print(f"Available capabilities (mode={mode}):\n")
            for cap in caps:
                print(f"  {cap['name']}: {cap['description']}")
                req = ", ".join(cap["required_args"])
                opt = ", ".join(cap.get("optional_args", []))
                print(f"    Required: {req}")
                print(f"    Optional: {opt}")
                print()
        return

    if not parsed.capability:
        parser.print_help()
        sys.exit(1)

    cap_name = parsed.capability
    if cap_name not in CAPABILITIES:
        valid = list(CAPABILITIES.keys())
        print(
            json.dumps(
                {
                    "error": f"Unknown capability '{cap_name}'. Valid: {valid}",
                    "code": "UNKNOWN_CAPABILITY",
                }
            )
        )
        sys.exit(1)

    cap = CAPABILITIES[cap_name]

    if mode not in cap["modes"]:
        print(
            json.dumps(
                {
                    "error": (
                        f"Capability '{cap_name}' not available in {mode} mode. "
                        f"Allowed: {cap['modes']}"
                    ),
                    "code": "MODE_RESTRICTED",
                }
            )
        )
        sys.exit(1)

    try:
        cap_args = json.loads(parsed.args) if parsed.args else {}
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON args: {e}", "code": "INVALID_JSON"}))
        sys.exit(1)

    missing = [f for f in cap.get("required_args", []) if f not in cap_args]
    if missing:
        print(
            json.dumps(
                {
                    "error": f"Missing required arguments: {missing}",
                    "code": "MISSING_FIELDS",
                    "example": cap.get("example"),
                }
            )
        )
        sys.exit(1)

    result = cap["handler"](cap_args, session_id, mode)
    print(json.dumps(result, indent=2))

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        logger.exception("Unhandled error: %s", e)
        print(json.dumps({"error": str(e), "code": "UNHANDLED_EXCEPTION"}))
        sys.exit(1)
