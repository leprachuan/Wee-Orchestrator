# Model Listing Implementation - Complete Documentation Index

This index provides a comprehensive guide to the model listing implementation in the N8N Copilot Shim project.

## 📚 Documentation Files

### 1. **MODEL_LISTING_QUICK_REF.md** ⭐ START HERE
Quick reference guide with:
- Key file locations and line numbers
- Static models summary (Claude, Gemini, CODEX, OpenCode)
- CLI patterns for each runtime
- Common operations & troubleshooting
- **Best for**: Quick lookups, copy-paste patterns

### 2. **MODEL_LISTING_IMPLEMENTATION.md** 📖 COMPLETE GUIDE
Comprehensive implementation details covering:
- Static model definitions (lines 770-872)
- Dynamic model fetching (copilot & opencode CLIs)
- Model resolution & alias matching (lines 1685-1770)
- `/model` slash commands (list/set/current)
- REST API `/api/v1/models` endpoint
- Runtime CLI integration (copilot/claude/opencode/gemini/codex)
- Test coverage
- Session data structure
- Execution flows

### 3. **ENTRY_POINTS_SUMMARY.md** 🏗️ ARCHITECTURE
High-level architecture documentation:
- Main entry points (agent_manager.py, telegram_connector.py, webex_connector.py)
- Class structures and key methods
- Architecture diagram
- Data flow diagrams
- File structure overview
- Configuration priority
- Error handling & fallbacks
- Deployment instructions

### 4. **MODEL_LISTING_INDEX.md** (this file)
Navigation guide for all documentation

---

## 🎯 Quick Navigation by Task

### I want to...

**...understand the overall architecture**
→ Read: ENTRY_POINTS_SUMMARY.md (section "Architecture Diagram")

**...know what models are available**
→ Read: MODEL_LISTING_QUICK_REF.md (section "Static Models Summary")

**...find where model X is defined**
→ Use: MODEL_LISTING_QUICK_REF.md (section "Quick Code Locations")

**...understand how /model set works**
→ Read: MODEL_LISTING_IMPLEMENTATION.md (section "4. /MODEL SLASH COMMAND")
→ Or: MODEL_LISTING_QUICK_REF.md (section "Common Operations")

**...find API documentation for /api/v1/models**
→ Read: MODEL_LISTING_IMPLEMENTATION.md (section "5. /API/V1/MODELS REST ENDPOINT")

**...add a new model to the system**
→ Read: MODEL_LISTING_QUICK_REF.md (section "Files to Modify When Adding Models")

**...understand model resolution algorithm**
→ Read: MODEL_LISTING_IMPLEMENTATION.md (section "3. MODEL RESOLUTION & ALIASING")

**...find test files**
→ Read: MODEL_LISTING_IMPLEMENTATION.md (section "8. TEST COVERAGE")
→ Files: `/opt/n8n-copilot-shim-dev/tests/test_agent_manager.py:475-533`

**...troubleshoot a model issue**
→ Read: MODEL_LISTING_QUICK_REF.md (section "Troubleshooting")

**...understand Telegram model pinning**
→ Read: MODEL_LISTING_IMPLEMENTATION.md (section "7. TELEGRAM & WEBEX CONNECTORS")

---

## 📍 Code Locations Quick Map

### Model Definitions
```
CLAUDE_MODELS        agent_manager.py:770-793
OPENCODE_MODELS      agent_manager.py:795-805
GEMINI_MODELS        agent_manager.py:809-850
CODEX_MODELS         agent_manager.py:853-872
```

### Key Functions
```
SessionManager.__init__        agent_manager.py:874-940
get_model_from_name()          agent_manager.py:1685-1770
fetch_copilot_models()         agent_manager.py:1135-1209
fetch_opencode_models()        agent_manager.py:1211-1256
execute()                      agent_manager.py:3386-3400
/model list handler            agent_manager.py:3691-3745
/model set handler             agent_manager.py:3751-3757
/api/v1/models endpoint        agent_manager.py:4695-4742
run_copilot()                  agent_manager.py:2828-2914
run_claude()                   agent_manager.py:2979-3053
run_opencode()                 agent_manager.py:2916-2977
run_gemini()                   agent_manager.py:3055-3122
run_codex()                    agent_manager.py:3124-3215
```

### Telegram Integration
```
TelegramConfig.get_pinned_model()    telegram_connector.py:132-137
TelegramConnector._enforce_pinned()  telegram_connector.py:221-236
/model set blocking logic            telegram_connector.py:1042-1047
```

### Tests
```
TestModelResolution              tests/test_agent_manager.py:475-533
test_get_claude_model_by_alias   tests/test_agent_manager.py:494-500
test_get_copilot_model_*_match   tests/test_agent_manager.py:512-532
```

---

## 🔑 Key Concepts

### Static vs Dynamic Models
- **Static**: CLAUDE_MODELS, GEMINI_MODELS, CODEX_MODELS, OPENCODE_MODELS (hard-coded)
- **Dynamic**: fetch_copilot_models(), fetch_opencode_models() (CLI introspection)
- **Strategy**: Check static first (fast), fall back to dynamic (accurate)

### Model Resolution
- **Input**: User-provided model name/alias (e.g., "sonnet")
- **Process**: Alias matching → substring matching → fallback to None
- **Output**: Full model ID (e.g., "sonnet") to pass to CLI

### Session Persistence
- **Storage**: JSON file (~/.copilot/n8n-session-map.json)
- **Key**: n8n_session_id
- **Contains**: model, runtime, agent, timeout, mode, render_type
- **Lifetime**: Per-user, survives across messages

### Model Pinning
- **What**: Admin can lock user to specific model
- **Where**: telegram_config.json or webex_config.json
- **How**: _enforce_pinned_session() called before each command
- **Effect**: User cannot change model with /model set

---

## 🧪 Testing

### Model Resolution Tests (475-533 lines)
- `test_get_claude_model_by_alias()` - Alias matching
- `test_get_claude_model_by_full_name()` - Full name matching
- `test_get_invalid_model()` - Error handling
- `test_get_copilot_model_exact_match()` - Exact matching
- `test_get_copilot_model_substring_match()` - Substring matching

### Run Tests
```bash
# All tests
python -m pytest tests/test_agent_manager.py -v

# Specific class
python -m pytest tests/test_agent_manager.py::TestModelResolution -v

# With real runtimes
export TEST_WITH_RUNTIMES=1
python -m pytest tests/test_agent_manager.py -v
```

---

## 🚀 Common Operations

### User Command: /model set sonnet
1. Parse slash command → ("/model", "set sonnet")
2. Extract model name → "sonnet"
3. Resolve to ID → get_model_from_name("sonnet", "claude") → "sonnet"
4. Save to session → update_session_field(session_id, "model", "sonnet")
5. Next prompt uses → claude -p "..." --model sonnet

### User Command: /model list
1. Execute /model list handler
2. Get current runtime from session
3. If static model runtime → iterate model dict
4. If dynamic (copilot/opencode) → fetch from CLI
5. Format as markdown with categories
6. Send to user

### API Call: GET /api/v1/models?runtime=claude
1. Extract runtime parameter
2. Iterate CLAUDE_MODELS dictionary
3. Build list: [{"id": model_id, "label": display_name}, ...]
4. Return JSON with models

---

## 📋 CLI Invocation Patterns

### Copilot
```bash
copilot -p "PROMPT" --allow-all-tools --model MODEL [--resume SESSION] [--yolo]
```

### Claude
```bash
claude -p "PROMPT" --permission-mode MODE --model MODEL [--resume SESSION]
```

### OpenCode
```bash
opencode run --model MODEL "PROMPT" [--session SESSION]
```

### Gemini
```bash
gemini "PROMPT" [--yolo] [--resume SESSION]
```
Note: No --model flag support yet

### CODEX
```bash
codex exec "PROMPT" [--dangerously-bypass-approvals-and-sandbox]
codex exec resume SESSION "PROMPT" [--dangerously-bypass-approvals-and-sandbox]
```

---

## 🔍 Model Count Summary

| Runtime | Static Count | Variants | Total | Dynamic Support |
|---------|--------------|----------|-------|-----------------|
| Claude  | 6            | 0        | 6     | ❌ No          |
| Gemini  | 12           | 0        | 12    | ❌ No          |
| CODEX   | 14           | 0        | 14    | ❌ No          |
| OpenCode| 5            | Varies   | 5+    | ✅ Yes (CLI)   |
| Copilot | 0            | N/A      | N/A   | ✅ Yes (CLI)   |

---

## 🏗️ Architecture Layers

```
Layer 1: User Interface
  ├─ Telegram Bot (/model set, /model list commands)
  ├─ WebEX Connector (/model set, /model list commands)
  └─ REST API (/api/v1/models endpoint)

Layer 2: Command Parsing & Session Management
  ├─ parse_slash_command() → extract command & args
  ├─ get_or_create_session_data() → load user's model
  └─ execute() → dispatch to handler

Layer 3: Model Resolution
  ├─ Static model dicts (CLAUDE_MODELS, etc.)
  ├─ get_model_from_name() → resolve alias to ID
  ├─ fetch_copilot_models() → CLI introspection
  └─ fetch_opencode_models() → CLI introspection

Layer 4: Session Persistence
  ├─ update_session_field() → save model to disk
  ├─ load_session_map() → restore user session
  └─ Model pinning via telegram/webex config

Layer 5: Runtime Execution
  ├─ run_copilot() → subprocess with --model flag
  ├─ run_claude() → subprocess with --model flag
  ├─ run_opencode() → subprocess with --model flag
  ├─ run_gemini() → subprocess (no --model yet)
  └─ run_codex() → subprocess
```

---

## 🔗 Cross-References

### If you need to understand...

**How models are stored** → See: session.md, ENTRY_POINTS_SUMMARY.md "Session Data Structure"

**How model aliases work** → See: MODEL_LISTING_IMPLEMENTATION.md "3. MODEL RESOLUTION & ALIASING"

**How CLI commands are built** → See: ENTRY_POINTS_SUMMARY.md "Data Flow" + CLI patterns

**How to add a new model** → See: MODEL_LISTING_QUICK_REF.md "Files to Modify When Adding Models"

**How pinning works** → See: MODEL_LISTING_IMPLEMENTATION.md "7. TELEGRAM & WEBEX CONNECTORS"

**How tests work** → See: MODEL_LISTING_IMPLEMENTATION.md "8. TEST COVERAGE"

---

## 📝 File Locations

All documentation files are in the repository root:
- `/opt/n8n-copilot-shim-dev/MODEL_LISTING_IMPLEMENTATION.md` - Complete guide (875 lines)
- `/opt/n8n-copilot-shim-dev/MODEL_LISTING_QUICK_REF.md` - Quick reference
- `/opt/n8n-copilot-shim-dev/ENTRY_POINTS_SUMMARY.md` - Architecture & entry points
- `/opt/n8n-copilot-shim-dev/MODEL_LISTING_INDEX.md` - This file

---

## 🎓 Learning Path

### Beginner (5-10 min)
1. Read MODEL_LISTING_QUICK_REF.md (Static Models Summary)
2. Read MODEL_LISTING_QUICK_REF.md (CLI Patterns)

### Intermediate (30-45 min)
1. Read ENTRY_POINTS_SUMMARY.md (sections 1-3)
2. Read MODEL_LISTING_QUICK_REF.md (all sections)
3. Skim MODEL_LISTING_IMPLEMENTATION.md (sections 1-3)

### Advanced (2-3 hours)
1. Read all of ENTRY_POINTS_SUMMARY.md
2. Read all of MODEL_LISTING_IMPLEMENTATION.md
3. Review agent_manager.py code at key locations
4. Run tests to understand behavior

### Expert (full day)
1. Read all documentation
2. Study agent_manager.py code (6414 lines)
3. Study telegram_connector.py code (1309 lines)
4. Study webex_connector.py code (~1400 lines)
5. Study test_agent_manager.py code (1259 lines)
6. Trace data flows end-to-end
7. Modify code to add a new model or runtime

---

Generated: 2025
For N8N Copilot Shim Project
Model Listing Implementation Documentation
