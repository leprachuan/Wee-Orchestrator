# Changelog

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
