# Changelog

All notable changes to Wee Orchestrator are documented here.

## [Unreleased] — Dev Branch

### Fixed

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
