# Wee Orchestrator Release Documentation

This directory contains comprehensive documentation for the Wee Orchestrator release workflow implemented on May 2, 2026.

## Documentation Files

### For Everyone - Start Here
- **[RELEASE_SUMMARY.txt](RELEASE_SUMMARY.txt)** (30 KB)
  - Complete overview in plain text
  - Best for understanding the entire system at once
  - Includes all key information in one document

### By Role

#### For Foster (Decision Maker)
- **[RELEASE_QUICK_REFERENCE.md](RELEASE_QUICK_REFERENCE.md)** (5 KB)
  - Quick commands: `/release`, `/background status`, etc.
  - Notifications you'll receive and what they mean
  - Troubleshooting flowchart

#### For Developers
- **[RELEASE_QUICK_REFERENCE.md](RELEASE_QUICK_REFERENCE.md)** → Development section
  - How to commit directly to dev (no feature branches)
  - Testing locally before commit

#### For wee-dev Agent
- **[RELEASE_QUICK_REFERENCE.md](RELEASE_QUICK_REFERENCE.md)** → wee-dev section
  - When dispatched after test failure
  - Bug fix workflow and verification process

#### For wee-qa (If Needed)
- **[RELEASE_WORKFLOW.md](RELEASE_WORKFLOW.md)** → "When Blocked or Approaching Timeout"
  - Optional pre-release validation if Foster requests it

### By Topic

#### Full Workflow Explanation
- **[RELEASE_WORKFLOW.md](RELEASE_WORKFLOW.md)** (15 KB)
  - Complete step-by-step workflow
  - Development process
  - Release process with all three stages
  - Communication & notifications
  - Failure scenarios & recovery
  - Troubleshooting section

#### Architecture & Visuals
- **[RELEASE_ARCHITECTURE.md](RELEASE_ARCHITECTURE.md)** (14 KB)
  - Visual flowcharts (ASCII art)
  - State machine diagrams
  - Component interaction diagrams
  - Example timeline (Monday-Friday development cycle)
  - Error handling matrix
  - Performance characteristics

#### Quick Reference
- **[RELEASE_QUICK_REFERENCE.md](RELEASE_QUICK_REFERENCE.md)** (5 KB)
  - One-page per role
  - Quick commands
  - Key files list
  - Glossary

---

## Quick Start

### If you just want to understand what happens:
1. Read: **RELEASE_SUMMARY.txt** (5 minutes)
2. Check: **RELEASE_QUICK_REFERENCE.md** (2 minutes)

### If you want full details:
1. Read: **RELEASE_WORKFLOW.md** (main document)
2. Review: **RELEASE_ARCHITECTURE.md** (diagrams)
3. Reference: **RELEASE_QUICK_REFERENCE.md** (commands)

### If you're waiting for wee-dev to fix bugs:
1. Read: "What happens when tests fail" section in RELEASE_WORKFLOW.md
2. Use: `foster /background status bg_XXXXXXXX` to monitor

---

## The Release Process (TL;DR)

**Every Thursday 7:00 PM UTC:**

```
release_orchestrator.py runs
  ↓
Merge dev → main
Run tests
  ↓
✅ PASS? → Deploy to production
❌ FAIL? → Rollback + Dispatch wee-dev to fix
```

**Manual Release Anytime:**
```
foster /release
```

**If Tests Fail:**
- Foster gets Telegram notification with task ID
- wee-dev automatically dispatched to fix bugs (1-2 hours)
- Retry manually with `/release` or wait for next Thursday

---

## Key Files in System

| File | Purpose |
|------|---------|
| `/opt/n8n-copilot-shim-dev/scripts/release_orchestrator.py` | Orchestration script |
| `/opt/.task-scheduler-dev/jobs.json` | Scheduled task (Thursday 7pm) |
| `/opt/n8n-copilot-shim-dev/run_tests.sh` | Test suite runner |
| `/opt/n8n-copilot-shim-dev/` | Dev branch (all work happens here) |
| `/opt/n8n-copilot-shim/` | Production (deploys to here) |

---

## Notifications

Foster receives Telegram notifications at key stages:

✅ **Success:** "Weekly release completed successfully. All tests passed and deployed to production."

⚠️ **Test Failure:** "Test failure detected. Dispatched wee-dev (task bg_XXXXXXXX) to fix bugs. Will retry after fixes."

❌ **Error:** "Release deployment failed. Manual intervention required."

---

## Important Guarantees

✓ **No untested code reaches production** — Test suite must pass
✓ **Production stays safe on failure** — Automatic rollback
✓ **Automatic bug fix dispatch** — wee-dev handles failures
✓ **Clear visibility** — Telegram notifications at all stages
✓ **Manual override** — `/release` command available anytime

---

## Version & Status

- **Created:** May 2, 2026
- **Status:** ACTIVE (workflow in production)
- **First Release:** Thursday, May 2, 2026 at 7:00 PM UTC
- **Scheduled Task:** Every Thursday 7:00 PM UTC
- **Automatic Bug Fix:** wee-dev dispatched on test failure

---

## Related Documents

- `/opt/n8n-copilot-shim-dev/docs/TESTING.md` — Test suite documentation
- `/opt/n8n-copilot-shim-dev/docs/background-tasks.md` — Background task API
- `/opt/n8n-copilot-shim-dev/docs/dev-access.md` — Development environment setup

---

**Questions?** See the full documentation files or check the orchestrator logs.
