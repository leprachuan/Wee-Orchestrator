# Release Notes — Wee-Orchestrator

## v2.0.0 — 2026-02-21

### Highlights

This release is the largest single update to the project. It ships a browser-based chat UI, an embedded task scheduler, WebEx file handling, per-user access-control pinning, and a complete FastAPI-based REST API with pairing-code authentication. The project is also formally renamed from **n8n-copilot-shim** to **Wee-Orchestrator** (folder names unchanged for backwards compatibility).

---

### 🌐 Web UI (`webui/`)

A fully featured browser-based chat interface is now served at `/ui` by the API server.

- **Glassmorphism design** — frosted-glass panels, animated gradient blobs, fully responsive
- **Pairing-code authentication** — 6-digit one-time code delivered via Telegram or WebEx; no passwords stored
- **Chat panel**
  - Markdown + syntax-highlighted code block rendering
  - Images rendered inline with overflow protection
  - Clickable meta pills (agent · runtime · model · mode) in the chat header
  - `@username` display instead of raw numeric IDs
  - `/command` highlighting and typeahead autocomplete in the input box
  - Drag-and-drop / click file uploads attached to messages
- **Auto image search** — when the AI triggers an image request, the server fetches results from DuckDuckGo and serves them at `/ai-media/`; the browser renders them inline
- **Sidebar navigation** — switch between **Chat** and **Scheduler** panels without leaving the page
- **Leprechaun 🍀 branding** — sidebar title "Wee-Orchestrator", favicon, and auth logo
- **Session list** — past sessions shown in the sidebar; click to restore history

---

### 📅 Task Scheduler (`task_scheduler.py`)

A cron-like AI task scheduler is now embedded in the orchestrator core.

- **Natural-language schedules** — `in 10 minutes`, `every 2 hours`, `every day at 9am`
- **Recurring or one-shot jobs** — set `recurring: false` for a job that runs once and stops
- **Per-job AI configuration** — each job independently sets agent, runtime, model, and mode (yolo / restricted)
- **Restricted mode by default** — the scheduler UI defaults to `restricted` for safety
- **Creator-targeted notifications** — job results are sent back to the Telegram or WebEx user who created the job
- **Per-user ACL** — only users listed in `SCHEDULER_ALLOWED_TELEGRAM` / `SCHEDULER_ALLOWED_WEBEX` env vars can create or manage jobs
- **Pause / Resume** — temporarily disable a job without deleting it
- **Results history** — last N execution results stored per job, browsable in the Web UI or via API
- **Scheduler panel in Web UI**
  - Job list with status badges (active / paused / disabled)
  - Detail drawer showing full job config, next-run time, and recent results
  - Create / edit form with agent, runtime, model, and mode selectors
  - Toast notifications for all CRUD operations
  - Daemon status badge showing scheduler health

**REST API endpoints added:**

| Method | Path |
|--------|------|
| `GET` | `/api/v1/scheduler/status` |
| `GET` | `/api/v1/scheduler/jobs` |
| `POST` | `/api/v1/scheduler/jobs` |
| `GET` | `/api/v1/scheduler/jobs/{id}` |
| `PUT` | `/api/v1/scheduler/jobs/{id}` |
| `DELETE` | `/api/v1/scheduler/jobs/{id}` |
| `POST` | `/api/v1/scheduler/jobs/{id}/pause` |
| `POST` | `/api/v1/scheduler/jobs/{id}/resume` |
| `GET` | `/api/v1/scheduler/jobs/{id}/results` |
| `GET` | `/api/v1/scheduler/jobs/{id}/logs` |

---

### 🔐 FastAPI REST API (`agent_manager.py` — `create_api_app()`)

A full REST API was introduced to support the Web UI and external integrations.

- **Pairing-code auth flow** — `POST /api/v1/auth/request-pairing` → receive 6-digit code on Telegram/WebEx → `POST /api/v1/auth/verify` → receive Bearer session token
- **Shared-key auth** — `API_SHARED_KEY` env var for server-to-server calls
- **Rate limiting** — per-IP sliding-window on all endpoints
- **Session endpoints** — create, execute, status, delete
- **History endpoints** — list sessions, get messages, delete session
- **File upload/download** — `POST /api/v1/sessions/{id}/upload`, `GET /api/v1/uploads/{id}/{filename}`
- **Image search** — `GET /api/v1/search/images?q=...`
- **Static WebUI** — `GET /ui` serves `webui/dist/` (SPA with HTML5 history fallback)
- **AI media** — `GET /ai-media/{filename}` serves images fetched by AI agents
- **CORS** updated to include `PUT` method alongside existing verbs

New service files: `agent-manager-api.service` and `agent-manager-api-dev.service`.

---

### 📁 WebEx File Handling (`webex_connector.py`)

WebEx file/image support is now at parity with the Telegram connector.

- **Receive files** — incoming WebEx messages with attachments are downloaded to `webex_downloads/` and injected into the agent context
- **Send files** — agents can produce local file paths that the connector uploads back to the room
- **Automatic cleanup** — temp files older than 5 minutes are purged from `webex_downloads/` and `/tmp/`
- Documentation: `WEBEX_FILE_HANDLING.md`, `FILE_MEDIA_HANDLING_SKILL.md`, `FILE_HANDLING_QUICK_REF.md`

---

### 🔒 Per-User Access Control

- **Agent pinning** — `pinned_users` config dict locks a user/person ID to a specific agent; `/agent set` is blocked with an admin message for pinned users
- **Runtime & model pinning** — extend `pinned_users` entries with `runtime` and `model` fields; these are re-applied before every query so session resets cannot bypass them
- **Yolo mode restriction** — `yolo_allowed_users` list; if non-empty only listed IDs may run `/mode yolo`; empty list preserves the permissive default
- Changes are symmetric across `telegram_connector.py` and `webex_connector.py`

---

### 🐛 Bug Fixes

| Fix | Description |
|-----|-------------|
| `/model set` quote stripping | Model names surrounded by quotes (e.g. `"gpt-5-mini"`) are now stripped before storage |
| WebUI image overflow | Images in chat bubbles no longer overflow the message container |
| WebUI model pill names | Model names in the pill selector now use dots not hyphens (e.g. `gpt-5.1` not `gpt-5-1`) and exclude surrounding quotes |
| Media instructions | Added Option C (local screenshot files) to AI media handling instructions |
| Service PATH | All systemd service files now prepend `~/.local/bin` and nvm Node v22 to `PATH` so the correct copilot binary is found |
| DBUS / XDG\_RUNTIME\_DIR | Added missing env vars to all connector service files so copilot keyring access works correctly |

---

### 🧪 Tests

- Added `tests/test_new_features.py` — **79 new tests** covering auth/pairing, history manager, file upload, all scheduler endpoints, image search, and rate limiting
- Total test count: **141 tests** (was 62)

---

## v1.x — Prior Releases

### v1.5 — 2026-02-16 through 2026-02-20
- FastAPI application scaffold with `AuthManager` and `RateLimiter` (foundational work for v2.0 API)
- `EnvironmentFile` support in all systemd service files; `.env.example` template
- Optional API mode in Telegram and WebEx connectors
- `agent-manager-api` systemd service files (dev + prod)
- Various service user and PATH fixes

### v1.4 — Earlier
- `SessionManager`: full slash-command system (`/agent`, `/runtime`, `/model`, `/session`, `/status`, `/cancel`, `/mode`)
- Query tracking (`~/.copilot/running-queries.json`) enabling `/status` and `/cancel` for long-running queries
- Per-session model and runtime switching; session state persistence
- Multi-runtime support: Copilot, OpenCode, Claude Code, Gemini, Codex
- `agents.json` dynamic agent loading replacing hardcoded AGENTS dict
- Telegram connector with user whitelist/blacklist, user pairing
- WebEx connector with RabbitMQ integration
- N8N workflow integration examples
- Skill discovery and subagent delegation framework
- `/status` and `/cancel` query management commands
