# Wee-Dev Blockers Log

> **Purpose:** Structured format for wee-dev to document blockers during background task runs.
> Updated during each wee-dev run as blockers arise. Provides transparency on what's blocking progress and how to escalate.

---

## Blocker Entry Format

```markdown
### [BLOCKER-YYYY-MM-DD-NNN] Short description

- **Timestamp:** YYYY-MM-DD HH:MM UTC
- **Task/Issue:** #issue_number or task description
- **Type:** missing-key | needs-approval | needs-clarification | resource-unavailable | dependency-missing | permission-denied | other
- **What's Needed:** Clear, actionable description of what's required to unblock
- **Channel Used:** telegram | approval-flow | github-issue | none
- **Status:** OPEN | WAITING_FOR_KEY | WAITING_FOR_APPROVAL | ESCALATED | WORKAROUND_IN_PLACE | RESOLVED
- **Resolution:** (filled when resolved) What was done to resolve
```

---

## Blocker Types

| Type | Description | Example |
|------|-------------|---------|
| `missing-key` | API key, token, or credential not available | OpenAI API key not set in env |
| `needs-approval` | Requires Foster's explicit go-ahead | Architecture decision, breaking change |
| `needs-clarification` | Ambiguous requirement or conflicting specs | Unclear which endpoint format to use |
| `resource-unavailable` | External service or host is down/unreachable | Dev host SSH connection refused |
| `dependency-missing` | Required package, service, or module not installed | Python package not in requirements.txt |
| `permission-denied` | Insufficient permissions for an operation | Cannot write to protected directory |
| `other` | Anything not covered above | Describe in detail |

---

## Communication Decision Tree

Use this flowchart to determine the right escalation channel:

```
Is the blocker URGENT (blocks all further work)?
├── YES → Is Foster currently online (recent Telegram activity)?
│   ├── YES → 📱 Telegram (immediate ping)
│   └── NO  → 📱 Telegram + update BLOCKERS.md, continue other work
└── NO  → Can you apply a WORKAROUND and continue?
    ├── YES → Apply workaround, log in BLOCKERS.md, continue
    │         └── Later: 📋 GitHub Issue for proper fix
    └── NO  → Does it require a USER DECISION (approval, preference)?
        ├── YES → Is it time-sensitive (< 1 hour window)?
        │   ├── YES → 📱 Telegram + approval-flow skill
        │   └── NO  → 📋 GitHub Issue with options laid out
        └── NO  → Log in BLOCKERS.md, file 📋 GitHub Issue
```

### Channel Summary

| Channel | When to Use | Response Time |
|---------|-------------|---------------|
| 📱 **Telegram** | Urgent blockers, immediate decisions, missing credentials | Minutes |
| 🔐 **approval-flow** | Needs explicit approve/reject (architecture, breaking changes) | Hours |
| 📋 **GitHub Issue** | Non-urgent clarifications, design decisions, feature scoping | Days |

### Notification Format (Telegram)

Keep Telegram notifications to **one line max**:
```
⛔ Blocker: [BLOCKER-ID] — [short description]. Need: [what's needed].
```

---

## Status Tracking

| Status | Meaning |
|--------|---------|
| `OPEN` | Blocker identified, not yet escalated |
| `WAITING_FOR_KEY` | Credential/key requested from Foster |
| `WAITING_FOR_APPROVAL` | Decision pending Foster's approval |
| `ESCALATED` | Raised via Telegram or GitHub issue |
| `WORKAROUND_IN_PLACE` | Temporary fix applied, proper fix still needed |
| `RESOLVED` | Blocker cleared, work can proceed |

---

## Foster's Responsibilities

1. **Check BLOCKERS.md daily** — Review open blockers when checking on wee-dev progress
2. **Respond to Telegram pings immediately** — Urgent blockers need fast turnaround
3. **Review blocked GitHub issues** — Provide clarification or approval within 24 hours
4. **Check HEARTBEAT.md** — If wee-dev goes silent, check for undocumented blockers
5. **Provide credentials proactively** — When filing issues that need API keys, include them or note where they'll be stored

---

## Worked Examples

### Example 1: Immediate — Missing API Key

**Scenario:** wee-dev is implementing the OpenAI SDK runtime and needs an API key.

```markdown
### [BLOCKER-2026-04-05-001] OpenAI API key not configured for dev environment

- **Timestamp:** 2026-04-05 14:30 UTC
- **Task/Issue:** #72 OpenAI SDK Runtime
- **Type:** missing-key
- **What's Needed:** OPENAI_API_KEY env var set on 192.168.1.100 or stored via /secret set
- **Channel Used:** telegram
- **Status:** WAITING_FOR_KEY
- **Resolution:** —
```

**Action taken:**
1. Logged blocker above
2. Sent Telegram: `⛔ Blocker: BLOCKER-2026-04-05-001 — OpenAI API key not set on dev host. Need: /secret set OPENAI_API_KEY <key>`
3. Continued working on non-API-dependent parts of the feature
4. Once Foster provides key → update status to `RESOLVED`, note resolution

---

### Example 2: Clarification Needed — Escalate to GitHub Issue

**Scenario:** wee-dev is implementing a new endpoint but the issue description is ambiguous about the response format.

```markdown
### [BLOCKER-2026-04-05-002] Unclear response format for /api/v1/agents/status endpoint

- **Timestamp:** 2026-04-05 16:00 UTC
- **Task/Issue:** #75 Agent Status Endpoint
- **Type:** needs-clarification
- **What's Needed:** Should response include full session history or just current status? Issue says "status" but examples show history.
- **Channel Used:** github-issue
- **Status:** ESCALATED
- **Resolution:** —
```

**Action taken:**
1. Logged blocker above
2. Posted comment on GitHub issue #75 with the specific question and two proposed options
3. Labeled issue with `needs-clarification`
4. Moved to next item in work queue — no point waiting
5. Once Foster comments → update status, implement chosen option

---

### Example 3: Dependency Blocker — Workaround and Continue

**Scenario:** wee-dev needs a Python package that isn't in requirements.txt and pip install fails due to a build dependency.

```markdown
### [BLOCKER-2026-04-05-003] pydantic-settings package fails to install — missing Rust compiler

- **Timestamp:** 2026-04-05 18:00 UTC
- **Task/Issue:** #78 Config Refactor
- **Type:** dependency-missing
- **What's Needed:** Install Rust toolchain on 192.168.1.100 or use pre-built wheel
- **Channel Used:** none
- **Status:** WORKAROUND_IN_PLACE
- **Resolution:** Used python-dotenv as temporary config loader. Filed GitHub issue for proper pydantic-settings setup.
```

**Action taken:**
1. Logged blocker above
2. Found workaround: used `python-dotenv` instead of `pydantic-settings` for config loading
3. Implemented feature with workaround in place
4. Filed separate GitHub issue for installing Rust toolchain on dev host
5. Added TODO comment in code: `# TODO: Replace with pydantic-settings once Rust toolchain is available (see #79)`
6. Continued with feature implementation — no escalation needed

---

## Active Blockers

<!-- Add active blocker entries below this line. Move resolved blockers to the Resolved section. -->

*No active blockers.*

---

## Resolved Blockers

<!-- Move resolved blockers here with their resolution notes. -->

*No resolved blockers yet.*
