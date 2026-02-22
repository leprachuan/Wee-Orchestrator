# Architecture — Wee-Orchestrator

> This document describes the runtime architecture of Wee-Orchestrator as of v2.0.

## Table of Contents

- [System Overview](#system-overview)
- [Component Diagram](#component-diagram)
- [Request Flow — Chat Message](#request-flow--chat-message)
- [Request Flow — Web UI](#request-flow--web-ui)
- [Task Scheduler Flow](#task-scheduler-flow)
- [Authentication Flow (Web UI Pairing)](#authentication-flow-web-ui-pairing)
- [Component Reference](#component-reference)
- [Data Stores](#data-stores)
- [Deployment Topology](#deployment-topology)
- [Environment Variables](#environment-variables)

---

## System Overview

Wee-Orchestrator is a Python-based multi-channel AI orchestration platform. It receives messages from three inbound channels (**Telegram**, **WebEx**, **Web UI**), routes them through a shared `SessionManager`, dispatches work to one of five AI CLI runtimes, and returns the response.

A built-in **Task Scheduler** can trigger AI jobs autonomously and deliver results back to the originating user via the same channel adapters.

```
┌──────────────────────────────────────────────────────────────────┐
│                      Wee-Orchestrator Host                        │
│                                                                   │
│   Telegram ──► TelegramConnector ──┐                             │
│                                    │                             │
│   WebEx ────► WebEXConnector ──────┼──► SessionManager ──► AI   │
│                                    │                   CLIs      │
│   Browser ──► FastAPI /api/v1 ─────┘                             │
│                     │                                             │
│              TaskScheduler ───────────────────────────────────── │
└──────────────────────────────────────────────────────────────────┘
```

---

## Component Diagram

```mermaid
graph TB
    subgraph Inbound["Inbound Channels"]
        TG["📱 Telegram\n(Bot API polling)"]
        WX["💼 WebEx\n(Webhook / RabbitMQ)"]
        BR["🌐 Browser\n(/ui)"]
    end

    subgraph Connectors["Channel Connectors"]
        TC["TelegramConnector\ntelegram_connector.py"]
        WC["WebEXConnector\nwebex_connector.py"]
        FA["FastAPI App\nagent_manager.py"]
    end

    subgraph Core["Orchestrator Core (agent_manager.py)"]
        SM["SessionManager\n- slash commands\n- session state\n- agent/runtime/model\n- query tracking"]
        HM["HistoryManager\nchat-history.json"]
        AM["AuthManager\npairing codes\nsession tokens"]
        RL["RateLimiter\nper-IP sliding window"]
    end

    subgraph Scheduler["Task Scheduler (task_scheduler.py)"]
        TS["TaskScheduler\n- schedule / pause / resume\n- results & logs\n- creator notifications"]
    end

    subgraph Runtimes["AI CLI Runtimes"]
        CP["🤖 GitHub Copilot CLI"]
        OC["🔵 OpenCode"]
        CL["🟠 Claude Code"]
        GM["🔴 Google Gemini"]
        CX["⚪ OpenAI Codex"]
    end

    subgraph Storage["Data Stores"]
        SJ["~/.copilot/\nn8n-session-map.json\nrunning-queries.json"]
        CH["~/.copilot/\nchat-history.json"]
        JF["/opt/.task-scheduler/\njobs.json\nresults/  logs/"]
        UF["webex_downloads/\n/tmp/webui_ai_media/"]
    end

    TG --> TC
    WX --> WC
    BR --> FA

    TC --> SM
    WC --> SM
    FA --> SM
    FA --> AM
    FA --> RL
    FA --> HM
    FA --> TS

    SM --> CP
    SM --> OC
    SM --> CL
    SM --> GM
    SM --> CX
    SM --> SJ

    HM --> CH
    TS --> SM
    TS --> JF
    TC --> UF
    WC --> UF
    FA --> UF
```

---

## Request Flow — Chat Message

The following sequence shows how a Telegram message is processed end-to-end.

```mermaid
sequenceDiagram
    participant U as User (Telegram)
    participant TC as TelegramConnector
    participant SM as SessionManager
    participant CLI as AI CLI Runtime
    participant TG as Telegram API

    U->>TG: sends message
    TC->>TG: getUpdates (long poll)
    TG-->>TC: message payload
    TC->>TC: ACL check\n(whitelist / pinned_users)
    TC->>SM: _enforce_pinned_session()\n(writes agent/runtime/model to session map)
    TC->>SM: execute(prompt, session_id)
    SM->>SM: parse_slash_command()
    alt slash command
        SM-->>TC: command response
    else AI prompt
        SM->>CLI: subprocess (copilot/claude/etc.)
        CLI-->>SM: stdout response
        SM->>SM: strip_metadata()
        SM-->>TC: cleaned response
    end
    TC->>TC: sanitize_telegram_html()
    TC->>TG: sendMessage (HTML)
    TG-->>U: message delivered
```

---

## Request Flow — Web UI (Streaming)

Chat messages from the Web UI use the SSE streaming endpoint. The browser displays a live bubble with a blinking cursor while the AI CLI is running.

```mermaid
sequenceDiagram
    participant B as Browser
    participant FA as FastAPI (/api/v1)
    participant AM as AuthManager
    participant SM as SessionManager
    participant HM as HistoryManager
    participant CLI as AI CLI Runtime

    B->>FA: GET /api/v1/config  (no auth)
    FA-->>B: {"scheduler_enabled": true|false}
    Note over B: Hide scheduler tab if disabled

    B->>FA: POST /api/v1/auth/request-pairing\n{identity, channel}
    FA->>AM: generate_pairing_code()
    AM-->>B: 200 OK (code sent via Telegram/WebEx)

    B->>FA: POST /api/v1/auth/verify\n{code, identity}
    FA->>AM: verify_pairing_code()
    AM-->>B: 200 {session_token}

    B->>FA: POST /api/v1/sessions\nAuthorization: Bearer <token>
    FA-->>B: 200 {session_id}

    B->>FA: POST /api/v1/sessions/{id}/stream\n{query}  (text/event-stream)
    FA->>SM: _register_stream(session_id, queue, loop)
    FA-->>B: event: start  →  browser creates live bubble

    FA->>SM: execute(query, session_id)  [thread]
    SM->>CLI: subprocess (Popen)

    loop stdout lines
        CLI-->>SM: line
        SM->>SM: queue.put_nowait(("chunk", line))
        FA-->>B: event: chunk {"text": line}
        Note over B: Appends text to bubble
    end

    Note over FA,B: keepalive comment every 1s if no chunks

    CLI-->>SM: exit
    SM->>SM: queue.put_nowait(("done", ""))
    SM-->>FA: full stripped response
    FA->>SM: _unregister_stream(session_id)
    FA->>HM: append_message() x2 (user + assistant)
    FA-->>B: event: done {"response":"…","runtime":"…","model":"…"}
    Note over B: Replace raw text with rendered markdown
```

---

## Task Scheduler Flow

```mermaid
sequenceDiagram
    participant U as User (Telegram/WebEx/WebUI)
    participant FA as FastAPI (/api/v1/scheduler)
    participant TS as TaskScheduler
    participant SM as SessionManager
    participant CLI as AI CLI Runtime
    participant N as Notification Channel

    U->>FA: POST /api/v1/scheduler/jobs\n{name, schedule, agent, task, notify:true}
    FA->>FA: _require_scheduler_auth()\n(ACL check)
    FA->>TS: schedule_task(created_by={identity,channel})
    TS-->>FA: {success, job_id}
    FA-->>U: 200 {job_id}

    Note over TS: time passes — next_run reached

    TS->>SM: execute(task, scheduler_session_id)
    SM->>CLI: subprocess
    CLI-->>SM: result
    SM-->>TS: result text
    TS->>TS: store result in results/
    TS->>N: send notification to creator\n(Telegram or WebEx)
    N-->>U: "✅ Job 'Daily standup' completed:\n<result snippet>"

    U->>FA: GET /api/v1/scheduler/jobs/{id}/results
    FA->>TS: get_results(job_id)
    TS-->>FA: results[]
    FA-->>U: 200 {results}
```

---

## Authentication Flow (Web UI Pairing)

```mermaid
flowchart LR
    A([Browser]) -->|1. POST request-pairing\nidentity + channel| B[FastAPI]
    B -->|2. generate_pairing_code| C[AuthManager]
    C -->|3. 6-digit code stored\nTTL=300s| C
    C -->|4. deliver code via\nTelegram or WebEx| D([User phone])
    D -->|5. enters code\nin browser| A
    A -->|6. POST verify\ncode + identity| B
    B -->|7. verify_pairing_code| C
    C -->|8. valid → issue\nsession token TTL=3600s| C
    C -->|9. Bearer token| A
    A -->|10. all subsequent requests\nAuthorization: Bearer token| B
```

---

## Component Reference

### `SessionManager` (`agent_manager.py`)

The central execution engine. Responsible for:

- Loading `agents.json` and resolving agent paths
- Maintaining session state files (`n8n-session-map.json`)
- Parsing and executing slash commands (`/agent`, `/runtime`, `/model`, `/session`, `/status`, `/cancel`, `/mode`)
- Dispatching AI prompts to the selected runtime subprocess
- **Streaming**: maintaining `_stream_queues` (per-session `asyncio.Queue` registry); pushing stdout chunks to the SSE endpoint in real-time via `loop.call_soon_threadsafe()`
- Tracking running queries (PID, runtime, agent, output snippet)
- Stripping CLI metadata (thinking tags, token counts, banners) from output
- Building per-agent context prompts (skills, repo info, AGENTS.md)

**Key methods:**

| Method | Purpose |
|--------|---------|
| `execute()` | Entry point — parse slash command or dispatch to AI |
| `run_copilot()` / `run_claude()` / `run_gemini()` / `run_opencode()` / `run_codex()` | Runtime-specific subprocess wrappers |
| `_execute_subprocess_with_tracking()` | Dual-path: streaming (queue-based line-by-line) or blocking (`communicate()`) |
| `_register_stream()` / `_unregister_stream()` | Register/remove per-session SSE queue |
| `set_agent()` | Switch agent and update session state |
| `update_session_field()` | Write a single field (model, runtime, mode…) to the session map |
| `strip_metadata()` | Clean raw CLI stdout |
| `track_running_query()` / `clear_running_query()` | PID-based query lifecycle |

---

### `HistoryManager` (`agent_manager.py`)

Stores per-user, per-channel chat history in `~/.copilot/chat-history.json`.

- Keyed by `channel:identity` (e.g. `telegram:8193231291`, `webui:alice@example.com`)
- Each key holds a list of session objects, each containing a `messages` array
- Used by the Web UI to populate the sidebar session list and restore conversation context

---

### `AuthManager` (`agent_manager.py`)

Handles Web UI authentication:

- `generate_pairing_code(identity, channel)` — creates a time-limited 6-digit code and triggers delivery via the appropriate connector
- `verify_pairing_code(code, identity)` — validates the code and returns a signed session token (JWT-like dict stored in memory)
- `validate_session_token(token)` — verifies a Bearer token; returns identity+channel or None
- `validate_shared_key(key)` — server-to-server authentication via `API_SHARED_KEY`

---

### `RateLimiter` (`agent_manager.py`)

Per-IP sliding-window rate limiter for all FastAPI endpoints. Default limits enforced per endpoint path to protect against abuse.

---

### `TaskScheduler` (`task_scheduler.py`)

Embedded cron-like scheduler:

- Persists jobs in `/opt/.task-scheduler/jobs.json` (path configurable via env vars)
- `schedule_task()` — create a job with natural-language schedule, returns `job_id`
- `update_job()` — patch any job field
- `pause_job()` / `resume_job()` — toggle enabled state
- `get_results(job_id, limit)` — returns last N execution results
- `doctor()` — health check report for the daemon status badge
- Parses human schedules: `in X minutes/hours/days`, `every X hours`, `every day at HH:MM`

---

### `TelegramConnector` (`telegram_connector.py`)

Long-polling Telegram bot:

- Polls `getUpdates` in a loop; handles text, photos, documents
- Per-user whitelist/blacklist (`allowed_users` / `denied_users`)
- `pinned_users` — locks user to an agent/runtime/model before every query
- `yolo_allowed_users` — restricts `/mode yolo` to listed IDs
- Calls `SM.execute()` and formats response as Telegram HTML (entities sanitized)
- `send_file()` / `download_file()` for file/image attachments

---

### `WebEXConnector` (`webex_connector.py`)

Cisco WebEx connector (supports both webhook and RabbitMQ modes):

- Listens on RabbitMQ queue (`webex` prod / `webex-dev` dev) or HTTP webhook
- Same per-user ACL as Telegram (`pinned_users`, `yolo_allowed_users`)
- `send_file()` — uploads local file back to WebEx room
- `download_file()` — downloads attachment from WebEx to `webex_downloads/`
- Automatic temp-file cleanup (files >5 min old)

---

### FastAPI App (`create_api_app()` in `agent_manager.py`)

Factory function that builds the ASGI application. Endpoints grouped by prefix:

| Prefix | Purpose |
|--------|---------|
| `/api/v1/health` | Health check |
| `/api/v1/config` | **Public** feature-flag endpoint (no auth); returns `{scheduler_enabled}` |
| `/api/v1/auth/` | Pairing code request and verification |
| `/api/v1/sessions/` | Session create, execute (blocking), status, delete |
| `/api/v1/sessions/{id}/stream` | **SSE streaming** execute — WebUI chat path |
| `/api/v1/history/` | Per-user session history |
| `/api/v1/sessions/{id}/upload` | File uploads |
| `/api/v1/uploads/` | Serve uploaded files |
| `/api/v1/search/images` | DuckDuckGo image proxy |
| `/api/v1/scheduler/` | Task scheduler CRUD and results (only registered when `SCHEDULER_ENABLED=true`) |
| `/ai-media/` | AI-fetched images (static, no auth) |
| `/ui` | Web UI SPA (static, no auth) |

---

## Data Stores

| Store | Path | Description |
|-------|------|-------------|
| Session map | `~/.copilot/n8n-session-map.json` | Maps N8N/API session IDs to runtime session IDs, agent, model, runtime, mode |
| Running queries | `~/.copilot/running-queries.json` | Active query PIDs and metadata (for `/status` / `/cancel`) |
| Chat history | `~/.copilot/chat-history.json` | Per-user, per-channel conversation history for Web UI |
| Scheduler jobs | `/opt/.task-scheduler/jobs.json` | All scheduled job definitions |
| Scheduler results | `/opt/.task-scheduler/results/` | Per-job execution result files |
| Scheduler logs | `/opt/.task-scheduler/logs/` | Per-job execution log files |
| WebEx downloads | `webex_downloads/` | Temporary incoming file attachments |
| AI media | `/tmp/webui_ai_media/` | Images fetched by AI agents for inline display |

---

## Deployment Topology

Two environments run side-by-side on the CLI-Tools host:

```
┌────────────────────────────────────────────────────┐
│              CLI-Tools Host (Linux)                  │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  PRODUCTION   /opt/n8n-copilot-shim          │   │
│  │  Branch: main                                 │   │
│  │  webex-connector.service                      │   │
│  │  telegram-bot-listener.service                │   │
│  │  agent-manager-api.service   (port 8000)      │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  DEVELOPMENT  /opt/n8n-copilot-shim-dev      │   │
│  │  Branch: dev                                  │   │
│  │  webex-connector-dev.service                  │   │
│  │  telegram-bot-listener-dev.service            │   │
│  │  agent-manager-api-dev.service  (port 8001)   │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  RabbitMQ  192.168.0.85:5672                        │
│  Queue: webex (prod) / webex-dev (dev)              │
└────────────────────────────────────────────────────┘
```

Service files live in the repo and are deployed to `/etc/systemd/system/`. All services run as the `flipkey` user and source environment from `.env` via `EnvironmentFile=`.

---

## Environment Variables

Key variables (see `.env.example` for the full list):

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `PROD` | `DEV` enables debug logging and relaxes some validations |
| `API_PORT` | `8000` | Port for the FastAPI server |
| `API_SHARED_KEY` | — | Shared secret for server-to-server API auth |
| `PAIRING_CODE_LENGTH` | `6` | Digits in the one-time pairing code |
| `PAIRING_CODE_TTL` | `300` | Seconds before a pairing code expires |
| `SESSION_TOKEN_TTL` | `3600` | Seconds before a Web UI session token expires |
| `AGENT_CONFIG_FILE` | — | Path to `agents.json` (overrides auto-discovery) |
| `COPILOT_DEFAULT_AGENT` | `orchestrator` | Default agent for new sessions |
| `COPILOT_DEFAULT_MODEL` | `gpt-5-mini` | Default model for new sessions |
| `COPILOT_DEFAULT_RUNTIME` | `copilot` | Default runtime for new sessions |
| `SCHEDULER_JOBS_FILE` | `/opt/.task-scheduler/jobs.json` | Scheduler jobs persistence |
| `SCHEDULER_LOGS_DIR` | `/opt/.task-scheduler/logs/` | Scheduler log directory |
| `SCHEDULER_RESULTS_DIR` | `/opt/.task-scheduler/results/` | Scheduler results directory |
| `SCHEDULER_ALLOWED_TELEGRAM` | `vtflip` | Comma-separated Telegram usernames allowed to manage scheduler |
| `SCHEDULER_ALLOWED_WEBEX` | `flipkey@cisco.com` | Comma-separated WebEx emails allowed to manage scheduler |
| `SCHEDULER_ENABLED` | `true` | Set to `false` to disable all scheduler routes and hide the Scheduler tab in the Web UI |

---

## Feature Flags

Feature flags are read from the environment at startup and exposed to the browser via the public `GET /api/v1/config` endpoint. Because the endpoint requires no authentication, the browser can read it before the login screen appears.

| Flag | Env Var | Default | Effect |
|------|---------|---------|--------|
| Scheduler | `SCHEDULER_ENABLED` | `true` | When `false`: all `/api/v1/scheduler/*` routes are not registered (404); `GET /api/v1/config` returns `{"scheduler_enabled": false}`; browser hides the Scheduler tab before the app view is shown |

### Config Boot Flow

```mermaid
sequenceDiagram
    participant B as Browser
    participant FA as FastAPI

    Note over B: DOMContentLoaded fires
    B->>FA: GET /api/v1/config  (no auth, no token)
    FA-->>B: {"scheduler_enabled": false}
    Note over B: STATE.schedulerEnabled = false
    B->>B: loadAuth() → showAppView()
    Note over B: showAppView() hides Scheduler tab\nbefore anything is visible to user
```

### Adding a New Feature Flag

1. Add the env-var read in `agent_manager.py` near the top of `create_api_app()`:
   ```python
   MY_FLAG = os.environ.get("MY_FLAG", "true").lower() not in ("false", "0", "no")
   ```
2. Add it to the `GET /api/v1/config` response dict.
3. In `app.js`, read `config.my_flag` inside the config fetch in `DOMContentLoaded` and store in `STATE`.
4. Apply visibility in `showAppView()` (runs before UI is shown).
5. Document in `.env.example` and this file.

