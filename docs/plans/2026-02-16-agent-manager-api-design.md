# Agent Manager API Design

**Date:** 2026-02-16
**Status:** Approved
**Environment:** Development (`/opt/n8n-copilot-shim-dev`)

## Overview

Add RESTful API functionality to `agent_manager.py` using FastAPI with dual authentication: shared key authentication for bot connectors (telegram/webex) and session-based authentication with pairing codes for external API consumers.

## Goals

- Enable HTTP API access to agent execution functionality
- Maintain existing CLI `main()` functionality
- Support federated authentication for trusted bot connectors
- Provide secure pairing-based authentication for external clients
- Keep backward compatibility with direct SessionManager usage

## Architecture

### Hybrid Mode Design

The API will be added directly to `agent_manager.py` with a mode selector:

```python
if __name__ == "__main__":
    parser.add_argument("--api", action="store_true",
                       help="Run in API server mode")
    args = parser.parse_args()

    if args.api:
        start_api_server()  # Runs FastAPI with uvicorn
    else:
        main()  # Existing CLI mode
```

**Modes:**
- CLI mode: `python agent_manager.py` (existing behavior)
- API mode: `python agent_manager.py --api` (new)

### Component Structure

```
agent_manager.py
├── SessionManager (existing)
├── AuthManager (new - handles pairing codes, session tokens, shared key)
├── FastAPI app (new - HTTP endpoints)
├── main() - CLI mode (existing)
└── start_api_server() - API mode (new)
```

## Authentication

### 1. Shared Key Authentication (Bot-to-API)

For telegram_connector.py and webex_connector.py to call the API:

**Flow:**
```
Telegram/WebEx Connector → HTTP Request with Bearer shared_{key}
                         → API validates shared_key from .env
                         → User identity passed in headers
                         → Direct execution (federated trust)
```

**Request Format:**
```python
headers = {
    "Authorization": f"Bearer shared_{SHARED_API_KEY}",
    "X-User-Identity": "telegram_8405010413_8193231291",
    "X-Auth-Channel": "telegram"  # or "webex"
}
```

**Validation:**
1. Token starts with `shared_` prefix
2. Matches `API_SHARED_KEY` from .env
3. Extracts user identity from `X-User-Identity` header
4. Trusts the identity (federated authentication)

### 2. Session-based Authentication (External-to-API)

For external API consumers who need to authenticate:

**Pairing Flow:**

1. **Request Pairing**
   ```
   POST /api/v1/auth/request-pairing
   Body: {"identity": "8193231291", "channel": "telegram"}
   ```
   - API generates 6-digit code (configurable)
   - Stores code with expiration (5 minutes default)
   - Sends code to user via bot connector
   - Returns: `{"message": "Pairing code sent", "expires_in": 300}`

2. **User Receives Code**
   ```
   Bot message: "Your API pairing code is: 482719 (expires in 5 min)"
   ```

3. **Verify Pairing**
   ```
   POST /api/v1/auth/verify-pairing
   Body: {"code": "482719", "identity": "8193231291"}
   ```
   - Validates code matches and not expired
   - Generates session token: `session_abc123xyz...`
   - Returns: `{"token": "session_abc123xyz...", "expires_in": 3600}`

4. **Use Session Token**
   ```
   Authorization: Bearer session_abc123xyz...
   ```

### Storage

In-memory storage with TTL cleanup:

```python
pairing_codes = {}      # code -> {identity, channel, created_at, expires_at}
session_tokens = {}     # token -> {identity, channel, created_at, last_used, expires_at}
rate_limits = {}        # ip -> {endpoint -> [timestamps]}
```

**Cleanup:**
- Background task runs every 60 seconds
- Removes expired pairing codes and session tokens
- Prunes old rate limit entries

## API Endpoints

### Authentication Endpoints

**Request Pairing**
```
POST /api/v1/auth/request-pairing
Request:
{
    "identity": "8193231291",
    "channel": "telegram"  # or "webex"
}
Response (200):
{
    "message": "Pairing code sent to your telegram",
    "expires_in": 300
}
Response (429):
{
    "error": "Too many pairing requests",
    "retry_after": 600
}
```

**Verify Pairing**
```
POST /api/v1/auth/verify-pairing
Request:
{
    "code": "482719",
    "identity": "8193231291"
}
Response (200):
{
    "token": "session_abc123xyz...",
    "expires_in": 3600
}
Response (400):
{
    "error": "Invalid or expired pairing code"
}
```

### Session Endpoints

**Create Session**
```
POST /api/v1/sessions/create
Headers:
    Authorization: Bearer {shared_key or session_token}
    X-User-Identity: telegram_8193231291  # only for shared_key auth
    X-Auth-Channel: telegram               # only for shared_key auth
Request:
{
    "agent": "orchestrator",      # optional, defaults from .env
    "model": "gpt-5-mini",        # optional, defaults from .env
    "runtime": "copilot"          # optional, defaults from .env
}
Response (200):
{
    "session_id": "telegram_8193231291",
    "agent": "orchestrator",
    "model": "gpt-5-mini",
    "runtime": "copilot"
}
```

**Execute Query**
```
POST /api/v1/sessions/{session_id}/execute
Headers:
    Authorization: Bearer {shared_key or session_token}
Request:
{
    "query": "What is the status of the cluster?",
    # OR slash command: "/mode yolo", "/runtime set claude"
    "timeout": 300  # optional, defaults from .env
}
Response (200):
{
    "session_id": "telegram_8193231291",
    "response": "The cluster is running normally...",
    # OR "✓ YOLO mode enabled - auto-approving actions"
    "runtime": "copilot",
    "model": "gpt-5-mini"
}
```

**Get Session Status**
```
GET /api/v1/sessions/{session_id}/status
Headers:
    Authorization: Bearer {shared_key or session_token}
Response (200):
{
    "session_id": "telegram_8193231291",
    "agent": "orchestrator",
    "model": "gpt-5-mini",
    "runtime": "copilot",
    "created_at": "2026-02-16T17:00:00Z",
    "last_activity": "2026-02-16T17:05:00Z"
}
```

**Health Check**
```
GET /api/v1/health
# No auth required
Response (200):
{
    "status": "ok",
    "version": "1.0.0",
    "environment": "DEV"  # or "PROD"
}
```

### Slash Command Support

All slash commands work through the `/execute` endpoint:
- `/mode yolo` - Enable yolo mode
- `/runtime set claude` - Change runtime
- `/model set sonnet` - Change model
- All other existing slash commands

## Data Models

```python
from pydantic import BaseModel
from typing import Optional, Literal

class PairingRequest(BaseModel):
    identity: str
    channel: Literal["telegram", "webex"]

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
```

## Error Handling

### Error Response Format

All errors follow consistent format:

```python
{
    "error": "Human-readable error message",
    "error_code": "PAIRING_CODE_EXPIRED",  # Machine-readable code
    "details": {}  # Optional additional context
}
```

### HTTP Status Codes

- `200` - Success
- `400` - Bad Request (invalid input, expired code)
- `401` - Unauthorized (missing/invalid token)
- `403` - Forbidden (valid token, insufficient permissions)
- `404` - Not Found (session doesn't exist)
- `429` - Too Many Requests (rate limited)
- `500` - Internal Server Error (unexpected failure)

### Environment-Specific Error Handling

```python
APP_ENV = os.getenv("APP_ENV", "DEV")

if APP_ENV == "PROD":
    # Production: minimal error details
    return {"error": "Internal server error"}
else:
    # Development: verbose errors for debugging
    return {
        "error": "Internal server error",
        "details": str(exc),
        "traceback": traceback.format_exc()
    }
```

## Security

### Rate Limiting

Per-IP and per-session limits:

```bash
# .env configuration
RATE_LIMIT_PAIRING_REQUESTS=3        # requests per window
RATE_LIMIT_PAIRING_WINDOW=900        # 15 minutes
RATE_LIMIT_EXECUTE_REQUESTS=60       # requests per window
RATE_LIMIT_EXECUTE_WINDOW=60         # 1 minute
RATE_LIMIT_AUTH_FAILURES=5           # failed attempts per window
RATE_LIMIT_AUTH_FAILURE_WINDOW=3600  # 1 hour lockout
```

### Token Generation

```python
import secrets

# Pairing codes: configurable length, numeric
pairing_code = ''.join([str(secrets.randbelow(10))
                        for _ in range(PAIRING_CODE_LENGTH)])

# Session tokens: cryptographically secure
session_token = f"session_{secrets.token_urlsafe(32)}"

# Shared key prefix for identification
shared_key_prefix = "shared_"
```

### Input Validation

- Pydantic models validate all request bodies
- Identity strings: alphanumeric + underscores only
- Session IDs: validated format
- Query strings: max length 10,000 chars

### Token Expiration

- Pairing codes: expire after `PAIRING_CODE_TTL` (default 300s)
- Session tokens: expire after 1 hour of inactivity
- Shared key: never expires, validated against .env

### CORS

```bash
# .env
API_CORS_ORIGINS=  # empty = no CORS (localhost only)
# For external access: API_CORS_ORIGINS=https://example.com,https://app.example.com
```

## Integration with Existing Connectors

### Connector Updates

Both `telegram_connector.py` and `webex_connector.py` will be updated to optionally use the API:

```python
# Add to .env
USE_API=false                    # true = use API, false = direct SessionManager
API_URL=http://127.0.0.1:8000   # only used if USE_API=true

# In connector
self.use_api = os.getenv("USE_API", "false").lower() == "true"
self.api_url = os.getenv("API_URL", "http://127.0.0.1:8000")
self.shared_key = os.getenv("API_SHARED_KEY", "")

if self.use_api:
    response = self._execute_via_api(query, session_id, user_identity, channel)
else:
    session_mgr = self.get_session_manager(session_id)
    response = session_mgr.execute(query, session_id)

def _execute_via_api(self, query, session_id, user_identity, channel):
    """Execute query via API using shared key authentication"""
    headers = {
        "Authorization": f"Bearer shared_{self.shared_key}",
        "X-User-Identity": user_identity,
        "X-Auth-Channel": channel
    }
    payload = {"query": query}

    response = requests.post(
        f"{self.api_url}/api/v1/sessions/{session_id}/execute",
        headers=headers,
        json=payload,
        timeout=self.timeout
    )

    if response.status_code == 200:
        return response.json()["response"]
    else:
        # Fallback to direct mode on API failure
        logger.error(f"API request failed: {response.status_code}")
        session_mgr = self.get_session_manager(session_id)
        return session_mgr.execute(query, session_id)
```

### Sending Pairing Codes

The API sends pairing codes by importing connector functions:

```python
# In agent_manager.py API code
from telegram_connector import send_message as telegram_send
from webex_connector import send_message as webex_send

def send_pairing_code(identity, channel, code):
    message = f"Your API pairing code is: {code}\n(Expires in 5 minutes)"

    if channel == "telegram":
        telegram_send(identity, message)
    elif channel == "webex":
        webex_send(identity, message)
```

### Backward Compatibility

- Existing direct SessionManager usage continues to work
- `USE_API=false` (default) maintains current behavior
- Only opt-in connectors use API mode
- No breaking changes

## Environment Variables

### New Variables

```bash
# Environment
APP_ENV=DEV  # DEV or PROD

# API Configuration
API_HOST=127.0.0.1
API_PORT=8000
API_SHARED_KEY=generate_secure_random_key_here

# Pairing Code Settings
PAIRING_CODE_LENGTH=6
PAIRING_CODE_TTL=300

# Rate Limiting
RATE_LIMIT_PAIRING_REQUESTS=3
RATE_LIMIT_PAIRING_WINDOW=900
RATE_LIMIT_EXECUTE_REQUESTS=60
RATE_LIMIT_EXECUTE_WINDOW=60
RATE_LIMIT_AUTH_FAILURES=5
RATE_LIMIT_AUTH_FAILURE_WINDOW=3600

# Session Settings
SESSION_TOKEN_TTL=3600
API_CORS_ORIGINS=

# Connector API Usage (in telegram/webex .env)
USE_API=false
API_URL=http://127.0.0.1:8000
```

### Production vs Development

**Production (.env in /opt/n8n-copilot-shim):**
```bash
APP_ENV=PROD
API_HOST=127.0.0.1
API_PORT=8000
API_SHARED_KEY=<prod-secure-key>
```

**Development (.env in /opt/n8n-copilot-shim-dev):**
```bash
APP_ENV=DEV
API_HOST=127.0.0.1
API_PORT=8001  # Different port
API_SHARED_KEY=<dev-secure-key>  # Different key
```

## Deployment

### Dependencies

Add to `requirements.txt`:

```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
pydantic>=2.5.0
python-multipart>=0.0.6
```

### Systemd Services

**Production Service:**

```ini
# /etc/systemd/system/agent-manager-api.service
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

**Development Service:**

```ini
# /etc/systemd/system/agent-manager-api-dev.service
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

### Service Management

```bash
# Production
sudo systemctl start agent-manager-api.service
sudo systemctl status agent-manager-api.service
sudo journalctl -u agent-manager-api.service -f

# Development
sudo systemctl start agent-manager-api-dev.service
sudo systemctl status agent-manager-api-dev.service
sudo journalctl -u agent-manager-api-dev.service -f
```

## Testing

### Unit Tests

```python
# test_api_auth.py
- test_generate_pairing_code()
- test_validate_pairing_code_success()
- test_validate_pairing_code_expired()
- test_validate_shared_key()
- test_generate_session_token()
- test_rate_limiting()

# test_api_endpoints.py
- test_request_pairing_endpoint()
- test_verify_pairing_endpoint()
- test_execute_with_shared_key()
- test_execute_with_session_token()
- test_session_status_endpoint()
- test_unauthorized_access()
```

### Integration Tests

```python
# test_connector_api_integration.py
- test_telegram_connector_uses_api()
- test_webex_connector_uses_api()
- test_fallback_to_direct_mode()
- test_end_to_end_pairing_flow()
```

### Manual Testing

```bash
# Start API server
python agent_manager.py --api

# Test pairing flow
curl -X POST http://localhost:8000/api/v1/auth/request-pairing \
  -H "Content-Type: application/json" \
  -d '{"identity": "8193231291", "channel": "telegram"}'

# Check health
curl http://localhost:8000/api/v1/health
```

## Logging

```python
# Log all API requests
[INFO] POST /api/v1/auth/request-pairing - IP: 1.2.3.4 - Identity: 8193231291
[INFO] POST /api/v1/sessions/xyz/execute - User: telegram_8193231291 - Runtime: claude

# Log security events
[WARN] Rate limit exceeded - IP: 1.2.3.4 - Endpoint: /auth/request-pairing
[ERROR] Invalid shared key attempt - IP: 1.2.3.4
[WARN] Expired pairing code used - Code: 482719 - Identity: 8193231291

# Log cleanup
[DEBUG] Cleanup: Removed 3 expired pairing codes, 1 expired session token

# Startup logging
[INFO] Starting Agent Manager API - Environment: PROD
[INFO] Listening on 127.0.0.1:8000
```

## Rollout Strategy

1. Deploy API server in dev environment (`/opt/n8n-copilot-shim-dev`)
2. Test with dev telegram/webex bots (`USE_API=true`)
3. Verify pairing flow and execution
4. Monitor logs for errors
5. Deploy to prod with `USE_API=false` initially
6. Gradually enable `USE_API=true` for connectors
7. Monitor performance and error rates

## Documentation

Create `docs/api-usage.md`:
- API endpoint reference
- Authentication flow examples
- Rate limiting details
- Example code snippets (Python, curl)
- Troubleshooting guide

## Success Criteria

- ✅ API server runs via `python agent_manager.py --api`
- ✅ Existing CLI mode (`python agent_manager.py`) unchanged
- ✅ Shared key authentication works for telegram/webex connectors
- ✅ Pairing flow successfully authenticates external clients
- ✅ Rate limiting prevents abuse
- ✅ Session tokens expire appropriately
- ✅ Slash commands work through API
- ✅ Fallback to direct mode works when API unavailable
- ✅ Separate dev and prod services deployed
- ✅ All tests pass

## Future Enhancements

- WebSocket support for streaming responses
- Redis backend for distributed deployments
- OpenAPI/Swagger UI automatic documentation
- Metrics endpoint (Prometheus format)
- Admin API for managing sessions/tokens
- Multi-factor authentication option
