# Changelog

## [Issue #158] Feature: Wee CLI — Standalone Command-Line AI Tool
**Status:** ✅ QA Approved (Commits: 7622e8d, 2a718be, PR pending dev→main)

### Summary
Added `wee_cli.py` — a standalone command-line AI assistant for the Wee ecosystem. Similar in style to GitHub Copilot CLI, Claude Code CLI, and Codex CLI. Supports single-shot prompts, interactive REPL, stdin piping, and tool calling via any OpenAI-compatible backend (Ollama, OpenRouter, LM Studio).

### Key Features

#### CLI Flags
- `--model` / `-m` — Model ID with provider prefix (e.g. `ollama/qwen3:8b`, `openrouter/meta-llama/llama-2-70b`). Default: `$WEE_MODEL` or `ollama/qwen3:8b`.
- `--permission` / `-p` — Tool execution permission level: `restricted` (default), `auto`, or `elevated`. Controls whether tool calls (bash, python) are allowed and with what scope.
- `--output` / `-o` — Output format: `text` (default), `json`, or `markdown`. Non-text formats suppress streaming and reformat the full response after completion.
- `--tools` / `-t` — Enable tool calling (bash, python). Requires explicit flag to avoid accidental execution.
- `--interactive` / `-i` — Enter interactive REPL mode with readline history.
- `--api-key` / `-k` — API key override (prefer env var `WEE_API_KEY` to avoid key exposure in `ps aux`).
- `--api-base` / `-b` — Custom API base URL override.
- `--system` / `-s` — System prompt override.
- `--temperature` / `-T` — Sampling temperature.
- `--timeout` — Request timeout in seconds (default: 120).
- `--config` — Config file path (default: `~/.wee/config.json`).

#### Usage Examples
```bash
# Single-shot prompt
wee "What is the capital of France?"

# With specific model
wee --model openrouter/meta-llama/llama-2-70b "Explain quantum computing"

# Tool calling with elevated permissions
wee --tools --permission elevated "List all Python files in /opt"

# JSON output (suppresses streaming, formats full response as JSON)
wee --output json "What are the first 5 Fibonacci numbers?"

# Markdown output with rich rendering
wee --output markdown "Write a Python quicksort"

# Interactive REPL
wee --interactive

# Pipe from stdin
echo "summarize this" | wee --model ollama/qwen3:8b

# Custom system prompt
wee --system "You are a bash expert" "How do I tail a log file?"
```

#### Config File Support
Persistent configuration via `~/.wee/config.json`:
```json
{
  "model": "ollama/qwen3:8b",
  "system_prompt": "You are a helpful assistant",
  "tools": false,
  "permission": "restricted",
  "output_format": "text"
}
```
Settings priority: CLI arg > env var > config file > built-in default.

#### Permission Levels
| Level | Tool Calls | Use Case |
|-------|-----------|----------|
| `restricted` (default) | Blocked | Safe for untrusted input |
| `auto` | Confirmed per-call | Interactive use |
| `elevated` | Unrestricted | Trusted automation |

### Root Cause (Implementation Gap)
The Wee ecosystem had `wee_runtime.py` for background subprocess use but lacked a user-facing CLI entry point. Users had no `wee` command they could run directly from the terminal.

### Solution

#### Core Architecture
- `wee_cli.py` — standalone entrypoint, re-uses `wee_runtime.py` core functions (`resolve_model_and_endpoint`, `execute_tool`, `_WEE_TOOLS`, `MAX_TOOL_ROUNDS`)
- `chat_stream()` — streaming chat loop with tool-call support and token usage tracking
- `run_interactive()` — REPL with readline history (`~/.wee/history`), `/help`, `/model`, `/clear` slash commands
- `run_single_shot()` — non-interactive single prompt execution

#### Key Bug Fixes (QA Rounds 1–3)
- **M-1 (Round 3, commit 7622e8d):** `run_interactive()` now correctly passes `permission` kwarg to `chat_stream()` (was silently defaulting to `"auto"` regardless of CLI flag)
- **Round 2:** `--permission` default changed from `"auto"` to `"restricted"` for safer out-of-box behavior; output format wired end-to-end
- **Round 1:** Unused imports removed, flake8 clean

### Files Changed
- `wee_cli.py` — new file, ~700 LOC
- `tests/test_issue158_wee_cli.py` — 63 regression tests
- `tests/test_issue158_permission_regression.py` — 1 regression test added by wee-qa (commit 2a718be)

### Tests
- 63 new regression tests covering:
  - All CLI flags and their defaults
  - Permission propagation through `run_interactive()` → `chat_stream()`
  - Output format modes (text, json, markdown)
  - Tool enabling/disabling
  - Config file loading and priority resolution
  - Stdin piping
  - Interactive REPL slash commands
  - Token usage tracking
- Total: 1598 passed, 0 regressions

### QA History
- **Round 1:** REJECT — unused imports, default permission "auto" instead of "restricted"
- **Round 2:** REJECT — M-1 BLOCKER: permission kwarg not passed from `run_interactive()` to `chat_stream()`
- **Round 3:** APPROVE — all findings resolved; regression test added by wee-qa (commit 2a718be)

---
## [Issue #158 Supplement] Comprehensive Agentic Runtime Test Suite
**Status:** ✅ QA Complete (Commit: 8e2c0a7, branch: issue/158)

### Summary
Comprehensive test suite for `wee_runtime.py` agentic capabilities. Validates model resolution, tool calling, streaming, permissions, and live provider integration across Ollama and OpenRouter.

### Test Coverage (68 total tests)

#### Unit Tests (54 tests — no API calls)
- **Model Resolution (12 tests):** Ollama/OpenRouter prefix stripping, preset resolution, bare model names, cross-provider parametrization
- **Tool Definitions (6 tests):** Schema validation, tool registration, JSON schema correctness
- **Tool Execution (11 tests):** Bash/Python execution, error handling, output capture, timeouts
- **SSH Sanitization (5 tests):** Issue #111 — word-boundary validation, injection prevention
- **CLI Argument Parsing (3 tests):** Flag handling, defaults, priority resolution
- **Tool-Calling Loop (4 tests):** Single/multi-round mocked flows, max rounds enforcement, tool call validation
- **Permission Levels (5 tests):** Restricted/auto/elevated access control, tool blocking
- **Streaming Output (2 tests):** Empty response handling, newline termination
- **Error Handling (4 tests):** API failures, malformed arguments, invalid API base, timeouts
- **Performance Baseline (2 tests):** Import time <1s, model resolution <100ms

#### Live Integration Tests (14 tests — Ollama + OpenRouter)
- **Ollama Basic (3 tests):** Connection validation, single-turn chat, response parsing
- **Ollama Tool Calling (4 tests):** Tool execution flows, multi-round chains, tool result synthesis
- **OpenRouter Basic (4 tests):** Connection validation, API key verification, model listing
- **OpenRouter Tool Calling (3 tests):** Tool execution flows, rate-limit handling, fallback scenarios

### Key Validations
✓ 3-round message chain verification (tool call → API → result → synthesis)  
✓ Per-round API call count validation  
✓ Streaming edge cases (empty synthesis, malformed responses)  
✓ wee_tools JSON schema compliance  
✓ Runtime constants bounds checking  
✓ SSH command sanitization for bash execution  
✓ Permission-level enforcement across all operations  

### Files Added/Modified
- `tests/test_wee_runtime_agentic.py` — 932 lines, 68 test cases
- `tests/README_AGENTIC_TESTS.md` — comprehensive test documentation with quick-start guide
- `scripts/run_agentic_tests.sh` — runner script for unit/live test filtering

### Test Results
- **Unit Tests:** 54/54 pass
- **Live Ollama Tests:** 7/7 pass (requires Ollama on 192.168.1.101:11434)
- **Live OpenRouter Tests:** 4/4 skipped (no OPENROUTER_API_KEY in test environment)
- **Total:** 61 passed, 7 skipped, 0 failures

### Usage
```bash
# All tests
pytest tests/test_wee_runtime_agentic.py -v

# Unit only (fast, no API calls)
./scripts/run_agentic_tests.sh --unit

# Live Ollama only
./scripts/run_agentic_tests.sh --ollama

# Live OpenRouter only (requires OPENROUTER_API_KEY)
./scripts/run_agentic_tests.sh --openrouter
```

## [Issue #155] Bug: Devin Runtime Invalid Permission Mode causes Protocol Error
**Status:** QA Approved (Commit: a8311c5, Branch: issue/155)

### Summary
Fixed two root causes causing Devin runtime Protocol error when permission mode was set to "auto" (not a valid Devin CLI value).

### Root Cause 1: Invalid --permission-mode auto
Devin CLI only accepts normal, dangerous, bypass.
- run_devin(): Changed "auto" to "normal" for restricted/sandboxed sessions
- _run_background_task(): Unconditional "dangerous" for background tasks (always non-interactive)

### Root Cause 2: mode Param Not Propagated to run_devin()
- Added mode: str = "restricted" parameter to run_devin() signature
- Mode priority: explicit param > /mode in prompt > session data
- _dispatch_single_runtime() now correctly passes mode to run_devin()

### Files Changed
- agent_manager.py -- permission mode fix, mode param propagation
- tests/test_issue155_devin_permission_mode.py -- 17 new regression tests

### Tests
- 17 regression tests covering all fix paths
- Full suite: 1448 passed, 34 pre-existing failures, 0 new regressions

---

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
## [Issue #159] Feature: Fallback Runtime/Model for Scheduled Tasks
**Status:** ❌ QA Rejected (Round 1)  
**Rejection Date:** 2026-04-15  
**PR:** #163  
**Commit:** b5dddef  
**Blockers:** 1 MAJOR
**Dispatch:** wee-dev task bg_46083a8f

### QA Findings

**BLOCKER #1: WebUI Edit Form Pre-population Fails**
- File: `webui/dist/app.js`
- Issue: Form fails to pre-populate existing fallback_runtime and fallback_model values when editing scheduled task
- Impact: Users cannot see current fallback configuration, may accidentally clear it
- Fix: Add fallback fields to form pre-population logic, ensure values load in collapsible section

**MINOR #1: Cannot Clear Fallback via PUT Request**
- File: `scheduler/management.py` or `agent_manager.py`
- Issue: API does not support clearing fallback_runtime/fallback_model (cannot set to None)
- Fix: Support null/empty values in PUT request to clear fallback fields

**MINOR #2: 5 Unused Test Imports**
- File: `tests/test_issue159_scheduler_fallback.py`
- Fix: Remove unused imports

**MINOR #3: 1 Unused Test Variable**
- File: `tests/test_issue159_scheduler_fallback.py`
- Fix: Remove or use variable

**NITPICK #1: Type Hint Clarity (Non-blocking)**
- Optional improvement to type hints

### Test Results
- ✅ 37/37 targeted Issue #159 tests pass
- ✅ 0 new regressions (1478+ full suite clean)
- ✅ All 15 regex failure patterns correctly identified

### Implementation Highlights
- **Core Logic:** ✅ Solid — fallback detection, priority chain, all patterns working
- **15 Regex Patterns:** Rate limit (429), auth failure (401/403), service unavailable (502/503), timeout, connection errors, SSL failures, quota exceeded, + 8 more
- **Fallback Priority:** Per-job → global env vars → default fallback
- **API & Pydantic:** Routes correctly wired, validation working
- **Test Coverage:** Comprehensive failure scenarios covered

### Files Modified
- `agent_manager.py` — +6 lines (API route updates)
- `scheduler/executor.py` — +177/-50 lines (core fallback logic)
- `scheduler/management.py` — +6 lines (Pydantic fields)
- `webui/dist/app.js` — +56 lines (form + UI)
- `tests/test_issue159_scheduler_fallback.py` — +488 lines (37 tests)

### Next State
- wee-dev assigned to fix BLOCKER #1 + MINORs (task bg_46083a8f)
- Expected re-submission: 24-48 hours
- Full approval expected with next QA pass (Round 2) after fixes

---

## [Issue #113] Bug: Wee runtime synthesis caching + tool execution ordering
**Status:** ❌ QA Rejected  
**Rejection Date:** 2026-04-12  
**Blockers:** 2 MAJOR
**Dispatch:** wee-dev task bg_b1354034 (in progress)

### QA Findings

**BLOCKER #1: Hardcoded Test Path**
- File: `tests/test_issue113_synthesis.py`
- Issue: Test uses absolute hardcoded path instead of relative path
- Impact: Tests fail on any development system outside the primary dev host
- Fix: Replace hardcoded path with relative path or env var

**BLOCKER #2: Merge Conflict in wee_runtime.py**
- File: `wee_runtime.py`
- Issue: Unresolved merge conflict from Issue #128 integration (Token Usage Tracking)
- Impact: Branch cannot be rebased onto dev without manual conflict resolution
- Fix: Rebase onto current dev branch and manually resolve conflict

### Test Results
- ❌ 1207 tests pass (regression suite clean)
- ❌ 2 blockers prevent QA approval

### Next State
- wee-dev assigned to fix both blockers (task bg_b1354034)
- Re-dispatch to wee-qa for approval after fixes merged to dev

---

## [Issue #112] Bug: Wee runtime empty synthesis fallback
**Status:** ✅ QA Approved & Merged  
**Verdict Date:** 2026-04-12  
**Merge Date:** 2026-04-13  
**Commit:** 34c5876  
**PR:** #141  
**Test Results:** 12/12 issue tests pass, 0 regressions

### Summary
Fixed wee runtime handling of empty synthesis responses. When the LLM generates empty text after tool execution (no visible tokens), the wee runtime now gracefully falls back to the last tool result instead of returning an empty string.

### Implementation Details
**Fallback Logic (26 lines total):**
- After the agentic loop completes, if LLM synthesis is empty (whitespace-only or no output)
- Surface last tool result, truncated to 2000 chars
- Fallback message: `Tool result:\n{truncated_result}` or `(No response generated)` placeholder
- Applied in both `agent_manager.py` run_wee_native() and standalone `wee_runtime.py`

### Changes
- **agent_manager.py:** Added empty-synthesis fallback after agentic loop
- **wee_runtime.py:** Mirrored fallback logic for CLI standalone execution
- **tests/test_issue112_empty_synthesis.py:** 10 comprehensive regression tests
  - Single and multi-tool calls
  - Whitespace-only synthesis responses
  - Truncation at 2000 chars
  - SSE stream buffer edge cases
  - Thinking-only responses

### QA Verdict
- ✅ **APPROVED** — All functionality correct, no bugs found
- ✅ 12/12 issue tests pass
- ✅ 0 failures, 0 regressions
- ✅ Fallback verified with multiple tool call scenarios

### Status
- ✅ Merged to dev (PR #141)
- Ready for production (dev → main)

---

## [Issue #111] Bug: Wee runtime tool/skill execution audit (Label Sync)
**Status:** ✅ QA Approved  
**Verdict Date:** 2026-04-12  
**PR Status:** Already merged/approved in prior QA pass
**Label Update:** Added `wee-dev:qa-approved` label

### Summary
QA pass completed. Prior approval confirmed — label sync only. Issue #111 was already merged and approved in earlier QA pass. No new code changes.

### Note
- This was a label/tracking sync to ensure GitHub issue reflects current QA status
- All underlying code was previously verified and approved
- No re-QA of code required

---

## [Issue #128] Feature: Token Usage Tracking + Cost Estimation + WebUI Footer
**Status:** ✅ QA Approved — Round 2  
**Commits:** 3f036e1 (fixes), 9d6ecf5 (regression tests)  
**Verdict Date:** 2026-04-12  
**Next State:** Ready for production deployment (dev → main)

### Summary
Comprehensive token usage tracking across all runtimes (copilot-sdk, claude-sdk, openrouter, wee/ollama/openrouter/lmstudio). Calculates cumulative prompt/completion token counts and cost estimation with per-model pricing. WebUI footer displays live usage stats updated after each message.

### QA Verdict (Round 2)
- **Blocker B01 (Fixed):** `__WEE_META__` leak fixed — wee runtime now strips metadata before token counting (commit 3f036e1)
- **Minor M01 (Fixed):** Added `--session-id` tracking support to cost calculator (commit 3f036e1)  
- **Minor M02 (Non-blocking):** Dead code cosmetic issue — unfixed, no impact on functionality

### Test Results
- ✅ 31/31 Issue #128 specific tests pass
- ✅ 3/3 B01 regression tests pass
- ✅ 1212/1212 full suite pass (9 skipped)
- ✅ 0 failures, 0 regressions
- ✅ Token tracking verified across all runtimes
- ✅ Cost estimation accuracy validated

---

## [Issue #119] Feature: Wire Up OpenRouter in Wee Runtime UI
**Status:** ✅ QA Approved (Commit: 168a958, PR #121)

### Summary
Integrated OpenRouter as a primary model source for the wee runtime alongside Ollama. The wee runtime UI now displays OpenRouter models (llama-4-scout/maverick, gemma-3, qwen3, deepseek-r1, phi-4) grouped separately from Ollama models, with full model selection and execution support. Added 300-second cached model discovery with keyring-based API key storage and static fallback on network errors.

### Root Cause (Integration Gap)
The wee runtime model picker only showed Ollama models. OpenRouter integration existed in the backend but wasn't wired into the UI model dropdown or static model dispatch table.

### Solution

#### WEE_MODELS Constant Expansion
- Added OpenRouter models (6 models): llama-4-scout, llama-4-maverick, gemma-3, qwen3, deepseek-r1, phi-4
- Total wee model catalog: 16 models across 2 categories (Ollama + OpenRouter)
- Each model includes cost label + group field for UI grouping

#### OpenRouter Discovery & Caching
- `_fetch_openrouter_pricing()` — Fetches model catalog from OpenRouter API (OPENROUTER_API_KEY env var)
- 300-second TTL cache to minimize API calls
- Keyring vault storage for API key (no secrets in env vars)
- Static fallback on network error (prevents UI model picker from breaking)

#### WebUI Model Grouping
- `optgroup` rendering in app.js model dropdown
  - "Ollama Models" group
  - "OpenRouter Models" group
- Group field populated in /api/v1/models endpoint
- Smooth fallback when group data unavailable

#### Model Dispatch Wiring
- OpenRouter models added to `static_alias_map` (enables model name resolution)
- OpenRouter models added to `env_alias_map` (enables env-override resolution)
- `fetch_wee_models()` now returns both Ollama + OpenRouter in single list
- Session model validation handles OpenRouter model selection

### Files Changed
- `agent_manager.py` — WEE_MODELS expansion, _fetch_openrouter_pricing(), keyring API key handling, optgroup support in /api/v1/models
- `webui/dist/app.js` — optgroup rendering for model categories
- `tests/test_issue119_openrouter.py` — 35 new regression tests

### Tests
- 35 new tests covering:
  - OpenRouter model discovery & caching
  - Keyring-based API key storage
  - Static fallback on network error
  - Model grouping in UI
  - Session model validation with OpenRouter models
- Total: 1432 passed, 35 new issue tests, 0 regressions

### Non-Blocking Finding
- M-1 (MINOR): When OpenRouter HTTPS call fails (network error) while keyring API key is configured, live Ollama results may be discarded if synthesis times out. Acceptable trade-off — graceful degradation to static model list takes priority.

---


## [Issue #118] Bug: Wee Runtime Ignores Selected Ollama Model
**Status:** Implementation complete — 30 new tests, 1324 total pass (0 new regressions)

### Problem
When using the wee runtime, selecting any model (e.g., gemma4, qwen) in the UI model switcher had no effect — Ollama always ran `granite3.3-tuned` (the default). The selected model was dropped because "wee" was missing from multiple model dispatch/validation/resolution paths in `agent_manager.py`.

### Root Causes (8 bugs)
1. **No WEE_MODELS constant** — Unlike other runtimes (claude, gemini, codex, etc.), `wee` had no static model catalog. The dispatch table used an inline lambda with a hardcoded subset.
2. **"wee" not in `known_runtimes`** — `/api/v1/models?runtime=wee` returned "Unknown runtime" error, so the UI model picker was empty.
3. **"wee" not in `static_alias_map`** — `get_model_from_name()` couldn't resolve wee model aliases (gemma, granite, qwen) to full model IDs.
4. **`_get_model_description` had empty dict for "wee"** — All wee models showed raw IDs instead of human-readable labels.
5. **No `fetch_wee_models()` method** — No live Ollama discovery; models were hardcoded in a lambda.
6. **Session validation didn't check model validity** — When switching to wee, stale copilot models (e.g., gpt-5-mini) persisted without validation.
7. **No `WEE_MODELS_JSON` env var support** — Unlike other runtimes, wee had no env override for custom model lists.
8. **No `_env_wee_models` caching** — Env-provided models weren't cached for alias/description resolution.

### Solution

#### WEE_MODELS Constant
- Added `WEE_MODELS` class constant with 16 models across 2 categories:
  - **Ollama Models** (10): gemma4 variants, granite3.3, qwen, llama-scout, hermes3
  - **OpenRouter Models** (6): llama-4-scout/maverick, gemma-3, qwen3, deepseek-r1, phi-4

#### fetch_wee_models() Method
- Resolution order: `WEE_MODELS_JSON` env var → live Ollama discovery → static fallback
- Live discovery via `httpx.get("http://192.168.1.101:11434/api/tags")`
- Env models cached in `_env_wee_models` for alias/description resolution

#### Model Dispatch Wiring
- "wee" added to `known_runtimes` set (enables `/api/v1/models?runtime=wee`)
- "wee" added to `static_alias_map` (enables alias resolution)
- "wee" added to `env_alias_map` (enables env-override resolution)
- `_get_model_description` references `WEE_MODELS` (enables human labels)
- Session validation uses `get_model_from_name()` check (replaces empty-string check)
- Debug logging added to `run_wee_native()` for model tracing

### Files Changed
- `agent_manager.py`: WEE_MODELS constant, fetch_wee_models(), 6 wiring fixes, debug logging
- `tests/test_issue118_model_selection.py`: 30 new regression tests
- `tests/test_issue105_wee_runtime_stall.py`: Updated model category assertions for new structure

### Tests
- 30 new tests covering all 8 bug fixes
- Also fixes 19 previously-failing issue97 tests
- Full suite: 1324 passed, 33 failed (all pre-existing), 9 skipped


## [Issue #105] Bug: Wee Runtime Stalls with Ollama gemma4:e4b
**Status:** QA Approved — Commit 07733dc on `issue/105` branch — 15 new tests, 1157 total pass

### Problem
The `wee` runtime stalled indefinitely when routing to Ollama. Three root causes identified:

1. **Wrong Ollama port** — `agent_manager.py` and `wee_runtime.py` used port `11436` instead of the standard `11434`, causing all Ollama requests to silently fail.
2. **Missing connect timeout** — OpenAI client had no `httpx.Timeout(connect=...)` and `max_retries` defaulted to 2, causing long stalls (retry backoff) instead of fast-fail when connecting to a wrong/unavailable endpoint.
3. **Broken model resolution** — `get_models_for_runtime('wee')` returned tuples instead of flat strings; `get_model_from_name()` used longest-match instead of exact-match after stripping provider prefix (`ollama/`), causing model names to resolve incorrectly.

### Solution

#### Fix 1 — Correct Ollama Port
- `agent_manager.py` PRESETS: `http://192.168.1.101:11434/v1` (was `11436`)
- `wee_runtime.py` PRESETS: `http://192.168.1.101:11434/v1` (was `11436`)

#### Fix 2 — httpx Timeout + max_retries=0
- OpenAI client now initialized with `httpx.Timeout(connect=15.0, read=300.0, write=30.0, pool=10.0)`
- `max_retries=0` prevents retry backoff masking connection errors

#### Fix 3 — Model Resolution
- `get_models_for_runtime('wee')` returns flat strings (not tuples)
- `get_model_from_name()` Step 2 strips provider prefix (`ollama/`, `openrouter/`, `lmstudio/`) before matching; prefers exact match, then shortest match — not longest
- E2E test confirms `ollama/gemma4:e4b` routes to Ollama at `192.168.1.101:11434` correctly

### Files Changed
- `agent_manager.py` — port fix, timeout/retries, model resolution (3 locations)
- `wee_runtime.py` — port fix, timeout/retries
- `tests/test_issue105_wee_ollama_stall.py` — 15 new tests

### Known Gap (Pre-existing, not introduced by this fix)
- `GET /api/v1/models?runtime=wee` returns an "unknown runtime" error — unrelated to this fix, tracked separately


## [Issue #93] Bug: No way to unlock secret store via WebUI
**Status:** QA Pending — Commit 423668a on `issue/93` branch — 26 new tests, 1129 total pass
### [Issue #128] Feature: Token Usage Tracking + Cost Estimation + WebUI Footer
**Status:** ✅ Implemented

### Summary
Adds end-to-end token usage tracking for the wee runtime: token counts are captured from the OpenAI streaming API, costs are calculated using OpenRouter pricing (with 1h cache), and displayed in the WebUI message footer. Usage is logged to a JSONL file and exposed via a new `/api/v1/usage` endpoint.

### Changes

**wee_runtime.py (standalone CLI)**
- Added `stream_options={"include_usage": True}` to OpenAI streaming calls
- Parses `chunk.usage` from final streaming chunk (prompt/completion/total tokens)
- `fetch_openrouter_pricing()` — fetches model pricing from OpenRouter API, caches 1h in `/tmp/openrouter_pricing.json`
- `calculate_cost()` — returns `(cost_usd, label)` where label is `local`, `free`, or `$X.XXXX`
- `log_token_usage()` — appends entry to `~/.copilot/logs/token_usage.jsonl`
- Outputs `__WEE_META__ {...}` line at end of stream (stripped by backend before sending to UI)
- Fixed Ollama port: 11436 → 11434

**agent_manager.py (backend)**
- `_fetch_openrouter_pricing()` — same 1h-cached pricing fetch (for interactive sessions)
- `_calculate_wee_cost()` — cost+label for wee/ollama/openrouter models
- `_calculate_anthropic_cost()` — cost+label for claude-sdk models
- `_get_cost_label()` — formats cost as `$0.0001` or `free`
- `_log_token_usage()` — appends to `logs/token_usage.jsonl`
- `run_wee_native()` — captures usage from final streaming chunk, stores `wee_meta` in session
- `run_claude_sdk()` — captures usage from `ResultMessage`, stores `wee_meta` in session
- SSE `done_payload` — includes `wee_meta` (tokens, cost, model), cleared after send
- **GET /api/v1/usage** — aggregates JSONL log by model; supports `?period=today|7d|30d`

**webui/dist/app.js (frontend)**
- `buildTimingText(elapsedSec, weeMeta)` helper — returns footer string:
  - Paid: `Generated in 3.4s · 229 tokens · $0.0001`
  - Free: `Generated in 3.4s · 229 tokens · free`
  - Local (Ollama): `Generated in 3.4s · 229 tokens · local`
  - No data: `Generated in 3.4s`
- Streaming done path updated to pass `evt.wee_meta` to `buildTimingText`
- `renderMessage()` signature updated with `weeMeta` param; timing block uses `buildTimingText`

**Tests**
- `tests/test_issue128_token_usage.py` — 28 new tests
- `tests/test_wee_native_runtime.py` — fixed Ollama port assertion (11436 → 11434)
- Total: 1170 passed, 9 skipped (no regressions)


# [Issue #100] Feature: GitHub Issues Integration for TODO Endpoints

## [Issue #115] Feature: Inline Expandable Tool Call Blocks
**Status:** ✅ QA Approved (Commit: a85e5b5)

### Summary
Tool call events (started/completed) from all runtimes (copilot-sdk, claude-sdk, claude, gemini) now emit inline expandable blocks in the WebUI streaming panel. Each block shows a ▶ disclosure triangle; clicking expands a scrollable output pane showing the tool result. Silent mode hides all tool-call blocks. CSS handles error highlighting and dark/light themes.

### Changes
- **app.js** — `insertToolCallBlock()` creates collapsible TC block; `completeToolCallBlock()` fills output and toggles expand/collapse. Null guard restored to prevent crash on late/duplicate events.
- **agent_manager.py (copilot-sdk)** — `TOOL_EXECUTION_COMPLETE` event now includes `output` field extracted from `event.data.output/result/content` (up to 2000 chars)
- **agent_manager.py (claude-sdk)** — `ToolResultBlock` handler now populates `output` field (was incorrectly putting content in `input` with 200-char limit)
- **agent_manager.py (claude runtime)** — `tool_result` block SSE event now includes `output` field with list-join support (up to 2000 chars)
- **agent_manager.py (gemini)** — Tool output truncation raised from `[:500]` to `[:2000]`
- **app.css** — `.tc-block`, `.tc-toggle`, `.tc-output`, `.tc-error`, `.tc-expanded` styles; silent mode hides `.tc-block`

### QA Round 2 Fixes (Commit a85e5b5)
- M-1: Restored `if (!row) return;` null guard in `completeToolCallBlock`
- M-2: Added `output` field to copilot-sdk `TOOL_EXECUTION_COMPLETE` event
- M-3: claude-sdk `ToolResultBlock` — moved content to `output` field, cleared `input`, raised limit to 2000
- M-4: claude runtime `tool_result` — added `_tr_content` extraction with list-join to `output` field
- m-5 (MINOR): gemini `[:500]` → `[:2000]` confirmed applied

### Tests
- **39 new tests** in `tests/test_issue115_expandable_tool_calls.py`
  - `TestSanitizeToolCallOutput` — sanitizer passthrough and truncation
  - `TestCopilotSdkToolOutput` — output field extraction, fallbacks, 2000-char limit
  - `TestClaudeSdkToolOutput` — list content join, string content, empty content
  - `TestClaudeRuntimeToolResult` — list/string/error tool_result handling
  - `TestGeminiOutputLimit` — confirms 2000-char limit
  - `TestFrontendToolCallBlockStructure` — started/completed event handling, expand/collapse, markdown preservation
  - `TestCssExpandableStyles` — all CSS rules verified
  - `TestSilentModeIntegration` — silent mode hides tool call blocks

### Test Results
- **1181 total tests pass**, 9 skipped, 0 failures (no regressions)
- All 39 Issue #115 tests pass (100%)
- No BLOCKERs, no MAJORs, no MINORs


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

**Status:** ✅ QA Approved  
**Commits:** 9498a4f  
**Verdict Date:** 2026-04-12  
**PR:** #121 (issue/119 → dev)  
**Next State:** Ready for merge (dev → main)

### Summary
Complete OpenRouter integration in wee runtime with UI model selection. Adds `OPENROUTER_POPULAR_MODELS` constant (12 curated models), `WEE_MODELS` dict with grouping, `fetch_wee_models()` with 300s TTL cache and keyring API key retrieval, `/api/v1/models` endpoint `group` field, and frontend optgroup rendering for model dropdown.

### Features
- **Backend:** OpenRouter API key lookup via keyring, 300s cache, graceful fallback to static models
- **API:** `GET /api/v1/models?runtime=wee` returns 15 models across 2 groups (Ollama + OpenRouter)
- **Frontend:** Grouped model dropdown with `<optgroup>` headers
- **Models:** 12 popular OpenRouter models + 3 local Ollama models available

### Test Results
- ✅ 34/34 Issue #119 specific tests pass
- ✅ 1176/1176 full suite pass (9 skipped)
- ✅ 0 failures, 0 regressions
- ✅ API grouping verified, keyring integration validated

### Files Modified
- agent_manager.py: `OPENROUTER_POPULAR_MODELS`, `WEE_MODELS`, `fetch_wee_models()`, model endpoint, session dispatch
- app.js: optgroup rendering for grouped models
- tests/test_issue119_openrouter.py: 34 comprehensive tests

---

## [Issue #118] Bug Fix: Wee Runtime Model Selection & Ollama Port
**Status:** ✅ QA Approved  
**Commits:** fbd6c10  
**Verdict Date:** 2026-04-12  
**PR:** #120 (issue/118-119 → dev) — **Note:** PR #121 superset includes this fix  
**Next State:** Ready for merge (dev → main via PR #121)

### Summary
Fixed 8 critical bugs in wee runtime model dispatch pipeline:
1. `get_models_for_runtime('wee')` returned tuples instead of flat strings
2. `wee` missing from `known_runtimes` in `/api/v1/models`
3. `get_model_from_name()` crashed on tuple inputs
4. Session validation allowed stale copilot models on wee runtime
5. Ollama port 11436→11434 in agent_manager.py
6. Ollama port 11436→11434 in wee_runtime.py
7. `static_alias_map` missing `wee` entry
8. `_get_model_description()` missing wee model mapping

### Root Cause
Ollama runs on port 11434 (standard), not 11436. Model resolution pipeline had incomplete wee runtime support — missing constants in multiple locations, incorrect port configuration, and incomplete static model mapping.

### Fixes Applied
- Added `WEE_MODELS` constant with proper structure
- Corrected Ollama port in both agent_manager.py and wee_runtime.py
- Fixed `get_models_for_runtime()` to return flat strings via `_static_models_to_dict()`
- Added `wee` to `known_runtimes`, `static_alias_map`, and `_get_model_description()`
- Session validation now uses `get_model_from_name()` for wee model verification

### Test Results
- ✅ 29/29 Issue #118 specific tests pass
- ✅ 1171/1171 full suite pass (9 skipped)
- ✅ 0 failures, 0 regressions
- ✅ All 8 bugs verified fixed, port configuration validated

### API Verification
`GET /api/v1/models?runtime=wee` returns all 4 models with correct labels:
- `ollama/gemma4:e4b` — Ollama Gemma 4 E4B (local)
- `ollama/qwen3` — Ollama Qwen 3 (local)
- `ollama/granite3.3-tuned` — Ollama Granite 3.3 Tuned (local)
- `openrouter/meta-llama/llama-4-scout` — Llama 4 Scout via OpenRouter

### Minor Notes
- `get_model_from_name('', 'wee')` returns truthy for empty string (latent bug, not triggered in production due to short-circuit guard)
- Debug logging uses `print(..., file=sys.stderr)` on every wee call (adds journal noise, not blocking)

---

## [Issue #115] Feature: Inline Expandable Tool Call Blocks
**Status:** ✅ QA Approved — Pass 2 (Commit: a85e5b5)  
**Verdict Date:** 2026-04-12  
**Next State:** Ready for PR dev→main

### Summary
WebUI feature for inline expandable tool call blocks with markdown output rendering. Displays tool invocations with collapsible output panels. All 5 MAJOR/MINOR findings from Round 1 QA rejection fixed. Feature fully functional and ready to merge.

### Test Results
- ✅ 39/39 issue-specific tests pass
- ✅ 1181/1181 full suite pass
- ✅ 9 skipped (pre-existing, deterministic)
- ✅ 0 failures, 0 regressions

### Round 2 Fixes Applied
1. **app.js L2017:** Restored null guard in `completeToolCallBlock()` — prevents crash when toolId not in DOM
2. **agent_manager.py L6574:** Added `output` field to copilot-sdk TOOL_EXECUTION_COMPLETE events
3. **agent_manager.py L6856:** Moved claude-sdk `ToolResultBlock.content` to `output` key (2000 char limit)
4. **agent_manager.py L5782:** Added `output` extraction from claude runtime `tool_result` blocks
5. **agent_manager.py L5866:** Bumped gemini output limit from 500 to 2000 characters

---

## [Issue #114] Feature: Wee runtime auto-discover models from API hosts
**Status:** ❌ QA Rejected — Offline fallback bug (Commit: 4a8b587)
**Verdict Date:** 2026-04-11  
**Next State:** wee-dev fixing cache-write ordering bug, will re-dispatch for QA approval

### Summary
Feature adds dynamic model discovery for `wee` runtime via new `wee_model_discovery.py` module. Core functionality works — Ollama discovery, enriched `/api/v1/wee/models` endpoint, refresh endpoint, TTL cache all verified. **Offline fallback to cached models is broken** due to cache being overwritten before reading old value.

### QA Finding — MAJOR Bug
**File:** `wee_model_discovery.py`, lines 151–166  
Root cause: `self._cache[cache_key]` overwritten with empty list *before* reading old cached value. When a host goes offline after being online, the fallback never triggers because the old cache is already replaced with `(now, [])`.

**Required Fixes (wee-dev):**
1. Save old cache entry before overwriting it
2. Add regression test: host online → goes offline → verify cached models returned
3. (MINOR) `discover_all_enriched()` ignores TTL / `force` parameter

### Test Results
- ✅ 29/29 new tests pass (test_issue114_model_discovery.py)
- ✅ 1171/1171 full suite pass
- ✅ 0 failures, 0 regressions
- ✅ Live API tests: discovery, enriched endpoint, refresh, TTL cache, force bypass all working

---

## [Issue #111] Bug: Wee runtime tool/skill execution audit
**Status:** ✅ QA Approved (Commit: 502f267)

### Summary
Fixed critical bugs in wee native runtime tool and skill execution. Corrected argument order in context prompt building, added tool availability declaration to system prompt so local models (Ollama) recognize bash/python tools, and reorganized context prompt build to occur after model resolution.

### Root Causes & Fixes

1. **Wrong Argument Order in build_agent_context_prompt()**
   - `run_cursor()` and `run_wee_native()` passed arguments in incorrect order to build_agent_context_prompt()
   - Method expected (model, runtime, agent_name, tools) but received arguments in wrong sequence
   - Fixed in agent_manager.py: corrected argument order in both function calls

2. **Missing Tool Availability Declaration**
   - Local models (Ollama, LM Studio) lacked knowledge of available tools (bash, python)
   - Added `_wee_augment_system_prompt_with_tools()` function to inject [Available Tools] section
   - System prompt now explicitly lists bash and python tools with usage format
   - Enables local models to recognize and utilize tool-calling capabilities

3. **Context Prompt Build Timing**
   - Context prompt was being built before model resolution completed
   - Moved context prompt build to occur after model is fully resolved
   - Ensures correct model context is used in tool/skill execution

### Changes
- **agent_manager.py** — Fixed argument order in run_cursor() and run_wee_native() calls to build_agent_context_prompt()
- **agent_manager.py** — Added _wee_augment_system_prompt_with_tools() to inject tool availability declaration
- **agent_manager.py** — Reorganized model resolution and context prompt build sequence
- **wee_runtime.py** — Aligned tool execution with augmented system prompt format

### Tests
- **1197 total tests pass**, 9 skipped, 0 failures
- **7 new regression tests** for tool/skill execution audit
- Tests validate correct argument ordering, tool availability declaration, and model resolution timing
- Local model (Ollama) tool-calling verified end-to-end

### QA Notes
- wee-qa completed comprehensive tool/skill execution verification
- Tested with various model endpoints (Ollama, LM Studio, OpenRouter)
- Tool calling validated with bash and python tools on local models
- System prompt augmentation verified for all runtime configurations

### PR Status
- Issue #111 approved by QA (commit 502f267 on branch issue/107-108-109)
- Ready for PR: `issue/107-108-109` → `dev`

---

## [Issue #107] Bug: Wee runtime multi-turn history loss
**Status:** ✅ QA Approved (Commit: 83eb91e)

### Summary
Fixed wee native runtime losing conversation history in multi-turn interactions. The runtime now correctly maintains and passes session history between turns, preventing context loss in interactive agent workflows.

### Root Cause & Fix
- Multi-turn session history was not being preserved between turns in wee_runtime.py
- Added persistent message history accumulation across session turns
- Fixed in wee_runtime.py: enhanced session state management to retain full conversation context

### Changes
- **wee_runtime.py** — Added message history preservation across turns
- Session history now correctly accumulates and is passed to subsequent API calls
- Multi-turn agentic loops now maintain full context

### Tests
- **1165 total tests pass**, 23 new tests added
- **8 new regression tests** for multi-turn history preservation
- Live integration tests validate context retention across 10+ turn interactions

### QA Notes
- wee-qa completed comprehensive multi-turn verification
- Tested with various model endpoints (Ollama, LM Studio, OpenRouter)
- Context retention validated in interactive agent workflows

---

## [Issue #108] Bug: Wee runtime tool-call agentic loop
**Status:** ✅ QA Approved (Commit: 83eb91e)

### Summary
Fixed wee native runtime's tool-call agentic loop to properly execute and continue through multiple tool calls until completion. The runtime now correctly handles the complete cycle of tool invocation, result processing, and continuation.

### Root Cause & Fix
- Tool-call agentic loop was not iterating correctly through multiple tool invocations
- Added proper loop continuation logic and result accumulation
- Fixed in wee_runtime.py: enhanced tool execution cycle and message streaming

### Changes
- **wee_runtime.py** — Improved tool-call loop control flow
- Tool results are now correctly accumulated and fed back into the loop
- Loop continues until stop_reason indicates completion (no more tool calls)

### Tests
- **1165 total tests pass**, 23 new tests added
- **7 new regression tests** for tool-call agentic loop execution
- Tested with multi-step workflows requiring sequential tool invocations

### QA Notes
- wee-qa verified tool-call execution through complete agentic loops
- Tested with complex multi-step workflows and function calls
- All tool execution patterns validated

---

## [Issue #109] Feature: SSE tool events for wee runtime
**Status:** ✅ QA Approved (Commit: 83eb91e)

### Summary
Added Server-Sent Events (SSE) support for tool execution events in wee native runtime. Tool events are now streamed in real-time (TOOL_EXECUTION_START, TOOL_EXECUTION_COMPLETE) providing better observability and client-side progress tracking.

### Feature Implementation
- Tool execution lifecycle events now emitted as SSE events
- TOOL_EXECUTION_START: emitted when tool invocation begins
- TOOL_EXECUTION_COMPLETE: emitted when tool returns results
- Events include tool name, input parameters, and execution status

### Changes
- **wee_runtime.py** — Added SSE event emission for tool lifecycle
- Event streaming matches copilot-sdk and claude-sdk patterns
- Consistent event naming and format across all runtimes

### Tests
- **1165 total tests pass**, 23 new tests added
- **8 new tests** for SSE tool event streaming
- Events validated for both successful and failed tool invocations

### QA Notes
- wee-qa verified SSE event emission for all tool types
- Tested with streaming client consumers
- Event timing and format consistency validated

---

## [Issue #105] Bug: Wee runtime stall with Ollama gemma4:e4b
**Status:** ✅ QA Approved (Commit: 07733dc)

### Summary
Fixed critical wee runtime stall issue when using Ollama models. Identified and resolved three root causes: incorrect Ollama port configuration, missing connection timeout and retry limits causing infinite hangs, and model list format incompatibility with substring matching logic.

### Root Causes & Fixes

1. **Wrong Ollama Port (11436 → 11434)**
   - Ollama on kubuntu (192.168.1.101) runs on standard port 11434
   - agent_manager.py and wee_runtime.py had hardcoded port 11436
   - Now correctly points to port 11434 in both PRESETS configurations
   - Fixed in agent_manager.py:7630 and wee_runtime.py:20

2. **Missing Connection Timeout & Retry Limits**
   - OpenAI client had no timeout constraint on hanging connections
   - Unlimited retries could cause indefinite stalls on connection failures
   - Now enforced: `httpx.Timeout(connect=15s)` and `max_retries=0`
   - Enables fail-fast behavior on wrong/unreachable endpoints
   - Applied to wee_runtime.py initialization

3. **Model List Format & Matching Logic**
   - `get_models_for_runtime('wee')` was returning tuples instead of flat strings
   - `get_model_from_name()` Step 2 expected flat strings (line 4095-4108)
   - Substring matching now prefers exact match over longest match
   - Fixed in agent_manager.py model resolution functions

### Changes
- **agent_manager.py:7630** — Updated PRESETS with correct Ollama port 11434
- **wee_runtime.py:20** — Updated PRESETS with correct Ollama port 11434
- **wee_runtime.py initialization** — Added httpx.Timeout(connect=15s) and max_retries=0
- **agent_manager.py model functions** — Fixed model list return format (flat strings) and matching preference (exact > shortest)

### Tests
- **1157 total tests pass**, 9 skipped, 0 failures
- **15 new regression tests** specifically for wee runtime Ollama integration
- Live integration test passed with Ollama gemma4:e4b model
- All existing tests continue to pass with new fixes

### QA Notes
- wee-qa completed comprehensive verification of all three fixes
- Tested with actual Ollama gemma4:e4b model on kubuntu
- Connection timeout and retry limits validated in isolation
- Model resolution tested with various model name formats and providers

### Use Cases
```bash
# Wee runtime now reliably handles Ollama models
# Connection fails fast (15s timeout) instead of hanging indefinitely
curl -X POST -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"agent": "orchestrator", "runtime": "wee", "model": "ollama/gemma4:e4b", "prompt": "..."}' \
  https://127.0.0.1:8000/api/v1/background-tasks
```

### PR Status
- Issue #105 approved by QA (commit 07733dc on branch issue/105)
- Ready for PR: `issue/105` → `dev`

---

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
