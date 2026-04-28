## [Issue #190] Bug Fix: Copilot Session Auto-Recovery on Token Expiry
**Status:** ✅ QA Approved (Commit: 2e7f1941, PR #201)

### Summary
Fixed unconditional crash of background tasks when the Copilot runtime's session-level bearer token expires after ~30 minutes. Implemented both **proactive** (age-based pre-emptive restart before expiry) and **reactive** (error-detected mid-session restart with context injection) recovery strategies so long-running tasks continue seamlessly rather than failing.

### Root Cause
The GitHub Copilot API issues a short-lived session-level bearer token (~30 min TTL) separate from the user's OAuth token. The Copilot CLI does not auto-refresh this token mid-conversation. Tasks running longer than ~30 minutes (common for `wee-dev` and `wee-qa` workflows involving SSH latency) were hitting `Session token expired` and the entire background task crashed with exit code 1, requiring manual redispatch.

### Solution

#### Proactive Restart (Age-Based)
- `_copilot_session_start` dict tracks session start epoch per `n8n_session_id` in `SessionManager.__init__`
- `_COPILOT_SESSION_MAX_AGE_SEC = 1500` (25 min) — restarts before the 30-min TTL
- Before `--resume`, checks session age; if expired, starts a fresh session with reconstructed context prompt injected as the first message

#### Reactive Recovery (Error-Detected)
- Monitors subprocess stdout for `_TOKEN_EXPIRED_MARKER = "Session token expired"`
- On detection: extracts accumulated prior-work context from the partial output
- Relaunches with a fresh `--new` session with context injected — background task continues transparently

#### Double-Expiry Guard
- If the recovery session also hits `Session token expired`, returns best-effort partial output with a warning instead of corrupted or empty content

#### Module-Level Constants
- `_TOKEN_EXPIRED_MARKER`, `_COPILOT_SESSION_MAX_AGE_SEC` moved to module scope
- `_COPILOT_ELEVATED_MODE_INSTRUCTIONS`, `_COPILOT_SANDBOXED_MODE_INSTRUCTIONS` extracted to eliminate 3x duplication
- `# noqa: E501` applied to unavoidably long constant strings; Black formatting applied throughout

#### Session Cleanup
- `_copilot_session_start` entries cleaned up in `_cleanup_stream_buffer` to prevent memory leak in long-running orchestrator processes

### Files Changed
- `agent_manager.py` — Proactive + reactive recovery in `run_copilot()`, session tracking in `SessionManager.__init__`, cleanup in `_cleanup_stream_buffer`, module-level constants (+562 lines, -21 lines)
- `tests/test_issue190_copilot_session_expiry.py` — 12 regression tests covering both recovery paths

### Tests
- 12 regression tests in `test_issue190_copilot_session_expiry.py`:
  - A-series (4): Proactive restart — session age checks, threshold boundary, context injection into `cmd[2]`
  - B-series (8): Reactive recovery — expiry detection, context extraction, fresh session launch, double-expiry guard, `test_203` regression (3 previously-broken tests restored)
- Net new tests across full QA lifecycle: +22
- QA Rounds: 5 total (Rounds 1–4 rejected; Round 5 APPROVED after formatting pass)

### Usage
No configuration required. Recovery is automatic and transparent to callers. Log output will show:
```
[copilot] Session age NNs exceeds 1500s threshold — proactive restart
[copilot] Session token expired detected — reactive recovery initiated
[copilot] Reactive recovery succeeded — continuing task
```

## [Issue #146] Feature: Global Toggle to Suppress Background Task Notifications
**Status:** ✅ QA Approved (Commit: 69f29db, PR #150)

### Summary
Implemented a global notification toggle allowing users to suppress all background task completion notifications across all channels (Telegram, WebEx, WebUI) with a single command. Critical system alerts (heartbeat, crashes) are always delivered regardless of toggle state. Toggle state persists in `~/.copilot/notification_settings.json` and is controlled via `/notifications` slash command or REST API.

### Problem
Users had per-identity notification muting but no global toggle to suppress ALL background task notifications at once. The per-identity system was fragmented and confusing (using a `_global` special identity hack). Users wanted a simple way to mute all non-critical notifications system-wide.

### Root Causes
1. **No single global toggle** — Notification suppression was per-identity only, scattered across multiple code paths
2. **Fragmented state management** — `_global` special identity hack was non-standard and hard to maintain
3. **No persistence** — Notification preferences reset between sessions (settings were in-memory only)
4. **No API endpoint** — Users couldn't programmatically control notification settings
5. **No critical bypass** — No way to guarantee critical alerts always reach users regardless of settings

### Solution

#### Global Settings Persistence
- Added `notification_settings.json` stored in `~/.copilot/` (service user home directory)
- Atomic writes using `tempfile.mkstemp()` + `os.replace()` to prevent corruption
- Thread-safe with `_global_settings_lock` mutex
- Default state: `{"notifications_enabled": true, "updated_at": "2026-04-13T..."}`
- Auto-creates parent directory if missing
- Gracefully falls back to default on corrupt/missing file

#### NotificationManager Enhancements
- `set_global_enabled(bool)` — Enable/disable global toggle
- `is_global_enabled()` — Check current global state
- `get_global_settings()` — Return full settings dict for API responses
- Modified `create_notification()` — Added `is_critical` parameter (True bypasses suppression)
- Non-critical notifications return `None` when globally disabled (fully suppressed)
- Critical notifications always create and deliver regardless of toggle

#### Slash Command Integration
- `/notifications current` — Show global toggle state ("ON" or "OFF")
- `/notifications on` (alias: `/notifications all`) — Enable notifications globally
- `/notifications off` (alias: `/notifications mute`) — Disable notifications globally
- Clear messaging: "Critical alerts (heartbeat, crashes) will still be delivered"

#### REST API Endpoints
- `GET /api/v1/settings/notifications` — Returns `{notifications_enabled: bool, updated_at: string, available: bool}`
- `PUT /api/v1/settings/notifications` — Updates toggle state with body `{notifications_enabled: bool}`
- Both endpoints require Bearer token authentication
- Responses include informative success messages

#### Notification Preference Resolution
Precedence order (first match wins):
1. `body.notify` parameter (explicit override in background task request)
2. Global toggle via `is_global_enabled()` (Issue #146)
3. Per-identity mute (existing feature, still supported)
4. Session default (`notification_preference` field)
5. Hardcoded default (`True`)

Critical notifications skip the global toggle check and always deliver.

### Files Changed
- `notification_manager.py` — Global settings persistence (+71 lines)
- `agent_manager.py` — API endpoints + slash command updates (+55 lines, -65 lines)
- `tests/test_issue146_notification_toggle.py` — 25 regression tests (+248 lines)

### Tests
- 25 new tests covering:
  - Global settings persistence (file I/O, JSON format, corruption handling)
  - `create_notification()` suppression behavior
  - `is_critical` parameter bypasses suppression
  - Slash command `/notifications` variants
  - REST API GET/PUT endpoints
  - Notification preference resolution (precedence order)
  - Thread safety (concurrent access)
  - Edge cases (missing file, corrupt JSON, permission errors)
- Total: 1457 passed, 33 pre-existing failures, 0 regressions

### Usage Examples

**Disable all notifications:**
```
/notifications off
✓ Background task notifications suppressed globally.
Critical alerts (heartbeat, crashes) will still be delivered.
```

**Check current state:**
```
/notifications current
🔔 **Background Notifications:** `ON (all channels)`
```

**Via API:**
```bash
# Check state
curl -k https://127.0.0.1:8000/api/v1/settings/notifications
{"notifications_enabled": true, "updated_at": "2026-04-13T12:58:18Z", "available": true}

# Disable notifications
curl -k -X PUT https://127.0.0.1:8000/api/v1/settings/notifications \
  -H "Content-Type: application/json" \
  -d '{"notifications_enabled": false}'
{"notifications_enabled": false, "message": "Notifications suppressed globally..."}
```

### Non-Blocking Observations
- **M1 (MINOR):** Service user mismatch in docs — `~` resolves to service user's home (`n8n` on dev). Standard Unix behavior, no impact.
- **M2 (MINOR):** No WebUI integration yet — Toggle is only available via slash command or API. WebUI settings panel toggle can be added in a follow-up issue.
- **M3 (MINOR):** Timestamp format not explicitly documented — Uses ISO 8601 with `Z` suffix (UTC). Self-documenting, no impact.

### Design Decisions
1. **Single source of truth** — Global toggle replaces the fragmented `_global` special identity hack
2. **File-based persistence** — Simple, auditable, survives service restarts
3. **Critical bypass** — Ensures critical alerts (heartbeat, crashes) never get suppressed
4. **Graceful degradation** — Defaults to `true` (notifications enabled) on missing/corrupt settings
5. **Thread-safe I/O** — Lock protects concurrent file access; atomic writes prevent corruption


## [Issue #153] Bug Fix: OpenRouter 401 Authentication Error
**Status:** ✅ QA Approved (Commit: 1dae171, PR #154)

### Summary
Fixed silent 401 failures when using OpenRouter models in the wee runtime. Both `agent_manager.py` and `wee_runtime.py` were falling back to the local Ollama API key (`ollama`) as the Bearer token for OpenRouter requests. OpenRouter rejects this with HTTP 401 "Missing Authentication header". The fix adds proper `OPENROUTER_API_KEY` environment variable and keyring resolution, raising a clear error instead of silently using an invalid default.

### Root Cause
Both files used `os.environ.get('OPENROUTER_API_KEY', 'ollama')` — the `'ollama'` default is not a valid OpenRouter Bearer token.

### Solution

#### Key Resolution Order (after fix)
1. `OPENROUTER_API_KEY` (explicit kwarg)
2. `WEE_OPENROUTER_KEY` env var
3. `OPENROUTER_API_KEY` env var
4. keyring: `openrouter_api_key`
5. Raise `ValueError` / `RuntimeError` — never `'ollama'` for OpenRouter

#### agent_manager.py Changes
- `_resolve_openrouter_key()` helper: env var first, then keyring lookup
- `run_openrouter_model()`: uses resolver instead of hardcoded default
- Raises `ValueError` with clear message when no key found

#### wee_runtime.py Changes
- `_get_openrouter_key()` helper: same env var → keyring resolution chain
- OpenRouter request builder: uses helper, raises `RuntimeError` on missing key
- Removed silent `'ollama'` fallback

### Files Changed
- `agent_manager.py` — `_resolve_openrouter_key()`, updated OpenRouter key resolution
- `wee_runtime.py` — `_get_openrouter_key()`, updated OpenRouter request builder
- `tests/test_issue153_openrouter_auth.py` — 16 regression tests

### Tests
- 16 new regression tests covering:
  - Env var key resolution in both files
  - Keyring fallback when env var absent
  - `ValueError`/`RuntimeError` raised when no key found
  - `'ollama'` string rejected as OpenRouter key
  - Key priority ordering
- Total: 1462 passed, 0 regressions

### Non-Blocking Findings
- N-1 (NITPICK): Minor punctuation inconsistency in error message
- N-2 (NITPICK): PR description lists 1445 total pass vs 1462 actual (minor discrepancy, test suite grew between branch and QA)

---

## [Issue #119] Feature: Wire Up OpenRouter in Wee Runtime UI
**Status:** ✅ QA Approved (Commit: 168a958, PR #121)

**Problem:** Free OpenRouter models (`:free` suffix) crash immediately on `429 - Provider returned error` instead of retrying or falling back to alternative models.

**3-Layer Solution:**
1. **Layer 1 — openrouter/free as primary:** Built-in OpenRouter auto-router selects whichever free model is available
2. **Layer 2 — Retry with exponential backoff:** Up to 3 retries on 429 (2s, 5s, 10s). Only retries on 429, not auth errors or 5xx
3. **Layer 3 — Manual fallback chain:** If retries exhausted, iterates through ordered model list from wee_free_models.json

**Added:**
- `_wee_is_free_model()` — detects openrouter/free or :free suffix models
- `_wee_load_free_config()` — loads wee_free_models.json with hardcoded defaults
- `_wee_run_attempt()` — single model attempt with 429 retry loop and backoff
- 429 retry loop in `run_wee_native()` with SSE status messages
- Fallback chain in `run_wee_native()` — iterates alternative free models on 429 exhaustion
- `wee_free_models.json` — configurable fallback chain (11 models) + retry settings
- Full retry/fallback in standalone `wee_runtime.py` — `_call_with_retry()`, `run_with_fallback()`, `is_free_openrouter_model()`, `load_free_model_config()`
- Cleaned up stale files (agent_manager.py.bak, logs/wee_executor.log) and updated .gitignore
- 38 regression tests (test_issue125_429_retry.py)

**Tests:** 37 passed, 1 skipped


## [Issue #123] Bug Fix: Wee Runtime Tool Calling Returns {no response}
**Status:** 🔧 In QA Review (Commit: 6c14696)

**Root Cause:** The `dev` branch `run_wee_native()` made a single streaming API call with NO `tools` parameter. When a model requested a tool call (e.g. `bash` for `df -h`), Ollama returned chunks with `content: ""` and a `tool_calls` delta, but the code only checked `delta.content` and ignored tool calls entirely — resulting in empty output shown as `{no response}` in the WebUI.

**6 Bugs Fixed:**
1. No tool definitions passed to Ollama API (`tools` parameter missing)
2. No tool call detection in streaming response loop
3. No tool execution after detecting tool calls
4. No conversation history persistence (Ollama is stateless)
5. Wrong Ollama port: 11436 → 11434
6. Wrong `build_agent_context_prompt` argument order

**Added:**
- Full agentic tool loop in `run_wee_native()` (max 10 rounds)
- `_wee_execute_tool()` — bash, python, file_read, file_write
- `_wee_load_messages()` / `_wee_save_messages()` — conversation history persistence
- `_wee_augment_system_prompt_with_tools()` — system prompt tool capability declaration
- Wee case in `session_exists()` for message-based session detection
- Full tool-calling support in standalone `wee_runtime.py`
- 41 regression tests (`test_issue123_tool_calling.py`)
- Updated 3 pre-existing tests for port fix compatibility

**Tests:** 1183 passed, 9 skipped (41 new)


## [Issue #126] Feature: Wee Runtime Icon (Robot Leprechaun SVG)
**Status:** ✅ Implemented (Commits: b772237, d0cb1d1)

### Summary
Added a custom robot leprechaun SVG icon for the wee runtime in the WebUI runtime switcher. The icon features a tall top hat with wide brim, rounded robot head with circular eye cutouts, and a pill-shaped mouth — all using the evenodd fill rule for clean transparency.

### Changes
- **webui/dist/assets/runtime-icons/wee.svg** — New 430-byte SVG icon
  - Tall top hat crown (9x6.5) + wide brim (18x2)
  - Rounded robot head (14x13, rx=3) with circular eye cutouts (r=2)
  - Pill-shaped mouth using evenodd path rule
  - No hardcoded colors — inherits from CSS filters like other runtime icons
- **webui/dist/app.js** — Already registered in RUNTIME_ICONS map and fallback list (prior commit)
- **agent_manager.py** — Wee already included in get_available_runtimes() with leaf icon emoji
- **tests/test_issue126_wee_icon.py** — 10 new regression tests
  - SVG existence, validity, viewBox, size, no hardcoded fills
  - App.js RUNTIME_ICONS map, fallback list, runtimeIconHTML function
  - Backend API includes wee with icon field


## [Wee Runtime Fix] End-to-end Ollama + OpenRouter + Tool Calling
**Status:** ✅ Verified (Branch: issue/wee-runtime-fix, PR #122)

### Summary
Fixed the Wee Runtime to work end-to-end through the WebUI with both Ollama and OpenRouter providers, including streaming tool calls. Merged QA-approved feature branches and fixed remaining integration gaps.

### Fixes
1. **Ollama port 11436→11434** — Fixed in wee_runtime.py and agent_manager.py PROVIDER_PRESETS
2. **Merged tool calling support** — From QA-approved #107/#108/#109 (multi-turn tool loop, conversation history, SSE streaming)
3. **Merged OpenRouter UI integration** — From QA-approved #119 (model switcher groups, live discovery with TTL cache)
4. **Added 6 free OpenRouter models** — gemma-3-27b:free, gemma-4-31b:free, llama-3.3-70b:free, nemotron-120b:free, nemotron-nano-9b:free, qwen3-coder:free
5. **Fixed OpenRouter API key propagation** — Added OPENROUTER_API_KEY env var fallback when keyring lookup fails

### Verification
- Ollama gemma4:e4b: CLI ✅ API ✅ SSE streaming ✅ Tool calls ✅
- OpenRouter nemotron free: CLI ✅ API ✅ SSE streaming ✅ Tool calls ✅
- Model switcher shows 3 groups (Ollama, OpenRouter, OpenRouter Free) ✅
- 1231 tests pass, 9 skipped ✅


## [Issue #107] Bug: Wee runtime tool calling returns no response
**Status:** ✅ QA Approved (Commit: 000bda8)

### Summary
Fixed critical bug in wee native runtime where tool-calling agentic loops returned empty responses. Root cause: Ollama port misconfiguration (11436 instead of standard 11434) caused connection timeouts appearing as "no response". Additionally improved safety handling when tool calls exhaust max rounds and added full tool-calling support to standalone CLI mode.

### Root Cause & Fixes

1. **Wrong Ollama Port (11436 → 11434)**
   - Ollama on kubuntu (192.168.1.101) runs on standard port 11434
   - agent_manager.py and wee_runtime.py had hardcoded port 11436
   - Now correctly points to port 11434 in both PRESETS configurations
   - Fixed in agent_manager.py:7630 and wee_runtime.py:20 (2 locations each)

2. **Tool-Call Agentic Loop Safety Net**
   - When all MAX_TOOL_ROUNDS produce tool calls without final text response, now returns last tool result instead of empty string
   - Prevents empty response output while maintaining agentic flow

3. **Full Tool-Calling Support in Standalone CLI**
   - Added complete agentic loop to wee_runtime.py standalone CLI mode (--tools flag)
   - Supports tool detection, execution (bash/python), follow-up requests
   - Enables background task support with proper tool call handling

### Changes
- **agent_manager.py** — Fixed Ollama port 11436→11434 (2 locations in PRESETS)
- **wee_runtime.py** — Fixed port, enhanced tool-call loop with max-rounds safety net, added full agentic loop to CLI mode
- **tests/test_issue107_tool_calling.py** — 21 new comprehensive regression tests

### Test Coverage
- **1186 total tests pass**, 9 skipped, 0 failures
- **21 new regression tests** covering:
  - Port correctness validation
  - Tool call delta detection
  - Tool execution (bash/python)
  - Full agentic loop with mocked OpenAI
  - Max-rounds fallback behavior
  - Tools-not-supported retry logic
  - Standalone CLI tool execution

### QA Notes
- wee-qa verified complete tool-calling workflows
- Tested with Ollama and LM Studio endpoints on correct ports
- Agentic loops tested with multi-step workflows
- All edge cases validated: max-rounds, empty responses, tool execution failures

---

## [Issue #119] Feature: Wire up OpenRouter in wee runtime UI
**Status:** In QA Review

### Summary
Wired up OpenRouter as a cloud model provider in the wee runtime UI. Users can now select
OpenRouter models (Llama 4, Claude, Gemini, GPT-4.1, DeepSeek, etc.) from the WebUI model
switcher when using the wee runtime, alongside local Ollama models.

### Changes
- **Backend (agent_manager.py)**
  - Added OPENROUTER_POPULAR_MODELS set with 12 curated popular model IDs
  - Added WEE_MODELS dict constant with static Ollama + OpenRouter model groups
  - Added fetch_wee_models() method with OpenRouter API discovery, 300s TTL cache, static fallback
  - Fixed pre-existing bug: wee dispatch was returning raw tuples instead of flat IDs
  - Updated /api/v1/models endpoint with group field for UI grouping
  - Updated _get_model_description() and get_model_from_name() with wee model entries
  - Added wee to known_runtimes in /api/v1/models endpoint

- **WebUI (webui/dist/app.js)**
  - populateModelDropdown: renders optgroup elements when models have group info
  - meta-model dynamic load: adds group separator headers between model groups

- **Keyring**
  - OpenRouter API key stored in keyring-vault on both dev and prod hosts
  - fetch_wee_models() reads key from keyring first, falls back to env var

- **Tests** (34 new tests in tests/test_issue119_openrouter.py)

## [Issue #100] Feature: GitHub Issues Integration for TODO Endpoints
**Status:** ✅ QA Approved (Commit: ca21379)

### Summary
Integrated GitHub Issues as a primary TODO data source alongside flat-file TODOs. The `/api/v1/todos` endpoints now fetch from both GitHub Issues (with `todo` label) and flat files, merging results with deduplication by title. Adds robust label validation when creating GitHub Issues, with automatic retry and graceful degradation.

### Changes
- **GET /api/v1/todos** — Fetches from both GitHub Issues and flat files
  - GitHub Issues (primary) labeled with `todo` from configured repository
  - Flat files (fallback) from configured TODO directory
  - Automatic deduplication by title
  - All tests passing (26 new tests added to test suite)

- **POST /api/v1/todos** — Creates TODO in both GitHub Issues and flat file
  - Creates GitHub Issue with `todo` label (if labels provided)
  - Also creates flat-file backup for offline access
  - Label validation: invalid labels are stripped and issue creation is retried
  - If all labels invalid, issue created without --label flag
  - Response includes `labels_stripped` field when labels removed

- **POST /api/v1/todos/{title}/complete** — Closes matching GitHub Issue
  - Searches GitHub Issues by title and closes matching issue
  - Also marks flat file as complete
  - Works with both dual-source TODOs

- **Helper Functions**
  - `_fetch_github_todos()` — Fetch issues with `todo` label using `gh` CLI
  - `_create_github_todo()` — Create issue with label validation and retry logic
  - `_close_github_todo()` — Close matching GitHub Issue by title
  - `_fetch_valid_github_labels()` — Query valid repo labels for validation
  - `_merge_todos()` — Deduplicate by title, GitHub Issues primary
  - Added logging to 4 bare except blocks in TODO functions

### Tests
- **26 new tests** in `tests/test_issue100_github_todos.py` (1129 total pass)
  - `TestGitHubTodoCreation` — GitHub Issue creation and validation
  - `TestSourceTagging` — Track GitHub vs flat-file source
  - `TestInvalidLabelHandling` — Rewritten with FastAPI TestClient
    - test_invalid_label_stripped_and_retried
    - test_all_labels_invalid_creates_without_labels
    - test_non_label_failure_is_logged_and_returns_none
    - test_labels_stripped_field_in_response
    - test_no_labels_stripped_field_when_all_valid
  - TestClient-based mocking for subprocess.run calls

### Test Results
- **1129 total tests pass**, 9 skipped, 0 failures
- All 26 Issue #100 tests pass (100%)
- No BLOCKERs or MAJORs
- Full backwards compatibility maintained

### QA Notes
- Round 1 MINOR (test rewrite) — **RESOLVED** in commit ca21379
- All 5 hollow TestInvalidLabelHandling tests rewritten using FastAPI TestClient + subprocess mocks
- Each test now calls POST /api/v1/todos via TestClient with controlled mock responses
- Full feature verified for dual-source TODO retrieval and GitHub Issue integration

### Use Cases
```bash
# Get TODOs from both GitHub Issues and flat files (deduplicated by title)
curl -H "Authorization: Bearer <token>" https://127.0.0.1:8000/api/v1/todos

# Create TODO (creates both GitHub Issue + flat file)
curl -X POST -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"title": "Fix auth bug", "labels": ["bug", "urgent"]}' \
  https://127.0.0.1:8000/api/v1/todos

# Complete TODO (closes GitHub Issue + marks flat file done)
curl -X POST -H "Authorization: Bearer <token>" \
  https://127.0.0.1:8000/api/v1/todos/Fix%20auth%20bug/complete
```

### PR Status
- Issue #100 approved by QA (commit ca21379)
- Ready for PR: `issue/100` → `dev`

---

## [Issue #94] Bug: claude-sdk elevated mode broken — args swapped
**Status:** ✅ QA Approved (Commit: ea8495d)

### Summary
Identified and verified regression test coverage for a bug where `run_claude_sdk()` passed arguments to `_resolve_permission_mode()` in incorrect order (swapped args). The bug itself was already fixed in Issue #91 (commit a9ccce4), but this issue ensures regression prevention with targeted test coverage.

### Bug Details
In `run_claude_sdk()`, `_resolve_permission_mode()` was called with arguments swapped: `(mode, session_data)` instead of `(session_data, mode)`.

**Function signature:** `_resolve_permission_mode(self, session_data: dict, prompt_mode: str)`

With args swapped, the `session_data` dict landed in `prompt_mode`. The check `if prompt_mode != "restricted"` evaluated to True (dict != string), so the function immediately returned the dict. Downstream, `mode == "elevated"` always failed (dict != string), so `sdk_permission_mode` was never set to `bypassPermissions`. Elevated sessions via claude-sdk always fell through to default mode — bash tool still prompted for approvals.

### Fix & Verification
**Line 6748 in agent_manager.py:** `mode = self._resolve_permission_mode(session_data, mode)`

The bug was already fixed in Issue #91 (commit a9ccce4) as a side effect of comprehensive permission mode overhaul. This issue adds targeted regression tests to prevent reintroduction.

### Tests
- `tests/test_issue94_claude_sdk_elevated.py` — **8/8 new tests pass**
  - Swapped args return dict (demonstrating the bug)
  - Correct args always return string
  - Elevated mode maps to `bypassPermissions`
  - Background task elevated permissions resolve correctly
  - Return type is always string across all permission combinations

### Test Results
- **1111 total tests pass**, 9 skipped, 0 failures
- All call sites verified (8 call sites confirmed correct arg order)
- CHANGELOG entry accurate and complete
- No regressions in existing permission tests

### PR Status
- PR #98 approved
- Issue #94 closed

---

## [Issue #93] Bug Fix: Add Secrets Management API Endpoints for WebUI Unlock
**Status:** ✅ QA Approved (Commit: 3f351d3)

### Summary
Added API endpoints to detect and unlock GNOME Keyring secret store from the WebUI. When the keyring is locked, users can now use the WebUI to check status and unlock it, instead of failing silently on secret retrieval.

### Changes
- **GET /api/v1/secrets/keyring-status** — Check if GNOME Keyring is locked
  - Returns: `{"locked": boolean, "message": "string"}`
  - Status codes: 200 (success), 500 (backend error)
  - Example: `curl -H "Authorization: Bearer <token>" https://127.0.0.1:8000/api/v1/secrets/keyring-status`

- **POST /api/v1/secrets/keyring-unlock** — Unlock GNOME Keyring with password
  - Request body: `{"password": "string"}`
  - Returns: `{"success": boolean, "message": "string"}`
  - Status codes: 200 (success/already unlocked), 401 (wrong password), 500 (backend error)
  - Example: `curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"password":"mypassword"}' https://127.0.0.1:8000/api/v1/secrets/keyring-unlock`

- **README.md updated** with full endpoint documentation including schemas, curl examples, and security notes

### Tests
- **26/26 tests pass** across all suites
- Full backwards compatibility — existing secret retrieval unchanged
- Endpoint-specific tests for both locked/unlocked states

### QA Notes
- Round 1 MINOR (missing API documentation) — **RESOLVED** in commit 3f351d3
- README now includes complete endpoint docs with schemas, status codes, examples
- Backend implementation notes: Uses `python-keyring` + `gnome-keyring-daemon` via `secretstorage` library
- Security: Password transmitted over HTTPS only, no logging of credentials

---

## [Issue #88] Feature: Wee Native Runtime — OpenAI-Compatible API Backend
**Status:** ✅ QA Approved (Commit: 7f8a8de)

### Summary
Added a new `wee` runtime that connects to any OpenAI-compatible API endpoint 
(Ollama, OpenRouter, LM Studio) without depending on external CLI tools. Uses the 
OpenAI Python SDK with native streaming support and provider auto-resolution.

### Changes
- **run_wee_native()** — Interactive session handler with SSE streaming support
- **wee_runtime.py** — Standalone CLI for background task execution
- **Provider prefix auto-resolution** — `ollama/`, `openrouter/`, `lmstudio/` automatically resolve to correct endpoints
- **17 integration points** in agent_manager.py for smooth SDK/runtime fallback
- **Streaming support** — Full response streaming via OpenAI SDK event handling
- **Graceful degradation** — Falls back to claude/copilot SDK if wee endpoint unavailable

### Tests
- **19 new wee-specific tests** in `tests/test_wee_native_runtime.py`
- **1087 total tests pass** (1087 passed, 9 skipped, 0 failures)
- **19/19 wee-specific tests pass** — 100% coverage
- No BLOCKERs, no MAJORs
- 3 minor non-blocking observations logged in issue #88 comments

### Use Cases
```python
# Connect to Ollama on localhost:11434
wee_runtime = WeeNativeRuntime("ollama/qwen35-9b")

# Connect to LM Studio on 192.168.1.101:11437
wee_runtime = WeeNativeRuntime("lmstudio/qwen35-9b")

# Connect to OpenRouter via API key
wee_runtime = WeeNativeRuntime("openrouter/anthropic/claude-opus")
```

### Security & Reliability
- Uses OpenAI SDK's built-in retry logic and timeout handling
- Endpoint configuration via environment variables (no hardcoding)
- Request/response logging for debugging
- Automatic fallback to primary runtimes on failure

---

## [Issue #87] Feature: Streaming + Tool Call Support for copilot-sdk and claude-sdk
**Status:** ✅ QA Approved (Commit: 001015e)

### Summary
Added real-time streaming response delivery and tool call event tracking for both
`copilot-sdk` and `claude-sdk` runtimes, achieving feature parity with CLI-based
runtimes for WebUI SSE consumers.

### Changes

#### copilot-sdk (`run_copilot_sdk`)
- **Streaming:** `on_event` callback now handles `ASSISTANT_STREAMING_DELTA` and
  `ASSISTANT_MESSAGE_DELTA` events, pushing text chunks to `_StreamBuffer` in real-time
- **Tool calls:** `TOOL_EXECUTION_START`, `TOOL_EXECUTION_COMPLETE`, and `COMMAND_EXECUTE`
  events generate standardized tool_call events with id, name, input, runtime, timestamp
- **Done sentinel:** Pushed on all exit paths (success, error, session failure)
- **Graceful fallback:** Uses `getattr(self, "_stream_buffers", {})` for test compatibility

#### claude-sdk (`run_claude_sdk`)
- **Streaming:** `TextBlock` content pushed as chunks during async iteration over `query()`
- **Tool calls:** `ToolUseBlock` emits "detected" events with block.id/name/input;
  `ToolResultBlock` emits "completed" events with tool_use_id and is_error flag
- **Done sentinel:** Pushed on all exit paths including SDK exceptions
- **New imports:** `ToolUseBlock`, `ToolResultBlock` from `claude_agent_sdk`

#### Tool Event Format (both runtimes)
```json
{
  "event": "detected|started|completed",
  "id": "tc_<runtime>_N or block.id",
  "name": "tool_name",
  "input": "truncated_input (max 200 chars)",
  "runtime": "copilot-sdk|claude-sdk",
  "timestamp": "ISO 8601 UTC"
}
```

### Tests
- **18 new tests** in `tests/test_sdk_streaming_tools.py`:
  - `TestCopilotSdkStreaming` (4 tests): streaming deltas, done sentinel, error paths, no-buffer
  - `TestCopilotSdkToolCalls` (3 tests): tool start, command execute, start+complete lifecycle
  - `TestClaudeSdkStreaming` (4 tests): text chunks, done sentinel, error, no-buffer
  - `TestClaudeSdkToolCalls` (5 tests): ToolUseBlock, ToolResultBlock, error flag, multiple tools, session ID
  - `TestToolEventStructure` (2 tests): required field validation for both runtimes
- **47 existing SDK tests pass** (test_copilot_sdk_runtime.py + test_claude_sdk_runtime.py)
- **1056 total tests pass**, 9 skipped, 0 failures


## [Issue #86] Bug Fix: claude-sdk Runtime Stalls on 2nd Turn
**Status:** ✅ QA Approved (commit 4785965 on dev)

### Root Cause
Multi-turn conversations used `ClaudeSDKClient.receive_messages()` — an **infinite** async
generator designed for interactive bidirectional sessions. It never terminates after a single
response, causing the 2nd turn to hang indefinitely. The `ClaudeSDKClient` was also spawning
a new subprocess per turn without loading prior conversation context.

### Fix
Unified both first-turn and multi-turn code paths to use the stateless `query()` function
from `claude_agent_sdk`. For multi-turn, `options.resume` is set to the session ID, which
passes `--resume <session_id>` to the CLI subprocess, loading prior conversation context
and completing naturally when the response finishes.

### Changes
- **agent_manager.py:**
  - Removed `ClaudeSDKClient` import (no longer needed)
  - Replaced dual-path logic (ClaudeSDKClient vs query()) with unified `claude_sdk_query()` path
  - Multi-turn sets `options.resume = session_id` for proper session resumption
- **tests/test_claude_sdk_runtime.py:**
  - Removed `_FakeClaudeSDKClient` mock class (no longer needed)
  - Updated `TestSessionResumption` to verify `options.resume` is set correctly
  - Added 3 new Issue #86 regression tests:
    - `test_multiturn_uses_query_with_resume` — verifies query() path with resume option
    - `test_multiturn_completes_without_stall` — verifies 2nd turn returns output
    - `test_multiturn_stores_session_id_from_result` — verifies session_id stored on resume
    - `test_resume_false_with_session_id_starts_new` — verifies resume=False ignores session_id
  - Total: 28 tests (up from 24), all passing

### Testing
- 28/28 claude-sdk tests pass
- 1038/1038 full regression tests pass, 9 skipped, 0 failures


## [Issue #84] Bug Fix: Remove Non-Functional Auto Runtime
**Status:** ✅ Fixed (commit 266bee9 on dev)

### Summary
Removed the non-functional `auto` runtime that appeared in the UI runtime selector but had no execution handler. Selecting it would produce "Unknown runtime auto" errors.

### Changes
- **agent_manager.py:**
  - Removed `auto` from `get_available_runtimes()` list
  - Removed early-return bypass in `check_runtime_available()` that always returned True for `auto`
  - Removed `auto` from `/runtime set` help text
- **tests/test_auto_runtime_removed.py:** Added 4 regression tests:
  - Verify `auto` not in available runtimes list
  - Verify `check_runtime_available("auto")` returns False
  - Verify `/runtime set auto` returns error
  - Verify all listed runtimes have valid handlers

### Impact
- Users no longer see a broken `auto` option in the runtime selector
- API `/api/v1/runtimes` no longer includes `auto`
- No effect on the Cursor model `auto` (model selector, not runtime)



## [Issue #83] Feature: Display Response Generation Time
**Status:** ✅ Implemented (commit 4da4529 on dev)

### Summary
Added response generation timing display in the WebUI message output box. Each assistant message now shows how long it took to generate the response with a subtle timestamp indicator positioned in the bottom right corner.

### Changes
- **WebUI (app.js):** 
  - Added `messageTiming` to STATE object to track timing per session
  - Modified `sendMessage()` to capture start time using `performance.now()`
  - Updated `renderMessage()` signature to accept optional `timing` parameter
  - Modified `sendMessageStreaming()` done handler to calculate elapsed time
  - Automatically appends timing div to streaming bubbles and non-streaming responses
  - Works with all runtimes (streaming path and command/no-chunk path)
  
- **WebUI (app.css):**
  - Added `.message-timing` CSS class with subtle styling
  - Gray text color using `var(--text-secondary)` with 0.45 opacity
  - Small font (12px), italic styling
  - Top border separator with subtle transparent line
  - Positioned in bottom right of message bubble
  - Timing calculated to 1 decimal place using `elapsedSec.toFixed(1)`
  - Format: "⏱️ Generated in 2.5s"

### User-Facing Behavior
- Response messages display timing indicator below content, above TTS button
- Timing is measured from user query send to response completion
- Format: "⏱️ Generated in X.Xs" (e.g., "⏱️ Generated in 1.2s")
- Timing display only appears on assistant messages, not user messages
- Works for all runtimes: copilot, claude, claude-sdk, claude-agent-sdk, cursor, gemini, opencode

### Test Results
- Full regression suite: 1031 passed, 9 skipped, 0 failures (50.85s)
- No breaking changes to existing functionality

---


## [Issue #81] Bug Fix: Claude SDK Multi-Turn Conversations
**Status:** ✅ Fixed (commit a2610ba on dev)

### Problem
Claude SDK runtime failed on multi-turn conversations with exit code 1.
Root cause: Using stateless query() which does not maintain conversation state.
For resumed sessions, ClaudeSDKClient is now used instead.

### Solution  
Refactored to use ClaudeSDKClient for multi-turn (resumed) sessions
while keeping query() for one-shot queries. Session context now properly
preserved across turns with ResultMessage handling.

### Changes
- Added ClaudeSDKClient import for stateful multi-turn
- Refactored _run_sdk() with conditional logic
- Added _FakeClaudeSDKClient to test module
- New test: test_multiturn_uses_clausdesdk_client()

### Verification
- 25 claude-sdk tests pass (24 existing + 1 new)
- Full regression: 1031 passed, 9 skipped
- Multi-turn sessions work with context preservation

## [Issue #77] Feature: Claude Agent SDK Runtime
**Status:** ✅ QA Approved (commits 6ea6371 + 0ecc7ab on dev)

### Summary
Added new `claude-agent-sdk` runtime using the `claude-agent-sdk` Python package for
native async integration. This in-process approach eliminates subprocess overhead, provides
structured error handling, and supports session continuity via `ResultMessage` capture.

### Changes
- **New runtime:** `claude-agent-sdk` — uses `query()` async generator via `ClaudeAgentOptions`
- **New method:** `run_claude_agent_sdk()` in `SessionManager` (~160 lines)
- **Session continuity fix (MAJOR):** `ResultMessage` added to imports and processed in the
  async message loop — stores `session_id` via `update_session_field()` enabling resumption
- **TimeoutError fix (MINOR):** `except` clause now catches `(asyncio.TimeoutError,
  concurrent.futures.TimeoutError)` in the `ThreadPoolExecutor` path
- **16 integration points updated:** help text, validation, model dispatch, session handling,
  dispatch routing, API endpoints, argparse
- **Requirements:** Added `claude-agent-sdk>=0.1.0` to `requirements.txt`
- **Documentation:** `ARCHITECTURE.md` updated with flow diagram and runtime comparison table
- **Tests:** 24 unit tests across 9 classes — import handling, mode mapping, streaming,
  errors, session resume (incl. `test_result_message_stores_session_id`), registration, dispatch

### Key Technical Decisions
- `run_claude_agent_sdk()` uses `asyncio.run()` in a `ThreadPoolExecutor` — safe because
  FastAPI runs it via `asyncio.to_thread()` (separate OS thread)
- SDK import is lazy (inside method body) to avoid `ImportError` if package not installed
- Permission modes: `elevated→bypassPermissions`, `sandboxed→plan`, `restricted→default`
- `ResultMessage` is the terminal message in the stream; its `session_id` is the authoritative
  source for session continuity across turns
- `concurrent.futures.TimeoutError` catch added alongside `asyncio.TimeoutError` to cover
  the `ThreadPoolExecutor.submit(...).result(timeout=...)` code path

### Testing
- 24 unit tests — all passing (target suite)
- Full regression suite: 1030 passed, 0 failures
- No regressions in existing test suite

### Non-Blocking Follow-Up
The `concurrent.futures` import is inside the `if`-branch where it is used. A future cleanup
could hoist it before the outer `try` block to eliminate a latent `NameError` in edge-case
timeout paths (filed as non-blocking note).

### Usage
```
/runtime set claude-agent-sdk     # Switch to Agent SDK runtime
/runtime set claude               # Switch back to CLI subprocess runtime
```

---

All notable changes to Wee Orchestrator are documented here.

## [Issue #76] Feature: Add Copilot SDK Runtime (Hybrid Approach)
**Status:** ✅ QA Approved (commit 7dec088)

### Summary
Added new `copilot-sdk` runtime using the `github-copilot-sdk` Python package for native
async integration. This hybrid approach enables advanced features (streaming via event
handlers, session resumption, structured error handling) while maintaining full backward
compatibility with the existing `copilot` CLI runtime.

### Changes
- **New runtime:** `copilot-sdk` — uses `CopilotClient` async SDK instead of subprocess
- **New method:** `run_copilot_sdk()` in `SessionManager` (~90 lines)
- **16 integration points updated:** slash commands, API endpoints, argparse, dispatch
  routing, session management, strip_metadata, model fetch, help text, background tasks
- **Requirements:** Added `github-copilot-sdk>=0.1.0` to `requirements.txt`
- **Documentation:** Updated `ARCHITECTURE.md` with SDK flow diagram and comparison table
- **Tests:** 19 new tests across 6 test classes (routing, strip_metadata, session mgmt,
  run method with mocks, dispatch, API endpoints)

### Key Technical Decisions
- `run_copilot_sdk()` uses `asyncio.run()` internally to match existing synchronous runtime
  pattern — safe because FastAPI runs it in `asyncio.to_thread()` (separate thread)
- SDK import is lazy (inside method body) to avoid ImportError if package not installed
- Shares model list with `copilot` CLI runtime (same backend)
- Event handler captures `ASSISTANT_MESSAGE` events as fallback when `send_and_wait()` returns None
- Three-tier response extraction: result_event → collected_messages → message history

### Testing
- 19 unit tests — all passing
- Full regression suite: 1006 passed, 9 skipped, 0 failures
- No regressions in existing test suite

### Usage
```
/runtime set copilot-sdk          # Switch to SDK runtime
/runtime set copilot              # Switch back to CLI runtime (backward compatible)
```

---

## [Unreleased] — Dev Branch

### Added
#### F001: LLM-Generated Conversation Titles
- **Status**: QA Approved (commit 79503ed on dev)
- **Commit**: 79503ed
- **Issue**: Conversation history sessions lacked descriptive titles. Sessions displayed raw session IDs or a static placeholder in the history panel, making it difficult for users to identify past conversations at a glance.
- **Feature**: Auto-generates and periodically refreshes concise, descriptive session titles using an LLM, with a smart heuristic fallback when no LLM is available.
  - **Primary**: Ollama (local, free) — `POST {TITLE_GEN_OLLAMA_URL}/api/generate` using `TITLE_GEN_MODEL` (default: `granite3.3-tuned`)
  - **Fallback**: Anthropic API (`claude-haiku-4.5`) when `ANTHROPIC_API_KEY` is set and Ollama is unavailable
  - **Heuristic fallback**: Extracts the first substantive user message, strips markdown/code fences/URLs, truncates to 60 chars at a word boundary — never calls an LLM
  - **Auto-trigger**: `_maybe_auto_generate_title()` fires after every session response (background, non-blocking); first generation at 2+ messages; refreshes every `TITLE_REFRESH_INTERVAL` (default: 10) messages
  - **User-set title protection**: `update_title_llm()` will not overwrite titles with `title_source == "user"`
  - **Dependency**: `httpx>=0.27.0` added to `requirements.txt` (installed 0.28.1) for async HTTP to Ollama
- **Key functions**:
  - `_generate_title_via_llm(messages)` — async; tries Ollama then Anthropic; returns `None` on all failures
  - `_maybe_auto_generate_title(channel, identity, session_id)` — async; evaluates generation criteria and dispatches LLM/heuristic
  - `_smart_heuristic_title(messages)` — sync; extracts and cleans first user message; no LLM required
  - `update_title_llm(channel, identity, session_id, title, source)` — persists generated title; records `title_source`, `title_generated_at`, `message_count_at_title_gen`
- **API endpoint**: `POST /api/v1/history/sessions/{session_id}/generate-title` — force (re)generate an LLM title on demand
- **Configuration (env vars)**:
  - `TITLE_GEN_OLLAMA_URL` — Ollama base URL (default: `http://192.168.1.101:11434`)
  - `TITLE_GEN_MODEL` — Ollama model name (default: `granite3.3-tuned`)
  - `TITLE_REFRESH_INTERVAL` — messages between title refreshes (default: `10`)
- **Error handling**: All exceptions in `_maybe_auto_generate_title` are caught and logged as `WARNING`; title generation never blocks or errors a user response
- **Notes**: No dedicated unit tests for the async LLM path (dev disk full prevented test run — env issue, not code issue). Heuristic and API endpoint paths are covered by existing integration tests.
- **Impact**: Session history panel now shows human-readable titles that update as conversations evolve; titles persist across sessions and are searchable

### Fixed
#### #77: Claude Agent SDK Runtime
- **Status**: ✅ QA Approved (commits 6ea6371 + 0ecc7ab + 583ab20 on dev)
- **Commits**: 6ea6371 (feat) + 0ecc7ab (fix) + 583ab20 (docs)
- **Feature**: Add claude-agent-sdk runtime to Wee Orchestrator for Agent SDK-based session continuity
- **QA Result**: APPROVED — both MAJOR and MINOR issues resolved
- **Original Issues**:
  - **MAJOR**: `run_claude_agent_sdk()` never imported or processed `ResultMessage` from the SDK async generator. `ResultMessage` contains `session_id` (str) which must be captured and stored via `update_session_field()` for session resumption to work.
  - **MINOR**: `concurrent.futures.TimeoutError` not caught in `ThreadPoolExecutor` path — fell through to generic exception handler instead of returning a proper timeout response.
- **Fixes Applied** (commit 0ecc7ab):
  - Implemented `ResultMessage` import and async generator consumption in `run_claude_agent_sdk()`
  - Added session_id capture and storage via `update_session_field()` for session resumption
  - Added explicit `concurrent.futures.TimeoutError` exception handling in ThreadPoolExecutor path
  - **Non-blocking follow-up** (commit 583ab20): Hoist `concurrent.futures` import before try block in `run_claude_agent_sdk()` for clarity
- **Testing**:
  - 24 new tests (from 23 baseline) all pass
  - Full regression suite: 1030 passed, 9 skipped, 0 failures
  - Session ID capture tested and verified
  - TimeoutError handling verified across both ThreadPool and AsyncIO paths
- **Impact**: Claude Agent SDK runtime now fully functional with proper session resumption and timeout handling. Users can maintain session state across multiple calls to Agent SDK-based workflows.

### In Review / Blocked
#### #72: Remove Memory Context Prompt Fallback, Inject at Session Creation
- **Status**: ✅ QA Approved (commit 30d1400 on dev)
- **Commit**: 30d1400
- **Issue**: Memory context was injected as a prompt prefix in `_run_background_task()`, a fragile approach that only applied to background tasks. Other code paths (interactive sessions, queued jobs, promoted sessions) lacked memory context. The [MEMORY CONTEXT] wrapper block was unwieldy and duplicated memory across multiple system messages.
- **Fix**: Moved memory injection from background task prompt-prepend into `build_agent_context_prompt()` to run once per session creation for all code paths:
  - **memory/inject.py**: Removed [MEMORY CONTEXT] block wrapper from `build_context()` and `get_memory_context()` — now returns raw sections only
  - **agent_manager.py `build_agent_context_prompt()`**: Added `memory_injected` flag check; injects memory via `get_memory_context()` if not yet set for the session
  - **agent_manager.py `_run_background_task()`**: Removed explicit memory injection block (replaced by one-liner comment directing to build_agent_context_prompt)
  - **Session-Level Injection**: Memory is now injected exactly once per session at context build time, not at task execution time
- **Testing**: 34 tests in `tests/test_memory_native.py` (new `TestNoPromptPrefix` class with 4 tests):
  - No [MEMORY CONTEXT] markers in output
  - `memory_injected` flag prevents double-injection
  - `build_agent_context_prompt()` includes memory without wrapper block
  - Existing tests updated to assert absence of wrapper
- **Metrics**: 947 tests pass, 9 skipped, 0 failures — no regressions
- **Impact**: Memory context is now consistently injected across all session types (background, interactive, queued); cleaner output without wrapper markers; single injection point eliminates duplication risks


#### #71: Scheduler Backward Clock Drift Skips Jobs
- **Status**: QA Approved (commit 867c553 on dev)
- **Commit**: 867c553
- **Root Cause**: _is_job_ready() compares next_run against wall-clock now. When the system clock jumps backward (NTP adjustment or manual time change), a job that was due becomes future-dated relative to the new time and is silently skipped with no log entry.
- **Fix**: Implemented wall-clock debt compensation mechanism:
  - _wall_clock_debt accumulates on each backward drift event
  - In _is_job_ready(), computes compensated_now = now + debt (expanding the now window to cover lost time)
  - Jobs within the debt window are recovered and executed with a clear log entry
  - Debt drains on forward drift events (clock catches back up)
  - Capped at _DRIFT_COMPENSATION_CAP = 600s (10 minutes) to prevent runaway compensation
  - Drift events tracked in _drift_events list (capped at 50 most recent)
  - _drift_recovered_count counter tracks total compensated executions
- **Additional Improvements**:
  - _recalculate_stale_jobs(): now logs ValueError/TypeError exceptions instead of silently swallowing them
  - _is_job_ready(): error messages now include the job ID for easier log correlation
  - New public method get_drift_diagnostics() returns current compensation state for external monitoring
- **get_drift_diagnostics() Response**:
  - wall_clock_debt_seconds: float -- accumulated backward drift (0 = no active compensation)
  - drift_compensation_active: bool -- True when debt > 0
  - drift_recovered_jobs: int -- total jobs recovered via compensation since executor start
  - recent_drift_events: list -- last 10 drift events with direction and magnitude
  - compensation_cap_seconds: int -- current cap (600)
- **Testing**: 37 tests in tests/test_scheduler_clock_drift.py (up from 21), all passing:
  - Backward drift compensation accumulates and drains correctly
  - Jobs within debt window are recovered; jobs beyond cap are not
  - Debt cap prevents runaway compensation
  - get_drift_diagnostics() returns correct state in all scenarios
  - _recalculate_stale_jobs() logs errors on malformed job data
  - Integration: full cycle with backward/forward drift sequences
- **Metrics**: 938 tests pass, 9 skipped, 0 failures -- no regressions
- **Impact**: Schedulers behind NTP-synced hosts no longer silently drop jobs during backward clock corrections. All skipped jobs within the compensation window (up to 10 min) are recovered automatically with audit logging.

#### #72: Remove Memory Context Prompt Fallback, Inject at Session Creation
- **Status**: ✅ QA Approved (commit 30d1400 on dev)
- **Commit**: 30d1400
- **Issue**: Memory context was injected as a prompt prefix in `_run_background_task()`, a fragile approach that only applied to background tasks. Other code paths (interactive sessions, queued jobs, promoted sessions) lacked memory context. The [MEMORY CONTEXT] wrapper block was unwieldy and duplicated memory across multiple system messages.
- **Fix**: Moved memory injection from background task prompt-prepend into `build_agent_context_prompt()` to run once per session creation for all code paths:
  - **memory/inject.py**: Removed [MEMORY CONTEXT] block wrapper from `build_context()` and `get_memory_context()` — now returns raw sections only
  - **agent_manager.py `build_agent_context_prompt()`**: Added `memory_injected` flag check; injects memory via `get_memory_context()` if not yet set for the session
  - **agent_manager.py `_run_background_task()`**: Removed explicit memory injection block (replaced by one-liner comment directing to build_agent_context_prompt)
  - **Session-Level Injection**: Memory is now injected exactly once per session at context build time, not at task execution time
- **Testing**: 34 tests in `tests/test_memory_native.py` (new `TestNoPromptPrefix` class with 4 tests):
  - No [MEMORY CONTEXT] markers in output
  - `memory_injected` flag prevents double-injection
  - `build_agent_context_prompt()` includes memory without wrapper block
  - Existing tests updated to assert absence of wrapper
- **Metrics**: 947 tests pass, 9 skipped, 0 failures — no regressions
- **Impact**: Memory context is now consistently injected across all session types (background, interactive, queued); cleaner output without wrapper markers; single injection point eliminates duplication risks

#### #78: Task Scheduler Invokes LLM for Command-Only Tasks
- **Status**: ✅ QA Approved (commit c115852 on dev)
- **Commit**: c115852
- **Issue**: Scheduler tasks with `mode="command"` should execute shell commands directly, never invoke an LLM. However, if a misconfigured task included both `mode="command"` and `runtime`/`model` fields, the executor would be ambiguous about whether to route to command mode or LLM mode. This ambiguity could cause command-only tasks to unexpectedly invoke LLM APIs, wasting compute and token budget.
- **Fix**: Added validation in `_execute_task()`:
  - When `mode="command"` is paired with `runtime` or `model` fields, log a `WARNING` with the misconfiguration details
  - Always route command-mode tasks to `_execute_command_mode()`, never `_execute_ai_mode()`
  - Command execution continues unaffected (backward compatible); misconfiguration is visible in logs for remediation
- **Key Code**:
  - `scheduler/executor.py`: `_execute_task()` checks `if mode == "command" and (runtime or model)` and emits warning before routing
  - Validation prevents silent routing errors and surfaces misconfigurations during testing
- **Testing**: 7 new unit tests in `tests/test_scheduler_command_mode.py`:
  - Routing to `_execute_command_mode()` when `mode="command"` (with and without extra fields)
  - Warning emission when `runtime` or `model` are present alongside `mode="command"`
  - Result passthrough (no extra processing)
  - Command execution with various shell scenarios (exit codes, output, errors)
  - Edge case: `mode` field missing or empty
- **Metrics**: 987 tests pass, 9 skipped, 0 failures — no regressions
- **Impact**: Misconfigured scheduler tasks no longer silently route to LLM; operators receive clear warnings in logs for investigation and fix.


#### #71: Scheduler Backward Clock Drift Skips Jobs
- **Status**: QA Approved (commit 867c553 on dev)
- **Commit**: 867c553
- **Root Cause**: _is_job_ready() compares next_run against wall-clock now. When the system clock jumps backward (NTP adjustment or manual time change), a job that was due becomes future-dated relative to the new time and is silently skipped with no log entry.
- **Fix**: Implemented wall-clock debt compensation mechanism:
  - _wall_clock_debt accumulates on each backward drift event
  - In _is_job_ready(), computes compensated_now = now + debt (expanding the now window to cover lost time)
  - Jobs within the debt window are recovered and executed with a clear log entry
  - Debt drains on forward drift events (clock catches back up)
  - Capped at _DRIFT_COMPENSATION_CAP = 600s (10 minutes) to prevent runaway compensation
  - Drift events tracked in _drift_events list (capped at 50 most recent)
  - _drift_recovered_count counter tracks total compensated executions
- **Additional Improvements**:
  - _recalculate_stale_jobs(): now logs ValueError/TypeError exceptions instead of silently swallowing them
  - _is_job_ready(): error messages now include the job ID for easier log correlation
  - New public method get_drift_diagnostics() returns current compensation state for external monitoring
- **get_drift_diagnostics() Response**:
  - wall_clock_debt_seconds: float -- accumulated backward drift (0 = no active compensation)
  - drift_compensation_active: bool -- True when debt > 0
  - drift_recovered_jobs: int -- total jobs recovered via compensation since executor start
  - recent_drift_events: list -- last 10 drift events with direction and magnitude
  - compensation_cap_seconds: int -- current cap (600)
- **Testing**: 37 tests in tests/test_scheduler_clock_drift.py (up from 21), all passing:
  - Backward drift compensation accumulates and drains correctly
  - Jobs within debt window are recovered; jobs beyond cap are not
  - Debt cap prevents runaway compensation
  - get_drift_diagnostics() returns correct state in all scenarios
  - _recalculate_stale_jobs() logs errors on malformed job data
  - Integration: full cycle with backward/forward drift sequences
- **Metrics**: 938 tests pass, 9 skipped, 0 failures -- no regressions
- **Impact**: Schedulers behind NTP-synced hosts no longer silently drop jobs during backward clock corrections. All skipped jobs within the compensation window (up to 10 min) are recovered automatically with audit logging.

#### #70: Scheduler Clock Drift Handling
- **Status**: ✅ QA Approved (commit 4be10e2 on dev)
- **Commit**: 4be10e2
- **Issue**: Task scheduler executor was vulnerable to system clock adjustments (NTP corrections, manual time changes). A backward clock jump could cause jobs to execute multiple times (reentry into already-executed time slots). A forward jump could cause jobs to be skipped if they landed outside the scheduling window. No mechanism to detect or handle drift.
- **Fix**: Added four complementary clock drift handling mechanisms:
  - **Drift Detection** (`_detect_clock_drift()`): Compares wall-clock delta vs monotonic time delta each cycle. When drift exceeds 30 seconds, logs warning with direction and magnitude.
  - **Per-Job Monotonic Cooldown** (`_job_last_exec_mono`): Records monotonic time of last execution for each job. Prevents double-execution when a backward clock jump reschedules a job into an already-executed time slot. Cooldown expires after 10 monotonic seconds.
  - **Stale Job Recalculation** (`_recalculate_stale_jobs()`): Recurring jobs whose `next_run` is more than 1 hour (MAX_CATCHUP_WINDOW) overdue get their `next_run` advanced to the next future slot instead of executing stale runs. Logs warning with overdue duration. One-time jobs are never recalculated.
  - **Drift-Aware Readiness Check** (`_is_job_ready()`): Enhanced to apply all three guards. Returns `False` for stale jobs so recalculation runs; returns `False` during monotonic cooldown window; logs info when executing catchup runs >30s overdue.
- **Modernization**: Replaced deprecated `datetime.utcnow()` with `datetime.now(timezone.utc)` throughout executor (6 callsites). Properly handles ISO 8601 conversion (`+00:00` → `Z`).
- **Testing**: 18 new tests in `tests/test_scheduler_clock_drift.py` covering:
  - Drift detection (forward/backward/normal)
  - Monotonic cooldown (prevents double-exec, expires correctly)
  - Stale job recalculation (recurring vs one-time, MAX_CATCHUP_WINDOW boundary)
  - Integration tests with mock clock scenarios
  - Verification that `datetime.utcnow()` is no longer used
- **Metrics**: 919 tests pass (40 scheduler tests including 18 new), 0 failures — no regressions
- **Impact**: Scheduler now resilient to system clock adjustments; no more duplicate executions or skipped jobs after clock events. Enterprises running NTP sync or time-keeping hardware can rely on Wee Orchestrator for consistent job execution

#### #68: OpenCode/Gemma4 Code Generation Improvements
- **Status**: ✅ QA Approved (commit 9a0aee1 on dev)
- **Commit**: 9a0aee1
- **Issue**: OpenCode/Gemma4 code generation test (`code_gen`) was receiving misleading HTTP 200 responses when the runtime produced empty/null output or connection errors. Root cause identical to #66/#67 — test ran before `/api/v1/query` existed and accumulated artifacts from runtime issues.
- **Improvements**:
  - **ANSI Stripping**: Strip ANSI escape codes from runtime output before error detection and response formatting. Prevents color codes from interfering with pattern matching.
  - **Empty Response Detection**: Return `502 Bad Gateway` (empty_response error) for null, empty, or whitespace-only runtime output. Previously returned HTTP 200 with empty body.
  - **Connection Error Patterns**: Added detection for local model server failures:
    - `ECONNREFUSED` (502) — Connection refused (server not running)
    - `ETIMEDOUT` (504) — Connection timeout (server slow/hung)
    - `ECONNRESET` / `socket hang up` (502) — Connection reset by peer
  - **Response Format** (connection error example):
    ```json
    {
      "detail": {
        "error": "connection_refused",
        "message": "Error: connect ECONNREFUSED 127.0.0.1:5000",
        "runtime": "opencode",
        "code": "ECONNREFUSED"
      }
    }
    ```
- **Testing**: 11 new tests in `TestQueryEndpointCodeGen` covering ANSI stripping, empty response detection, and connection error patterns. 2 tests updated in `TestQueryEndpointErrorDetection` — empty/None responses now correctly return 502 instead of 200.
- **Suite Results**: 901 tests pass, 9 skipped, 0 failures — no regressions.
- **Impact**: Code generation evaluator tests now receive accurate HTTP status codes and error details; connection issues and empty responses no longer masked as success. Improves debugging and error recovery for OpenCode/Gemma4 scenarios.

#### #67: Runtime Error Detection for /api/v1/query Endpoint
- **Status**: ✅ QA Approved (commit 5fe872d on dev)
- **Commit**: 5fe872d
- **Issue**: `/api/v1/query` returned HTTP 200 with error text in the response body when runtime execution failed (model not found, rate limited, permission denied, etc.). Evaluator tests like opencode/gemma4-26b calc test received misleading results — a successful HTTP status masking a runtime failure.
- **Fix**: Added `_RUNTIME_ERROR_PATTERNS` detection to the `/api/v1/query` endpoint. When the runtime response matches a known error pattern, the endpoint returns a proper HTTP error status code with structured error detail instead of HTTP 200 with error text.
- **Error Codes Mapped**:
  - `422 Unprocessable Entity` — Model/resource not found (`ProviderModelNotFoundError`, `model not found`, `unknown model`)
  - `429 Too Many Requests` — Rate limit exceeded (`RateLimitError`, `rate limit`, `too many requests`)
  - `403 Forbidden` — Permission denied (`PermissionDeniedError`, `permission denied`, `access denied`)
  - `401 Unauthorized` — Auth failure (`AuthenticationError`, `invalid api key`, `authentication failed`)
  - `503 Service Unavailable` — Service unavailable (`ServiceUnavailableError`, `service unavailable`, `temporarily unavailable`)
- **Response Format** (error case):
  ```json
  {
    "detail": {
      "error": "model_not_found",
      "message": "ProviderModelNotFoundError: gemma4-26b not found... (truncated to 500 chars)",
      "runtime": "opencode",
      "model": "gemma4-26b"
    }
  }
  ```
- **Testing**: 890 tests pass, 9 skipped, 0 failures. 10 new tests in `TestQueryEndpointErrorDetection` all pass — covering all 5 error pattern categories, mixed case, multi-line errors, and non-error passthrough.
- **Impact**: Evaluators and callers of `/api/v1/query` now receive accurate HTTP status codes; runtime failures are no longer masked as success responses.

#### #66: Added POST /api/v1/query Stateless Endpoint
- **Status**: ✅ QA Approved (commit 2763cc4 on dev)
- **Commit**: 2763cc4
- **Issue**: Test suite expected a stateless query endpoint `/api/v1/query` to exist but it was missing (404)
- **Feature**: New POST `/api/v1/query` endpoint for one-shot queries without session management:
  - Creates ephemeral session internally
  - Executes prompt, returns result in single response
  - Automatically cleans up session after completion
  - Accepts `prompt`, `runtime` (copilot/claude), `model`, `agent`, `timeout` parameters
  - Includes 10k character prompt validation
  - Rate-limited to 30 requests/minute per IP
- **OpenCode/Gemma4 Support**: Endpoint works with all supported runtimes
- **Testing**: 880 tests pass, 9 skipped, 0 failures. 10 new tests for query endpoint all pass.
- **Impact**: Enables stateless, fire-and-forget query operations without session complexity


#### #63: Delete Skill Button Not Working in WebUI
- **Status**: ✅ QA Approved (commit 4c8ad30 on dev)
- **Commit**: 4c8ad30
- **Issue**: Delete skill button in WebUI Skills panel was non-functional due to missing window exposure
- **Root Cause**: The `_deleteSkill()` function was not assigned to the window object. All inline onclick handlers require functions to be on `window` scope. Other skill panel functions (`_deleteOrigin`, `_skillCheckUpdate`, `_skillTriggerUpdate`, `_showOriginForm`, `_saveOrigin`) were already exposed but `_deleteSkill` was missed.
- **Fix**: Added `window._deleteSkill = _deleteSkill;` to the window exposure block in `webui/dist/app.js` (line 7517)
- **Testing**: 859 passed, 9 skipped — no regressions
- **Impact**: Users can now delete skills from the WebUI Skills panel as intended

#### #65: Auto-Delegation Session Isolation
- **Status**: ✅ QA Approved (commit 3715325 on dev)
- **Commit**: 3715325
- **Issue**: Auto-delegation (via `detect_agent_delegation()`) and `/agent` command were passing the caller's `n8n_session_id` to `_execute_with_context()`, which allowed downstream code to overwrite the caller's `session_map` entry (agent, session_id, etc.). This violated session isolation boundaries and could cause cascading failures.
- **Root Cause**: When delegating to another agent, the caller's session context was directly reused, allowing mutations to propagate back to the original session map.
- **Fix**: When `is_delegation=True`, `_execute_with_context()` now:
  - Creates an ephemeral `delegation_{uuid}` session key
  - Copies essential fields (`channel`, `identity`, `render_type`, `bot_id`) from the caller
  - Executes the delegated task using the ephemeral key
  - Cleans up the ephemeral session afterward
  - The caller's session_map entry is never touched
- **Testing**: 11 new tests covering isolation, cleanup, channel inheritance, concurrent delegations, and non-delegation passthrough
- **Metrics**: 870 tests pass, 9 skipped, 0 failures — no regressions
- **Impact**: Session boundaries now properly enforced; cascading failures from delegation corruption eliminated


### Added

#### F025 (Enhanced): Custom Themes API — wee-qa Fix Round 2
- **Status**: 🔄 QA Submitted (commit 6d45763 on dev)
- **Commit**: 6d45763
- Adds custom theme support via `/api/v1/themes` endpoint with CSS embedded in listing response
- **B01 (BLOCKER) Fix**: Removed `/api/v1/themes/{name}/css` endpoint entirely.
  - CSS content now embedded as `css` field in each custom theme object in the themes list response.
  - `loadCustomThemes()` caches CSS content in `_customThemeCSS` map (populated via the already-authenticated themes list call).
  - `applyTheme()` now injects custom CSS via `<style id="custom-theme-style">` element instead of `<link href="...">`.
  - `<link>` elements cannot send `Authorization` headers — the root cause of always-401 custom theme loading.
  - After `loadCustomThemes()` completes, re-applies current theme if it's custom (handles localStorage-persisted custom themes on page load).
- **M01 Fix**: `name == "custom.css"` dead code → corrected to `name == "custom"` (stem of `custom.css.template` is `custom.css` but `.template` files don't match `*.css` glob).
- **M02 Fix**: E501 violation on `fastapi.responses` import line — split to multi-line parenthesized import form.
- **M03 Fix**: `innerHTML` with server data in `loadCustomThemes()` → replaced with explicit DOM element creation using `textContent` for label/description fields.
- **Testing**: Replaced `TestGetThemeCSS` (9 tests, endpoint removed) with `TestCustomThemeCSSInListing` (4 new tests: css in listing, builtins have no css field, endpoint returns 404, traversal regex). 17 themes tests pass; full suite 857 passed, 9 skipped.

#### #69: BLOCKERS.md Template for Structured wee-dev Blocker Tracking
- **Status**: ✅ QA Approved (commit 6d2aade on dev)
- **Commit**: 6d2aade
- Adds standardized `BLOCKERS.md` template at repository root for documenting blockers during wee-dev task execution
- **Purpose**: Provides transparency on what's blocking progress, escalation channels, and resolution tracking
- **Template Structure**:
  - **Blocker entry format**: Timestamp, issue reference, blocker type, communication channel, status, resolution
  - **Blocker types** (7 categories): missing-key, needs-approval, needs-clarification, resource-unavailable, dependency-missing, permission-denied, other
  - **Communication decision tree**: Flowchart for determining optimal escalation channel (Telegram, approval-flow, GitHub) based on urgency and type
  - **Status states** (6): OPEN, WAITING_FOR_KEY, WAITING_FOR_APPROVAL, ESCALATED, WORKAROUND_IN_PLACE, RESOLVED
  - **Foster responsibilities section**: Outlines key decision points and action items for Foster (approvals, key management, access, resource allocation)
  - **Worked examples** (3): API key missing, clarification needed, dependency blocker
  - **Active/Resolved sections**: Ongoing blockers vs. resolved blockers for easy tracking across multiple wee-dev runs
- **Outcomes**:
  - wee-dev tasks now have a standardized format for documenting blockers
  - Foster can review BLOCKERS.md to understand what's blocking work without digging through logs
  - Escalation decisions are transparent and consistent
- **QA Pass**: All requirements verified in commit 6d2aade. No BLOCKERs or MAJORs. Ready for production.

#### F025 (Enhanced): Custom Themes API — wee-qa Fix Round 2
- **Status**: ✅ QA Approved (commits 6d45763 + 56ac461 on dev)
- **Commit**: 6d45763
- Adds custom theme support via `/api/v1/themes` endpoint with CSS embedded in listing response
- **B01 (BLOCKER) Fix**: Removed `/api/v1/themes/{name}/css` endpoint entirely.
  - CSS content now embedded as `css` field in each custom theme object in the themes list response.
  - `loadCustomThemes()` caches CSS content in `_customThemeCSS` map (populated via the already-authenticated themes list call).
  - `applyTheme()` now injects custom CSS via `<style id="custom-theme-style">` element instead of `<link href="...">`.
  - `<link>` elements cannot send `Authorization` headers — the root cause of always-401 custom theme loading.
  - After `loadCustomThemes()` completes, re-applies current theme if it's custom (handles localStorage-persisted custom themes on page load).
- **M01 Fix**: `name == "custom.css"` dead code → corrected to `name == "custom"` (stem of `custom.css.template` is `custom.css` but `.template` files don't match `*.css` glob).
- **M02 Fix**: E501 violation on `fastapi.responses` import line — split to multi-line parenthesized import form.
- **M03 Fix**: `innerHTML` with server data in `loadCustomThemes()` → replaced with explicit DOM element creation using `textContent` for label/description fields.
- **Testing**: Replaced `TestGetThemeCSS` (9 tests, endpoint removed) with `TestCustomThemeCSSInListing` (4 new tests: css in listing, builtins have no css field, endpoint returns 404, traversal regex). 17 themes tests pass; full suite 857 passed, 9 skipped.

#### F027: Verbose Mode Toggle for Tool Call Visibility
- **Status**: ✅ QA Approved (commit edcc105 on dev)
- **Commit**: edcc105
- Adds session-level verbose mode toggle to show/hide tool call details in WebUI chat interface
- **Features**:
  - New **PATCH /api/v1/sessions/{id}/settings** endpoint for updating session settings
  - `silent_mode` field in settings (boolean); controls tool call display without affecting actual logging
  - WebUI header toggle button (aria-pressed="true|false") with visual feedback
  - CSS `.tc-line` visibility control based on verbose state
- **API Endpoint** — `PATCH /api/v1/sessions/{id}/settings`:
  - Accepts JSON body: `{"silent_mode": true|false}` (whitelist-based field filtering)
  - Returns 401 without bearer token, 422 for non-boolean values, 404 for missing sessions
  - Updates persisted session object; settings returned in subsequent `GET /api/v1/sessions/{id}` calls
  - Security: Requires API authentication (Bearer token)
- **WebUI Integration**:
  - Toggle button in WebUI header with aria-pressed state indication
  - Click handler updates server setting via PATCH endpoint
  - CSS hides tool call lines (`.tc-line`) when verbose mode off
  - Graceful error handling with user feedback on API failures
- **Testing**: 19 new tests covering auth, type validation, 404/422 responses, field whitelist, settings persistence
- **QA baseline**: 799 tests pass (9 skipped), 0 regressions, flake8 clean
- **Minor note**: Non-blocking dead code — localStorage.setItem called in click handler but no corresponding getItem; commit message promises localStorage fallback but only server-side persistence implemented. Feature works correctly. localStorage should be removed or fully implemented in future cleanup pass.
- **Ready for deployment**: No breaking changes, backwards compatible, no new config required

#### F024: get_secret Capability in wee_executor.py
- **Status**: ✅ QA Approved (commit 4232d55 on dev)
- **Commit**: 4232d55
- Adds `get_secret(name, backend)` capability to wee_executor for safe secret retrieval in privileged contexts
- **Defense-in-depth Security**:
  - **Mode filtering**: Callable only in `interactive` and `sync` modes; blocked in `background` and `api` modes
  - **Elevation requirement**: Requires `WEE_ELEVATED=true` environment variable (prevents accidental secret access)
  - **Name validation**: Regex-based whitelist (`SECRET_NAME_RE`) — only alphanumeric, dot, hyphen, underscore; blocks path traversal attempts (e.g., `../etc/passwd` → rejected)
  - **Rate limiting**: Per-session rate limit on secret retrieval (50 requests/minute)
  - **Audit logging**: All calls logged with secret name + backend only; **secret values never logged** for compliance
- **Implementation**:
  - New `cap_get_secret()` handler in wee_executor.py — subprocess delegate to `secret_tool.py`
  - Supports both `keyring` (system) and `file` (encrypted JSON) backends
  - Returns `{status, name, backend, value}` on success; `{error, code}` on failure
- **Agent Context Injection**:
  - agent_manager.py automatically includes `get_secret()` example in context injection for agents running with `WEE_ELEVATED=true`
  - Example shows usage pattern and elevation requirement warning
- **Testing**: 52/52 wee_executor tests pass (20 new F024 tests + 32 baseline)
  - Coverage: elevation enforcement, name validation, path traversal protection, mode restrictions, backend validation, subprocess integration, timeout handling, rate limiting, audit log format
  - Integration tests: CLI usage with agent_manager.py; session-aware mode enforcement
- **QA baseline**: 842 total tests pass (9 skipped), 0 regressions, flake8 clean
- **Minor note**: Pre-existing E501 on docstring line 3 (90 chars) — not introduced by F024, flagged for future cleanup
- **Ready for PR**: No breaking changes, backwards compatible, no new dependencies, no config required

#### F025: CSS Theming/Skinning System
- **Status**: ✅ QA Approved (commits 7446a56, 70e5b75, b367375)
- **Initial Commit**: 7446a56
- Adds 4 themes (Emerald default, Midnight, Sunrise, Cyberpunk) with CSS variable-based customization
- Theme switching via `data-theme` attribute on html element, persisted in localStorage
- Theme picker UI in sidebar toolbar with color swatch previews
- Sunrise light mode includes highlight.js theme swap; mobile-friendly meta theme-color updates
- **Changes**:
  - webui/dist/themes.css: 3 alternate theme definitions with CSS variable overrides (334 lines)
  - webui/dist/index.html: theme picker UI and JS switching logic (106 lines)
- **QA Resolution**:
  - FIXED MAJOR 1: Added sunrise override for `.settings-textarea` (70e5b75) — white background, dark text now visible
  - FIXED MAJOR 2: Added sunrise override for `.logs-output` (70e5b75) — white background, dark text now visible
  - b367375: Comprehensive sunrise overrides for 12 additional hardcoded dark backgrounds across app UI
  - All fixes verified against app.css; 765 tests pass (9 skipped)
  - MINOR non-blocking: `.asf-input` missing from sunrise override (white text on dark bg — readable but inconsistent in agent setup form)
- **Ready for PR**: Approved by wee-qa. dev → main, Foster can create PR at will.

#### F016: Telegram Slash Command Registration with BotFather
- **Status**: ✅ QA Approved (commit 3cdf77a on dev)
- **Commit**: 3cdf77a

#### F407: Per-Agent Memory Promotion Endpoints
- **Status**: ✅ QA Approved (commit 55f3a4f on dev)
- **Commit**: 55f3a4f
- Two new API endpoints for triggering memory promotion (daily notes → MEMORY.md consolidation):
  - **POST /api/v1/memory/promote** — Promote memory for a single agent (or orchestrator if agent omitted)
  - **POST /api/v1/memory/promote-all** — Fan-out promotion across all agents in agents.json
- Endpoint behavior:
  - Each endpoint spawns `memory_promoter.py` with correct `WEE_AGENT_DIR` environment variable
  - Agent path resolution from agents.json; 404 error for unknown agents
  - 120-second timeout per agent; 504 error on timeout; 503 if script missing
  - Full stdout/stderr capture (last 2000/1000 chars) in response for debugging
  - `/promote-all` handles partial failures gracefully — continues on individual agent errors
- Helper script: `scripts/promote_all_agents_memory.sh` for use in task scheduler jobs
- Security: Both endpoints require standard API authentication (Bearer token)
- **Testing**: 13 new tests covering auth, agent resolution, error handling, fan-out, 404/503/504 behaviors
- **QA baseline**: 735 tests pass (9 skipped), 0 regressions, flake8 clean
- **Minor note**: 2 cosmetic F841 unused variables in test file (mock_run on lines 89, 206) — non-functional
- **Ready for deployment**: No breaking changes, backwards compatible, no new config required

- Automatically registers Telegram slash commands with BotFather for UI autocomplete
- `register_bot_commands()` method in TelegramConnector dynamically pulls command list from SessionManager
- Builds valid Telegram BotCommand payload; calls `setMyCommands` API on startup
- Added `/registercommands` handler for manual refresh if commands change
- Startup registration happens before polling begins
- **Testing**: 15 new tests covering payload construction, API calls, error handling
- **QA baseline**: 674 tests pass (9 skipped), flake8 clean, all services operational
- **Minor cleanup**: Removed unused imports (json, PropertyMock, pytest); fixed 10 E501 line-length violations
- **Ready for deployment**: No breaking changes, backwards compatible, no new config required


#### F402: Fix TTS Authorization Error on Objects
- **Status**: ✅ QA Approved (commit 7390c8e on dev)
- **Commit**: 7390c8e
- **Issue**: TTS fetch calls on objects were failing with 401 Unauthorized due to incorrect auth token retrieval
- **Root Cause**: `loadAuth()` function populates global `STATE` but returns `undefined`, causing TTS fetch to use `auth?.token` which was always `undefined`, resulting in empty Bearer token header
- **Fix**: Changed TTS fetch call to use `STATE.token` directly, consistent with all other authenticated API calls throughout the codebase
- **Changes**:
  - webui/dist/app.js: Updated TTS fetch auth parameter (line 160)
  - webui/dist/index.html: Cache-bust to v=20260402tts1
- **Testing**: 674 tests pass (9 skipped), py_compile OK, no fetch calls remain with loadAuth pattern
- **QA verification**: All 4 dev services running on 192.168.1.100, no regressions
- **Ready for deployment**: No breaking changes, backwards compatible, no new config required

#### F025: Session-Start Memory Injection
- **Status**: ✅ QA Approved (commit c03e0b8 on dev)
- **Commit**: c03e0b8
- Automatically inject session context (MEMORY.md + daily notes) at the start of background tasks
- Context prepended to top-level task prompts; sub-tasks skipped via `origin_session_id` to prevent double-injection
- Wraps `/opt/foster-skills/flat-memory/memory_inject.py` via new `memory_context.py` module
- Session-level tracking: `memory_injected` flag stored in session metadata
- Compaction detection (for future re-injection): `detect_compaction()` checks for known context-loss phrases
- Fail-silent design: If memory inject script is missing or errors, task continues without context
- Testing: 10 new tests covering `get_memory_context()`, `prepend_memory()`, and `detect_compaction()`
- QA baseline: 701 tests pass, 9 skipped, 2 MINORs noted:
  - `detect_compaction()` defined but not yet called (infrastructure ready, integration pending)
  - Queued-task promotion paths don't pass `memory_injected` param to `_run_background_task()` (no runtime impact)
- **Ready for PR**: No breaking changes, backwards compatible, memory context optional

#### F024: Brief One-Line Notifications for Background & Scheduled Tasks
- **Status**: ✅ QA Approved (commit 0c0bb3f on dev)
- **Commit**: 0c0bb3f
- External notifications (Telegram, WebEx) now deliver single-line format instead of verbose multi-line messages
- Format: `✅ task_id done — description` (max 200 chars, auto-truncated with ellipsis)
- Full output/error details remain in WebUI notification dict (output_preview[:500], error[:500])
- Changes:
  - `_format_notification_message()` in notification_manager.py rewritten for single-line output
  - Added `_MAX_NOTIFICATION_LENGTH=200` constant to both notification_manager.py and scheduler/executor.py
  - Added `_brief_notification()` helper in executor.py
  - Replaced 8 verbose multi-line notification strings across scheduler AI mode (4) and command mode (4)
- Testing: 25 new tests in test_brief_notifications.py covering format, truncation, length validation, and WebUI preservation
- QA baseline: 659 tests pass (0 new failures), 4 pre-existing flake8 issues unchanged, all dev services active
- **Ready for deployment**: No breaking changes, backwards compatible, no new config required

#### F019: WebUI Secrets Manager — Store Secrets via UI
- **Status**: ✅ QA Approved (commit 44df840 on dev; F020-F023 follow-up bugs approved)
- **Commit**: b4efcdc (main feature); 44df840 (UX bug fixes)
- Backend: GET/POST/DELETE /api/v1/secrets endpoints delegating to secret_tool.py
- WebUI: Secrets nav panel with masked input form and secrets list
- Security: DELETE name regex validates input; sensitive values never returned by API
- **QA fixes (F020-F023)**: Cache-bust updated (v=20260401secrets1); emojis corrected (🔐💾👁); toggle state indicator added with ARIA; error feedback persistence + screen reader support
- 24/24 F019-F023 tests pass; 580+ total tests pass; flake8 clean
- **Ready for deployment**: No breaking changes, backwards compatible

#### F017: In-Thread Background Task Notifications
- **Status**: ✅ QA Approved (546 tests pass, 0 failures)
- **Commit**: 35c3f71
- Real-time background task status updates delivered in-thread to user messages
- Users now see task lifecycle (queued → running → complete) directly in conversation, not just in ⚡ Tasks panel
- Supports Telegram, WebEx, and other connected channels
- Enhanced notification_manager routing with context/thread preservation
- New `bg_events` SQLite table for event lifecycle tracking
- **Non-blocking QA observations**:
  - No automatic TTL cleanup for orphaned bg_events entries (safe; manageable database size)
  - shownBgBanners state resets on page reload (UX polish in upcoming release)
- **Ready for deployment**: No database schema setup needed, no config changes, backwards compatible

### Changed

### Fixed

### Deprecated

### Removed

### Security

---

## Previous Releases

(Historical releases documented when applicable)
## [Issue #88] Feature: Wee Native Runtime — OpenAI-compatible API backend
**Status:** Implementation Complete — 19 new tests, 1087 total pass

### Overview
Added a new `wee` runtime that connects to any OpenAI-compatible API endpoint
(Ollama, OpenRouter, LM Studio, etc.) without depending on external CLI tools
like GitHub Copilot CLI, Claude Code, or OpenCode.

### Supported Backends
- **Ollama** (Kubuntu) at `http://192.168.1.101:11436/v1` — local, free
- **OpenRouter** at `https://openrouter.ai/api/v1` — cloud fallback, 100+ models
- **LM Studio** at `http://localhost:1234/v1` — local alternative

### Model Format
Uses `provider/model_name` prefix syntax for auto-resolving API base URL and key:
- `ollama/gemma4:e4b` — Ollama on Kubuntu (default)
- `openrouter/meta-llama/llama-4-scout` — OpenRouter cloud
- `lmstudio/qwen2.5-7b` — LM Studio local

### Implementation Details
- **`run_wee_native()`** — In-process method using OpenAI Python SDK with streaming
- **`wee_runtime.py`** — Standalone CLI script for background task subprocess execution
- Real-time SSE streaming to WebUI via `StreamBuffer.push()`
- Provider presets auto-resolve API base URLs and API keys
- `done` sentinel pushed on all exit paths (success and error)
- Graceful error handling with informative messages

### Files Changed
- `agent_manager.py` — 17 touch points: runtime registration, dispatch, streaming,
  model defaults, strip_metadata, background tasks
- `wee_runtime.py` — NEW standalone CLI for background task execution
- `tests/test_wee_native_runtime.py` — 19 comprehensive tests

### Configuration
```json
{"runtime": "wee", "model": "ollama/gemma4:e4b"}
```
Environment variables: `WEE_API_BASE`, `WEE_API_KEY`, `WEE_DEFAULT_MODEL`

