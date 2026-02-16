# Agent Manager API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a FastAPI-based REST API to `agent_manager.py` with dual authentication (shared key for bots, pairing-based sessions for external clients).

**Architecture:** Hybrid mode in `agent_manager.py` -- `--api` flag starts FastAPI/uvicorn server, otherwise existing CLI `main()` runs. New `AuthManager` class handles pairing codes, session tokens, rate limiting. All in-memory with TTL cleanup.

**Tech Stack:** FastAPI, uvicorn, pydantic v2, python-dotenv, secrets (stdlib)

**Design Doc:** `docs/plans/2026-02-16-agent-manager-api-design.md`

---

### Task 1: Install Dependencies

**Files:**
- Create: `requirements.txt`

**Step 1: Create requirements.txt**

```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
pydantic>=2.5.0
python-multipart>=0.0.6
python-dotenv>=1.0.0
requests>=2.31.0
```

**Step 2: Install dependencies**

Run: `cd /opt/n8n-copilot-shim-dev && pip install -r requirements.txt`
Expected: All packages install successfully

**Step 3: Commit**

```bash
cd /opt/n8n-copilot-shim-dev
git add requirements.txt
git commit -m "feat: add FastAPI dependencies for API server"
```

---

### Task 2: Update .env.example and .env with API Config

**Files:**
- Modify: `/opt/n8n-copilot-shim-dev/.env.example`
- Modify: `/opt/n8n-copilot-shim-dev/.env`

**Step 1: Append API configuration to .env.example**

Add after existing content:

```bash
# --- API Server Configuration ---

# Environment identifier
APP_ENV=DEV  # DEV or PROD

# API Server
API_HOST=127.0.0.1
API_PORT=8001
API_SHARED_KEY=change_me_to_a_secure_random_string

# Pairing Code Settings
PAIRING_CODE_LENGTH=6
PAIRING_CODE_TTL=300

# Session Token Settings
SESSION_TOKEN_TTL=3600

# Rate Limiting
RATE_LIMIT_PAIRING_REQUESTS=3
RATE_LIMIT_PAIRING_WINDOW=900
RATE_LIMIT_EXECUTE_REQUESTS=60
RATE_LIMIT_EXECUTE_WINDOW=60
RATE_LIMIT_AUTH_FAILURES=5
RATE_LIMIT_AUTH_FAILURE_WINDOW=3600

# CORS (comma-separated origins, empty = disabled)
API_CORS_ORIGINS=

# Connector API Mode (set in connector .env files)
USE_API=false
API_URL=http://127.0.0.1:8001
```

**Step 2: Add matching values to .env (with a real dev shared key)**

Same as above but generate an actual key:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use the output as `API_SHARED_KEY` value in `.env`.

**Step 3: Commit**

```bash
git add .env.example
git commit -m "feat: add API server environment variables to .env.example"
```

Note: `.env` is gitignored -- do NOT commit it.

---

### Task 3: Write AuthManager Tests

**Files:**
- Create: `tests/test_api_auth.py`

**Step 1: Write the failing tests**

```python
"""Tests for AuthManager - pairing codes, session tokens, rate limiting."""
import time
import unittest
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAuthManager(unittest.TestCase):
    """Test AuthManager authentication logic."""

    def setUp(self):
        from agent_manager import AuthManager
        self.auth = AuthManager(
            shared_key="test_shared_key",
            pairing_code_length=6,
            pairing_code_ttl=300,
            session_token_ttl=3600,
        )

    def test_validate_shared_key_success(self):
        """Valid shared key returns True."""
        result = self.auth.validate_shared_key("shared_test_shared_key")
        self.assertTrue(result)

    def test_validate_shared_key_wrong_key(self):
        """Wrong shared key returns False."""
        result = self.auth.validate_shared_key("shared_wrong_key")
        self.assertFalse(result)

    def test_validate_shared_key_missing_prefix(self):
        """Token without shared_ prefix returns False."""
        result = self.auth.validate_shared_key("test_shared_key")
        self.assertFalse(result)

    def test_generate_pairing_code(self):
        """Generate pairing code returns numeric string of correct length."""
        code = self.auth.generate_pairing_code("user123", "telegram")
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_generate_pairing_code_stored(self):
        """Generated pairing code is stored with correct metadata."""
        code = self.auth.generate_pairing_code("user123", "telegram")
        self.assertIn(code, self.auth.pairing_codes)
        entry = self.auth.pairing_codes[code]
        self.assertEqual(entry["identity"], "user123")
        self.assertEqual(entry["channel"], "telegram")

    def test_verify_pairing_code_success(self):
        """Valid pairing code returns session token."""
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        self.assertIsNotNone(token)
        self.assertTrue(token.startswith("session_"))

    def test_verify_pairing_code_wrong_identity(self):
        """Pairing code with wrong identity returns None."""
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "wrong_user")
        self.assertIsNone(token)

    def test_verify_pairing_code_wrong_code(self):
        """Invalid pairing code returns None."""
        token = self.auth.verify_pairing_code("000000", "user123")
        self.assertIsNone(token)

    def test_verify_pairing_code_consumed(self):
        """Pairing code is consumed after successful verification."""
        code = self.auth.generate_pairing_code("user123", "telegram")
        self.auth.verify_pairing_code(code, "user123")
        token2 = self.auth.verify_pairing_code(code, "user123")
        self.assertIsNone(token2)

    def test_verify_pairing_code_expired(self):
        """Expired pairing code returns None."""
        code = self.auth.generate_pairing_code("user123", "telegram")
        # Manually expire it
        self.auth.pairing_codes[code]["expires_at"] = time.time() - 1
        token = self.auth.verify_pairing_code(code, "user123")
        self.assertIsNone(token)

    def test_validate_session_token_success(self):
        """Valid session token returns identity info."""
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        result = self.auth.validate_session_token(token)
        self.assertIsNotNone(result)
        self.assertEqual(result["identity"], "user123")
        self.assertEqual(result["channel"], "telegram")

    def test_validate_session_token_invalid(self):
        """Invalid session token returns None."""
        result = self.auth.validate_session_token("session_bogus")
        self.assertIsNone(result)

    def test_validate_session_token_expired(self):
        """Expired session token returns None."""
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        self.auth.session_tokens[token]["expires_at"] = time.time() - 1
        result = self.auth.validate_session_token(token)
        self.assertIsNone(result)

    def test_validate_session_token_updates_last_used(self):
        """Validating a session token updates last_used timestamp."""
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        before = self.auth.session_tokens[token]["last_used"]
        time.sleep(0.01)
        self.auth.validate_session_token(token)
        after = self.auth.session_tokens[token]["last_used"]
        self.assertGreater(after, before)

    def test_cleanup_expired(self):
        """Cleanup removes expired pairing codes and session tokens."""
        code = self.auth.generate_pairing_code("user123", "telegram")
        token = self.auth.verify_pairing_code(code, "user123")
        # Expire both
        # code is already consumed, make a new one and expire it
        code2 = self.auth.generate_pairing_code("user456", "webex")
        self.auth.pairing_codes[code2]["expires_at"] = time.time() - 1
        self.auth.session_tokens[token]["expires_at"] = time.time() - 1
        self.auth.cleanup_expired()
        self.assertNotIn(code2, self.auth.pairing_codes)
        self.assertNotIn(token, self.auth.session_tokens)


class TestRateLimiter(unittest.TestCase):
    """Test rate limiting logic."""

    def setUp(self):
        from agent_manager import RateLimiter
        self.limiter = RateLimiter()

    def test_allow_under_limit(self):
        """Requests under limit are allowed."""
        allowed = self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        self.assertTrue(allowed)

    def test_block_over_limit(self):
        """Requests over limit are blocked."""
        for _ in range(3):
            self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        allowed = self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        self.assertFalse(allowed)

    def test_different_ips_independent(self):
        """Rate limits are per-IP."""
        for _ in range(3):
            self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        allowed = self.limiter.check("5.6.7.8", "pairing", max_requests=3, window=900)
        self.assertTrue(allowed)

    def test_different_endpoints_independent(self):
        """Rate limits are per-endpoint."""
        for _ in range(3):
            self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        allowed = self.limiter.check("1.2.3.4", "execute", max_requests=60, window=60)
        self.assertTrue(allowed)

    def test_expired_entries_dont_count(self):
        """Old entries outside the window don't count toward limit."""
        # Add old entries manually
        self.limiter.records.setdefault("1.2.3.4", {}).setdefault("pairing", [])
        self.limiter.records["1.2.3.4"]["pairing"] = [time.time() - 1000, time.time() - 1000, time.time() - 1000]
        allowed = self.limiter.check("1.2.3.4", "pairing", max_requests=3, window=900)
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run tests to verify they fail**

Run: `cd /opt/n8n-copilot-shim-dev && python -m pytest tests/test_api_auth.py -v`
Expected: FAIL with `ImportError: cannot import name 'AuthManager'`

**Step 3: Commit**

```bash
git add tests/test_api_auth.py
git commit -m "test: add AuthManager and RateLimiter unit tests (red)"
```

---

### Task 4: Implement AuthManager and RateLimiter

**Files:**
- Modify: `agent_manager.py` (add classes after imports, before `SessionManager`)

**Step 1: Add AuthManager and RateLimiter classes**

Insert after the imports block (after line ~19, before `find_executable`), add:

```python
import secrets as _secrets
import threading


class RateLimiter:
    """In-memory per-IP rate limiter with sliding window."""

    def __init__(self):
        self.records: Dict[str, Dict[str, List[float]]] = {}
        self._lock = threading.Lock()

    def check(self, ip: str, endpoint: str, max_requests: int, window: int) -> bool:
        """Return True if request is allowed, False if rate limited."""
        now = time.time()
        with self._lock:
            ep_list = self.records.setdefault(ip, {}).setdefault(endpoint, [])
            # Prune entries outside window
            ep_list[:] = [t for t in ep_list if now - t < window]
            if len(ep_list) >= max_requests:
                return False
            ep_list.append(now)
            return True

    def cleanup(self):
        """Remove all empty entries."""
        with self._lock:
            for ip in list(self.records):
                for ep in list(self.records[ip]):
                    if not self.records[ip][ep]:
                        del self.records[ip][ep]
                if not self.records[ip]:
                    del self.records[ip]


class AuthManager:
    """Manages pairing codes, session tokens, and shared key validation."""

    def __init__(
        self,
        shared_key: str,
        pairing_code_length: int = 6,
        pairing_code_ttl: int = 300,
        session_token_ttl: int = 3600,
    ):
        self.shared_key = shared_key
        self.pairing_code_length = pairing_code_length
        self.pairing_code_ttl = pairing_code_ttl
        self.session_token_ttl = session_token_ttl
        self.pairing_codes: Dict[str, dict] = {}
        self.session_tokens: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def validate_shared_key(self, token: str) -> bool:
        """Validate a Bearer token as a shared key. Expects 'shared_<key>'."""
        if not token.startswith("shared_"):
            return False
        return token[7:] == self.shared_key

    def generate_pairing_code(self, identity: str, channel: str) -> str:
        """Generate a numeric pairing code and store it."""
        code = "".join(
            [str(_secrets.randbelow(10)) for _ in range(self.pairing_code_length)]
        )
        now = time.time()
        with self._lock:
            self.pairing_codes[code] = {
                "identity": identity,
                "channel": channel,
                "created_at": now,
                "expires_at": now + self.pairing_code_ttl,
            }
        return code

    def verify_pairing_code(self, code: str, identity: str) -> Optional[str]:
        """Verify pairing code. Returns session token on success, None on failure."""
        with self._lock:
            entry = self.pairing_codes.get(code)
            if not entry:
                return None
            if entry["identity"] != identity:
                return None
            if time.time() > entry["expires_at"]:
                del self.pairing_codes[code]
                return None
            # Consume the code
            del self.pairing_codes[code]
        # Generate session token
        token = f"session_{_secrets.token_urlsafe(32)}"
        now = time.time()
        with self._lock:
            self.session_tokens[token] = {
                "identity": identity,
                "channel": entry["channel"],
                "created_at": now,
                "last_used": now,
                "expires_at": now + self.session_token_ttl,
            }
        return token

    def validate_session_token(self, token: str) -> Optional[dict]:
        """Validate session token. Returns identity info or None."""
        with self._lock:
            entry = self.session_tokens.get(token)
            if not entry:
                return None
            if time.time() > entry["expires_at"]:
                del self.session_tokens[token]
                return None
            entry["last_used"] = time.time()
            # Extend expiry on use
            entry["expires_at"] = time.time() + self.session_token_ttl
            return {"identity": entry["identity"], "channel": entry["channel"]}

    def cleanup_expired(self):
        """Remove expired pairing codes and session tokens."""
        now = time.time()
        with self._lock:
            for code in list(self.pairing_codes):
                if now > self.pairing_codes[code]["expires_at"]:
                    del self.pairing_codes[code]
            for token in list(self.session_tokens):
                if now > self.session_tokens[token]["expires_at"]:
                    del self.session_tokens[token]
```

**Step 2: Run tests to verify they pass**

Run: `cd /opt/n8n-copilot-shim-dev && python -m pytest tests/test_api_auth.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add agent_manager.py
git commit -m "feat: add AuthManager and RateLimiter classes"
```

---

### Task 5: Write API Endpoint Tests

**Files:**
- Create: `tests/test_api_endpoints.py`

**Step 1: Write the failing tests**

```python
"""Tests for FastAPI endpoints."""
import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set env vars before import
os.environ["API_SHARED_KEY"] = "test_key_123"
os.environ["APP_ENV"] = "DEV"
os.environ["API_PORT"] = "8099"


class TestAPIEndpoints(unittest.TestCase):
    """Test API HTTP endpoints using FastAPI TestClient."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from agent_manager import create_api_app
        cls.app = create_api_app()
        cls.client = TestClient(cls.app)
        cls.shared_header = {"Authorization": "Bearer shared_test_key_123"}

    def test_health_endpoint(self):
        """Health check returns 200 with status ok."""
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("environment", data)

    def test_request_pairing_success(self):
        """Request pairing returns 200 with expires_in."""
        resp = self.client.post(
            "/api/v1/auth/request-pairing",
            json={"identity": "test_user", "channel": "telegram"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("expires_in", data)

    def test_request_pairing_invalid_channel(self):
        """Request pairing with invalid channel returns 422."""
        resp = self.client.post(
            "/api/v1/auth/request-pairing",
            json={"identity": "test_user", "channel": "sms"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_verify_pairing_invalid_code(self):
        """Verify pairing with bad code returns 400."""
        resp = self.client.post(
            "/api/v1/auth/verify-pairing",
            json={"code": "000000", "identity": "test_user"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_execute_no_auth(self):
        """Execute without auth returns 401."""
        resp = self.client.post(
            "/api/v1/sessions/test_session/execute",
            json={"query": "hello"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_execute_bad_shared_key(self):
        """Execute with wrong shared key returns 401."""
        resp = self.client.post(
            "/api/v1/sessions/test_session/execute",
            json={"query": "hello"},
            headers={"Authorization": "Bearer shared_wrong_key"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_session_create_with_shared_key(self):
        """Create session with shared key succeeds."""
        resp = self.client.post(
            "/api/v1/sessions/create",
            json={},
            headers={
                **self.shared_header,
                "X-User-Identity": "telegram_123",
                "X-Auth-Channel": "telegram",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("session_id", data)

    def test_session_status_not_found(self):
        """Status for nonexistent session returns 404."""
        resp = self.client.get(
            "/api/v1/sessions/nonexistent_session/status",
            headers=self.shared_header,
        )
        self.assertEqual(resp.status_code, 404)

    def test_full_pairing_flow(self):
        """End-to-end: request pairing, verify, get token, use token."""
        # Request pairing (code sent via mock)
        resp = self.client.post(
            "/api/v1/auth/request-pairing",
            json={"identity": "flow_user", "channel": "telegram"},
        )
        self.assertEqual(resp.status_code, 200)

        # Extract code from AuthManager directly (testing shortcut)
        from agent_manager import _api_auth_manager
        codes = {
            k: v
            for k, v in _api_auth_manager.pairing_codes.items()
            if v["identity"] == "flow_user"
        }
        self.assertEqual(len(codes), 1)
        code = list(codes.keys())[0]

        # Verify pairing
        resp = self.client.post(
            "/api/v1/auth/verify-pairing",
            json={"code": code, "identity": "flow_user"},
        )
        self.assertEqual(resp.status_code, 200)
        token = resp.json()["token"]
        self.assertTrue(token.startswith("session_"))

        # Use token for session create
        resp = self.client.post(
            "/api/v1/sessions/create",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run tests to verify they fail**

Run: `cd /opt/n8n-copilot-shim-dev && python -m pytest tests/test_api_endpoints.py -v`
Expected: FAIL with `ImportError: cannot import name 'create_api_app'`

**Step 3: Commit**

```bash
git add tests/test_api_endpoints.py
git commit -m "test: add API endpoint tests (red)"
```

---

### Task 6: Implement FastAPI App and Endpoints

**Files:**
- Modify: `agent_manager.py`

This is the largest task. Add the FastAPI app factory and all endpoints.

**Step 1: Add imports at top of agent_manager.py (after existing imports)**

After line 19 (`from typing import Optional, Tuple, Dict, List`), add:

```python
from datetime import datetime

# Lazy imports for API mode (only loaded when --api is used)
# FastAPI, uvicorn, pydantic are imported inside create_api_app()
```

**Step 2: Add Pydantic models and `create_api_app()` factory**

Add before the `main()` function (before line ~2917). This is a large block:

```python
# ============================================================
# API Server (FastAPI)
# ============================================================

# Module-level reference for test access
_api_auth_manager: Optional["AuthManager"] = None


def create_api_app() -> "FastAPI":
    """Create and configure the FastAPI application."""
    global _api_auth_manager

    from fastapi import FastAPI, Request, Header, HTTPException, Depends
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, field_validator
    from typing import Literal
    import traceback
    import asyncio

    # Load dotenv if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # --- Configuration from environment ---
    APP_ENV = os.environ.get("APP_ENV", "DEV")
    IS_PRODUCTION = APP_ENV == "PROD"
    SHARED_KEY = os.environ.get("API_SHARED_KEY", "")
    PAIRING_CODE_LENGTH = int(os.environ.get("PAIRING_CODE_LENGTH", "6"))
    PAIRING_CODE_TTL = int(os.environ.get("PAIRING_CODE_TTL", "300"))
    SESSION_TOKEN_TTL = int(os.environ.get("SESSION_TOKEN_TTL", "3600"))

    # Rate limit config
    RL_PAIRING_MAX = int(os.environ.get("RATE_LIMIT_PAIRING_REQUESTS", "3"))
    RL_PAIRING_WINDOW = int(os.environ.get("RATE_LIMIT_PAIRING_WINDOW", "900"))
    RL_EXECUTE_MAX = int(os.environ.get("RATE_LIMIT_EXECUTE_REQUESTS", "60"))
    RL_EXECUTE_WINDOW = int(os.environ.get("RATE_LIMIT_EXECUTE_WINDOW", "60"))
    RL_AUTH_FAIL_MAX = int(os.environ.get("RATE_LIMIT_AUTH_FAILURES", "5"))
    RL_AUTH_FAIL_WINDOW = int(os.environ.get("RATE_LIMIT_AUTH_FAILURE_WINDOW", "3600"))

    # --- Shared instances ---
    auth = AuthManager(
        shared_key=SHARED_KEY,
        pairing_code_length=PAIRING_CODE_LENGTH,
        pairing_code_ttl=PAIRING_CODE_TTL,
        session_token_ttl=SESSION_TOKEN_TTL,
    )
    _api_auth_manager = auth
    rate_limiter = RateLimiter()
    session_mgr = SessionManager()

    # --- Pydantic models ---
    class PairingRequest(BaseModel):
        identity: str
        channel: Literal["telegram", "webex"]

        @field_validator("identity")
        @classmethod
        def validate_identity(cls, v):
            import re as _re
            if not _re.match(r'^[\w\-\.]+$', v):
                raise ValueError("Identity must be alphanumeric (with underscores, hyphens, dots)")
            return v

    class PairingVerification(BaseModel):
        code: str
        identity: str

    class SessionCreate(BaseModel):
        agent: Optional[str] = None
        model: Optional[str] = None
        runtime: Optional[str] = None

    class ExecuteRequest(BaseModel):
        query: str
        timeout: Optional[int] = None

        @field_validator("query")
        @classmethod
        def validate_query_length(cls, v):
            if len(v) > 10000:
                raise ValueError("Query must be 10,000 characters or less")
            return v

    # --- FastAPI app ---
    app = FastAPI(
        title="Agent Manager API",
        version="1.0.0",
        docs_url="/api/v1/docs" if not IS_PRODUCTION else None,
        redoc_url="/api/v1/redoc" if not IS_PRODUCTION else None,
    )

    # --- Auth dependency ---
    async def authenticate(
        request: Request,
        authorization: Optional[str] = Header(None),
        x_user_identity: Optional[str] = Header(None),
        x_auth_channel: Optional[str] = Header(None),
    ) -> dict:
        """Resolve caller identity from shared key or session token."""
        client_ip = request.client.host if request.client else "unknown"

        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail={
                "error": "Missing or invalid Authorization header",
                "error_code": "AUTH_MISSING",
            })

        token = authorization[7:]  # strip "Bearer "

        # Shared key path
        if token.startswith("shared_"):
            if auth.validate_shared_key(token):
                return {
                    "auth_type": "shared_key",
                    "identity": x_user_identity or "unknown",
                    "channel": x_auth_channel or "unknown",
                }
            else:
                # Track auth failure
                rate_limiter.check(client_ip, "auth_failures", RL_AUTH_FAIL_MAX, RL_AUTH_FAIL_WINDOW)
                raise HTTPException(status_code=401, detail={
                    "error": "Invalid shared key",
                    "error_code": "AUTH_INVALID_SHARED_KEY",
                })

        # Session token path
        if token.startswith("session_"):
            result = auth.validate_session_token(token)
            if result:
                return {
                    "auth_type": "session_token",
                    "identity": result["identity"],
                    "channel": result["channel"],
                }
            else:
                rate_limiter.check(client_ip, "auth_failures", RL_AUTH_FAIL_MAX, RL_AUTH_FAIL_WINDOW)
                raise HTTPException(status_code=401, detail={
                    "error": "Invalid or expired session token",
                    "error_code": "AUTH_INVALID_SESSION",
                })

        raise HTTPException(status_code=401, detail={
            "error": "Unrecognized token format",
            "error_code": "AUTH_UNKNOWN_FORMAT",
        })

    # --- Exception handler ---
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        if IS_PRODUCTION:
            return JSONResponse(status_code=500, content={
                "error": "Internal server error",
                "error_code": "INTERNAL_ERROR",
            })
        return JSONResponse(status_code=500, content={
            "error": "Internal server error",
            "error_code": "INTERNAL_ERROR",
            "details": str(exc),
            "traceback": traceback.format_exc(),
        })

    # --- Background cleanup task ---
    @app.on_event("startup")
    async def startup_cleanup_task():
        async def _cleanup_loop():
            while True:
                await asyncio.sleep(60)
                auth.cleanup_expired()
                rate_limiter.cleanup()
        asyncio.create_task(_cleanup_loop())
        print(f"[INFO] Agent Manager API started - Environment: {APP_ENV}", file=sys.stderr)

    # --- Endpoints ---

    @app.get("/api/v1/health")
    async def health():
        return {
            "status": "ok",
            "version": "1.0.0",
            "environment": APP_ENV,
        }

    @app.post("/api/v1/auth/request-pairing")
    async def request_pairing(body: PairingRequest, request: Request):
        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.check(client_ip, "pairing", RL_PAIRING_MAX, RL_PAIRING_WINDOW):
            raise HTTPException(status_code=429, detail={
                "error": "Too many pairing requests",
                "error_code": "RATE_LIMITED",
                "retry_after": RL_PAIRING_WINDOW,
            })

        code = auth.generate_pairing_code(body.identity, body.channel)

        # Send code via bot connector (best effort)
        try:
            _send_pairing_code(body.identity, body.channel, code, PAIRING_CODE_TTL)
        except Exception as e:
            print(f"[WARN] Failed to send pairing code via {body.channel}: {e}", file=sys.stderr)

        return {
            "message": f"Pairing code sent to your {body.channel}",
            "expires_in": PAIRING_CODE_TTL,
        }

    @app.post("/api/v1/auth/verify-pairing")
    async def verify_pairing(body: PairingVerification, request: Request):
        client_ip = request.client.host if request.client else "unknown"
        token = auth.verify_pairing_code(body.code, body.identity)
        if not token:
            rate_limiter.check(client_ip, "auth_failures", RL_AUTH_FAIL_MAX, RL_AUTH_FAIL_WINDOW)
            raise HTTPException(status_code=400, detail={
                "error": "Invalid or expired pairing code",
                "error_code": "PAIRING_CODE_INVALID",
            })
        return {
            "token": token,
            "expires_in": SESSION_TOKEN_TTL,
        }

    @app.post("/api/v1/sessions/create")
    async def create_session(
        body: SessionCreate,
        caller: dict = Depends(authenticate),
    ):
        identity = caller["identity"]
        session_id = identity
        session_data = session_mgr.get_or_create_session_data(session_id)

        if body.agent:
            session_mgr.update_session_field(session_id, "agent", body.agent)
            session_data["agent"] = body.agent
        if body.model:
            session_mgr.update_session_field(session_id, "model", body.model)
            session_data["model"] = body.model
        if body.runtime:
            session_mgr.update_session_field(session_id, "runtime", body.runtime)
            session_data["runtime"] = body.runtime

        return {
            "session_id": session_id,
            "agent": session_data.get("agent", "orchestrator"),
            "model": session_data.get("model"),
            "runtime": session_data.get("runtime"),
        }

    @app.post("/api/v1/sessions/{session_id}/execute")
    async def execute(
        session_id: str,
        body: ExecuteRequest,
        request: Request,
        caller: dict = Depends(authenticate),
    ):
        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.check(client_ip, "execute", RL_EXECUTE_MAX, RL_EXECUTE_WINDOW):
            raise HTTPException(status_code=429, detail={
                "error": "Too many execute requests",
                "error_code": "RATE_LIMITED",
                "retry_after": RL_EXECUTE_WINDOW,
            })

        # Run execution in thread pool to avoid blocking
        import concurrent.futures
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(
                pool, session_mgr.execute, body.query, session_id
            )

        session_data = session_mgr.get_or_create_session_data(session_id)
        return {
            "session_id": session_id,
            "response": result,
            "runtime": session_data.get("runtime"),
            "model": session_data.get("model"),
        }

    @app.get("/api/v1/sessions/{session_id}/status")
    async def session_status(
        session_id: str,
        caller: dict = Depends(authenticate),
    ):
        data = session_mgr.load_session_data(session_id)
        if not data:
            raise HTTPException(status_code=404, detail={
                "error": "Session not found",
                "error_code": "SESSION_NOT_FOUND",
            })
        return {
            "session_id": session_id,
            "agent": data.get("agent", "orchestrator"),
            "model": data.get("model"),
            "runtime": data.get("runtime"),
        }

    return app


def _send_pairing_code(identity: str, channel: str, code: str, ttl: int):
    """Send pairing code to user via the appropriate bot connector."""
    minutes = ttl // 60
    message = f"Your API pairing code is: {code}\n(Expires in {minutes} minutes)"

    if channel == "telegram":
        try:
            from telegram_connector import TelegramConnector
            config_path = os.path.join(os.path.dirname(__file__), "telegram_config.json")
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
                token = config.get("token", "")
                if token:
                    connector = TelegramConnector(token=token, config_path=config_path)
                    connector.send_message(int(identity), message)
                    return
        except Exception as e:
            print(f"[WARN] Telegram send failed: {e}", file=sys.stderr)
    elif channel == "webex":
        try:
            from webex_connector import WebEXConnector
            config_path = os.path.join(os.path.dirname(__file__), "webex_config.json")
            if os.path.exists(config_path):
                connector = WebEXConnector(config_path=config_path)
                connector.send_message(identity, message)
                return
        except Exception as e:
            print(f"[WARN] WebEx send failed: {e}", file=sys.stderr)

    print(f"[WARN] Could not send pairing code via {channel} to {identity}", file=sys.stderr)


def start_api_server():
    """Start the FastAPI server using uvicorn."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    import uvicorn

    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))

    app = create_api_app()
    print(f"[INFO] Starting Agent Manager API on {host}:{port}", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")
```

**Step 3: Modify the `if __name__` block**

Replace the existing block at line 3079-3080:

```python
if __name__ == "__main__":
    # Quick check for --api flag before argparse (main() has its own parser)
    if "--api" in sys.argv:
        start_api_server()
    else:
        main()
```

**Step 4: Run tests to verify they pass**

Run: `cd /opt/n8n-copilot-shim-dev && python -m pytest tests/test_api_auth.py tests/test_api_endpoints.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add agent_manager.py
git commit -m "feat: add FastAPI app with auth, rate limiting, and session endpoints"
```

---

### Task 7: Manual Smoke Test

**Step 1: Start the dev API server**

Run: `cd /opt/n8n-copilot-shim-dev && python3 agent_manager.py --api &`
Expected: Server starts on 127.0.0.1:8001 (from .env)

**Step 2: Test health endpoint**

Run: `curl -s http://127.0.0.1:8001/api/v1/health | python3 -m json.tool`
Expected: `{"status": "ok", "version": "1.0.0", "environment": "DEV"}`

**Step 3: Test shared key execute**

Run:
```bash
curl -s -X POST http://127.0.0.1:8001/api/v1/sessions/create \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer shared_$(grep API_SHARED_KEY /opt/n8n-copilot-shim-dev/.env | cut -d= -f2)" \
  -H "X-User-Identity: test_user_123" \
  -H "X-Auth-Channel: telegram" \
  -d '{}' | python3 -m json.tool
```
Expected: 200 with session_id

**Step 4: Test unauthorized access**

Run: `curl -s -X POST http://127.0.0.1:8001/api/v1/sessions/test/execute -H "Content-Type: application/json" -d '{"query": "hello"}' | python3 -m json.tool`
Expected: 401 error

**Step 5: Stop the server**

Run: `kill %1` (or the PID)

**Step 6: Verify CLI mode still works**

Run: `cd /opt/n8n-copilot-shim-dev && python3 agent_manager.py --list-agents`
Expected: Normal agent list output (no API server started)

---

### Task 8: Create Systemd Service Files

**Files:**
- Create: `agent-manager-api.service`
- Create: `agent-manager-api-dev.service`

**Step 1: Create production service file**

```ini
[Unit]
Description=Agent Manager API Server (Production)
After=network.target

[Service]
Type=simple
User=n8n
WorkingDirectory=/opt/n8n-copilot-shim
Environment=PATH=/usr/bin:/usr/local/bin
EnvironmentFile=/opt/n8n-copilot-shim/.env
ExecStart=/usr/bin/python3 /opt/n8n-copilot-shim/agent_manager.py --api
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Step 2: Create development service file**

```ini
[Unit]
Description=Agent Manager API Server (Development)
After=network.target

[Service]
Type=simple
User=n8n
WorkingDirectory=/opt/n8n-copilot-shim-dev
Environment=PATH=/usr/bin:/usr/local/bin
EnvironmentFile=/opt/n8n-copilot-shim-dev/.env
ExecStart=/usr/bin/python3 /opt/n8n-copilot-shim-dev/agent_manager.py --api
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Step 3: Commit**

```bash
git add agent-manager-api.service agent-manager-api-dev.service
git commit -m "feat: add systemd service files for API server (dev and prod)"
```

**Step 4: Deploy dev service (optional)**

```bash
sudo cp /opt/n8n-copilot-shim-dev/agent-manager-api-dev.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable agent-manager-api-dev.service
sudo systemctl start agent-manager-api-dev.service
sudo systemctl status agent-manager-api-dev.service
```

---

### Task 9: Update .env.example and Commit Final

**Step 1: Run full test suite**

Run: `cd /opt/n8n-copilot-shim-dev && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 2: Push to origin**

```bash
git push origin dev
```

---

## Summary of Files Changed

| File | Action | Description |
|------|--------|-------------|
| `requirements.txt` | Create | FastAPI, uvicorn, pydantic, python-dotenv |
| `.env.example` | Modify | Add API configuration variables |
| `.env` | Modify | Add actual API config (not committed) |
| `agent_manager.py` | Modify | Add AuthManager, RateLimiter, FastAPI app, start_api_server() |
| `tests/test_api_auth.py` | Create | AuthManager + RateLimiter unit tests |
| `tests/test_api_endpoints.py` | Create | FastAPI endpoint integration tests |
| `agent-manager-api.service` | Create | Systemd service for production |
| `agent-manager-api-dev.service` | Create | Systemd service for development |

## Commit History (Expected)

1. `feat: add FastAPI dependencies for API server`
2. `feat: add API server environment variables to .env.example`
3. `test: add AuthManager and RateLimiter unit tests (red)`
4. `feat: add AuthManager and RateLimiter classes`
5. `test: add API endpoint tests (red)`
6. `feat: add FastAPI app with auth, rate limiting, and session endpoints`
7. `feat: add systemd service files for API server (dev and prod)`
