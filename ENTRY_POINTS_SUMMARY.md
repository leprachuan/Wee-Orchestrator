# N8N Copilot Shim: Main Entry Points & Architecture

## 1. agent_manager.py (6414 lines)

### SessionManager Class (line 759+)
**Main class managing all session, model, and runtime logic**

#### Key Methods:
- `__init__()` [line 874] - Initialize paths, executables, agents, models
- `execute(prompt, session_id)` [line 3386] - Main execution dispatcher
- `get_model_from_name(name, runtime)` [line 1685] - Model alias resolution
- `fetch_copilot_models()` [line 1135] - CLI model introspection
- `fetch_opencode_models()` [line 1211] - OpenCode CLI model fetch
- `run_copilot(prompt, model, agent, ...)` [line 2828] - Execute Copilot CLI
- `run_claude(prompt, model, agent, ...)` [line 2979] - Execute Claude CLI
- `run_opencode(prompt, model, agent, ...)` [line 2916] - Execute OpenCode CLI
- `run_gemini(prompt, model, agent, ...)` [line 3055] - Execute Gemini CLI
- `run_codex(prompt, model, agent, ...)` [line 3124] - Execute CODEX CLI

#### Session Management Methods:
- `get_or_create_session_data(session_id)` - Get/initialize session
- `update_session_field(session_id, field, value)` - Persist model/runtime changes
- `load_session_map()` / `save_session_map()` - JSON persistence
- `session_exists(session_id, runtime)` - Check session state

#### Model Methods:
- `get_model_from_name()` - Resolve alias to model ID
- `fetch_copilot_models()` - Dynamic model listing
- `fetch_opencode_models()` - Dynamic model listing
- Static model dicts: CLAUDE_MODELS, GEMINI_MODELS, CODEX_MODELS, OPENCODE_MODELS

### FastAPI Application Setup (line ~4400+)

#### Key Routes:
- `POST /api/v1/sessions/create` [line 4830] - Create new session
- `POST /api/v1/sessions/{session_id}/execute` [line 4830] - Execute prompt
- `GET /api/v1/models` [line 4695] - List models for runtime
- `GET /api/v1/status` - System status
- `POST /api/v1/query/cancel` - Cancel running query

#### Authentication:
- `AuthManager` class [line 56] - Manages pairing codes and session tokens
- `RateLimiter` class [line 27] - Per-IP rate limiting

### Command Line Interface (line ~6200+)

#### Main Entry Point:
```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-models", help="List all available models")
    parser.add_argument("--api", action="store_true", help="Run as FastAPI server")
    parser.add_argument("--listen", default="0.0.0.0:8001")
    
    args = parser.parse_args()
```

#### Usage:
```bash
# Run as API server
python agent_manager.py --api --listen 0.0.0.0:8001

# List models for current runtime
python agent_manager.py --list-models

# Execute single prompt
python agent_manager.py "your prompt here"
```

---

## 2. telegram_connector.py (1309 lines)

### TelegramConfig Class (line 34+)
Manages Telegram-specific configuration

#### Methods:
- `_load_config()` [line 41] - Load telegram_config.json
- `get_user_session()` [line 79] - Get user's session info
- `set_user_session()` [line 83] - Save user's session info
- `get_pinned_model()` [line 132] - Get model pinned to user
- `is_yolo_allowed()` [line 139] - Check YOLO permission

### TelegramConnector Class (line 148+)
Main Telegram integration

#### Key Methods:
- `__init__(token, config_file)` [line 151] - Initialize bot
- `get_session_manager(session_id)` [line 199] - Get/create SessionManager
- `_enforce_pinned_session(user_id, session_id)` [line 221] - Enforce admin pinning
- `send_message(chat_id, text)` - Send response
- `handle_message(user_id, chat_id, text)` [line ~950] - Message dispatcher
- `start_polling()` - Main polling loop

#### Message Handler Flow:
```
handle_message()
  ├─ Check if slash command
  ├─ Enforce pinned agent/runtime/model
  ├─ Route to SessionManager.execute()
  ├─ Check for blocking commands (/model set when pinned)
  └─ Send response back
```

### Main Entry Point (line ~1250+)
```python
if __name__ == "__main__":
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    connector = TelegramConnector(token)
    connector.start_polling()
```

---

## 3. webex_connector.py (~1400 lines)

### WebEXConfig Class (line 29+)
Similar to TelegramConfig but for WebEX

#### Key Methods:
- `get_pinned_model(person_id)` [line ~130] - Get pinned model
- Same methods as TelegramConfig

### WebEXConnector Class (line ~150+)
Similar to TelegramConnector but uses RabbitMQ instead of polling

#### Key Methods:
- `__init__(token, config_file)` - Initialize with RabbitMQ config
- `_enforce_pinned_session(person_id, session_id)` - Enforce pinning
- `process_message(message)` - Process RabbitMQ message
- `start_listener()` - Main RabbitMQ listener loop

#### RabbitMQ Integration:
- Connects to RabbitMQ queue (configured in webex_config.json)
- Processes messages from queue
- Sends responses via WebEX API

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    N8N Copilot Shim                         │
└─────────────────────────────────────────────────────────────┘

┌────────────────────────────────┬────────────────────────────┐
│      agent_manager.py          │   Telegram/WebEX Connectors│
│      (Core Logic)              │                            │
├────────────────────────────────┼────────────────────────────┤
│ • SessionManager class         │ • TelegramConnector        │
│ • FastAPI routes               │ • TelegramConfig           │
│ • Model definitions            │ • WebEXConnector           │
│ • CLI integration              │ • WebEXConfig              │
│ • Session persistence          │ • RabbitMQ listener        │
└────────────────────────────────┴────────────────────────────┘
         ↓                               ↓
    ┌─────────────────────────────────────────────┐
    │     Runtimes (Subprocess execution)         │
    ├─────────────────────────────────────────────┤
    │ • Copilot CLI (/usr/bin/copilot)           │
    │ • Claude CLI (/usr/bin/claude)             │
    │ • OpenCode CLI (/usr/local/bin/opencode)   │
    │ • Gemini CLI (gemini)                      │
    │ • CODEX CLI (codex)                        │
    └─────────────────────────────────────────────┘
         ↓
    ┌─────────────────────────────────────────────┐
    │     Model Listing Layer                     │
    ├─────────────────────────────────────────────┤
    │ • Static: CLAUDE_MODELS, GEMINI_MODELS, ... │
    │ • Dynamic: fetch_copilot_models()           │
    │ • Dynamic: fetch_opencode_models()          │
    │ • Resolution: get_model_from_name()         │
    └─────────────────────────────────────────────┘
         ↓
    ┌─────────────────────────────────────────────┐
    │     Session & Persistence Layer             │
    ├─────────────────────────────────────────────┤
    │ • JSON session maps                         │
    │ • Model per-user pinning                    │
    │ • Session state directories                 │
    └─────────────────────────────────────────────┘
```

---

## Data Flow: /model set command

```
User sends: /model set sonnet
    ↓
telegram_connector.handle_message()
    ├─ Detect slash command
    ├─ Check if user pinned → block if true
    └─ _enforce_pinned_session()
    ↓
SessionManager.execute("/model set sonnet", session_id)
    ├─ parse_slash_command() → ("/model", "set sonnet")
    ├─ Model handler: extract "sonnet"
    ├─ get_model_from_name("sonnet", "claude")
    │   └─ Check CLAUDE_MODELS → found: "sonnet"
    ├─ update_session_field(session_id, "model", "sonnet")
    │   └─ Load session map JSON
    │   └─ Update model field
    │   └─ Save to disk
    └─ Return: "✓ Switched to model `sonnet`"
    ↓
telegram_connector.send_message(chat_id, response)
    ↓
On next user prompt:
SessionManager.execute(prompt, session_id)
    ├─ get_or_create_session_data(session_id)
    │   └─ Load session map, get model="sonnet"
    └─ run_claude(prompt, "sonnet", agent, ...)
        └─ Subprocess: claude -p "..." --model sonnet
```

---

## Data Flow: /api/v1/models API call

```
Client: GET /api/v1/models?runtime=claude
    ↓
FastAPI route: get_models(runtime="claude")
    ├─ Extract runtime parameter
    ├─ Iterate CLAUDE_MODELS
    │   └─ Build: [{"id": "sonnet", "label": "Claude Sonnet (Latest)"}, ...]
    └─ Return JSON
    ↓
Response:
{
  "runtime": "claude",
  "models": [
    {"id": "sonnet", "label": "Claude Sonnet (Latest)"},
    {"id": "haiku", "label": "Claude Haiku (Latest)"},
    ...
  ]
}
```

---

## File Structure Overview

```
/opt/n8n-copilot-shim-dev/
├── agent_manager.py              [6414 lines] Core engine
├── telegram_connector.py          [1309 lines] Telegram integration
├── webex_connector.py             [~1400 lines] WebEX integration
├── agents.json                    Agent definitions
├── telegram_config.json           Telegram config
├── webex_config.json              WebEX config
├── opencode.example.json          OpenCode example config
├── requirements.txt               Python dependencies
│
├── tests/
│   ├── test_agent_manager.py     [1259 lines] Unit tests
│   ├── test_api_endpoints.py     API route tests
│   ├── test_api_auth.py          Auth tests
│   └── ...
│
├── scheduler/                     Task scheduler
├── docs/                          Documentation
└── static/                        Web UI assets
```

---

## Configuration Priority

### Model Selection:
1. User's session data (from /model set)
2. Pinned model (admin override)
3. Default model (from config)
4. Hardcoded default: `"gpt-5-mini"`

### Runtime Selection:
1. User's session data (from /runtime set)
2. Pinned runtime (admin override)
3. Default runtime (from config)
4. Hardcoded default: `"copilot"`

### Agent Selection:
1. User's session data (from /agent set)
2. Pinned agent (admin override)
3. Default agent (from config)
4. Hardcoded default: `"orchestrator"`

---

## Error Handling & Fallbacks

### Model Resolution Failures:
```
get_model_from_name("invalid-model", "claude")
  → Check static CLAUDE_MODELS → Not found
  → Fetch from CLI (if not claude) → Not found
  → Return None
  → Handler shows error: "Unknown model 'invalid-model' for runtime claude"
```

### CLI Execution Failures:
```
run_copilot(prompt, model, agent, ...)
  → Check copilot_bin exists
  → If not: return "Error: Copilot executable not found"
  → subprocess.run() with timeout
  → If timeout: return "Error: Command timed out"
  → If error: return "Error: CLI command failed"
```

### Model Listing Failures:
```
/model list when copilot not found
  → execute() → /model list handler
  → Try fetch_copilot_models()
  → Returns {} (empty dict)
  → Handler shows: "❌ No models available. Check that Copilot CLI is properly configured."
```

---

## Testing

### Run All Tests:
```bash
cd /opt/n8n-copilot-shim-dev
python -m pytest tests/ -v
```

### Run with Real Runtimes (requires CLIs installed):
```bash
export TEST_WITH_RUNTIMES=1
python -m pytest tests/ -v
```

### Run Specific Test:
```bash
python -m pytest tests/test_agent_manager.py::TestModelResolution -v
```

---

## Deployment

### As SystemD Services:
```bash
# API Server
sudo systemctl start agent-manager-api
sudo systemctl enable agent-manager-api

# Telegram Listener
sudo systemctl start telegram-bot-listener
sudo systemctl enable telegram-bot-listener

# WebEX Listener
sudo systemctl start webex-connector
sudo systemctl enable webex-connector
```

### Direct Execution:
```bash
# Start API server on port 8001
python agent_manager.py --api --listen 0.0.0.0:8001

# Start Telegram bot
python telegram_connector.py

# Start WebEX listener
python webex_connector.py
```

---

## Key Configuration Files

### agents.json
```json
{
  "agents": [
    {
      "name": "orchestrator",
      "description": "Main coordinator agent",
      "path": "/path/to/agent"
    },
    ...
  ]
}
```

### telegram_config.json
```json
{
  "token": "YOUR_BOT_TOKEN",
  "allowed_users": [123456789],
  "default_model": "gpt-5-mini",
  "default_agent": "orchestrator",
  "pinned_users": {
    "123456789": {
      "agent": "devops",
      "model": "sonnet",
      "runtime": "claude"
    }
  }
}
```

---

## Summary

The N8N Copilot Shim is a multi-layered system:

1. **Core Engine** (agent_manager.py): SessionManager handles all execution, model resolution, CLI integration
2. **Connectors** (telegram/webex): Bridge between chat platforms and engine
3. **Models**: Static dicts + dynamic CLI fetching with alias resolution
4. **Runtimes**: Subprocess execution via CLI (copilot, claude, opencode, gemini, codex)
5. **Persistence**: JSON-based session storage with per-user model/runtime configuration
6. **APIs**: FastAPI REST endpoints + CLI interface

Key architectural decision: **Static models for reliability, dynamic fetch for accuracy**, with comprehensive fallback logic at each layer.

