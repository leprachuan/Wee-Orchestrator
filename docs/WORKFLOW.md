# Wee Orchestrator Development Workflow

## Overview

Two agents, two stages:
1. **wee-dev** — Issue → dev branch (ongoing)
2. **wee-qa** — dev → main (scheduled gate)

No per-issue QA. Simple. Fast.

## Stage 1: wee-dev Issue Development

**Trigger:** GitHub issue with `wee-dev` label (queued)

**Process:**
1. Dispatcher picks up queued issue
2. wee-dev SSH to 192.168.1.100
3. Work on `/opt/n8n-copilot-shim-dev/`
4. Commit directly to `dev` branch (no feature branches)
5. Test locally
6. Push to `origin dev`
7. Remove `wee-dev` label from GitHub issue
8. Close the issue

**Result:** Issue ✓ closed, changes on dev branch

**Parallel Work:** Multiple wee-dev tasks can run at once (one per queued issue, up to max_concurrent limit)

## Stage 2: wee-qa Release Gate (Scheduled)

**Trigger:** Scheduled task (e.g., daily at 2am UTC)

**Process:**
1. wee-qa reviews dev branch since last main release
2. Runs full test suite
3. Checks code quality (flake8, black, mypy)
4. Reviews for bugs

**Decision:**
- ✅ **APPROVED** → Merge dev→main, post approval comment
- ❌ **BLOCKED** → Post blockers, wait for fixes on dev

**Result:** dev branch gated, main only advances when QA approves

## Labels

Only two labels needed:
- `wee-dev` — Issue ready for development
- `wee-dev:in-progress` — wee-dev is working on it

That's it. No QA labels. No PR workflow.

## Workflow Benefits

- **Fast:** wee-dev commits directly, no PRs to review
- **Clear:** dev branch = working code, main = production
- **Simple:** Dispatcher is 100 lines, easy to maintain
- **Scalable:** Multiple wee-dev tasks in parallel

## Example

```
User creates issue #123: "Add auth logging"
  ↓
Dispatcher picks it up → wee-dev
  ↓
wee-dev: implement, test, commit to dev, close issue #123
  ↓
(2am daily) Scheduled wee-qa gate runs
  ↓
wee-qa reviews dev, approves → merges to main
  ↓
Production updated
```

## Setup Scheduled wee-qa Gate

```bash
/schedule add 'wee-qa dev→main gate' | 'daily at 2am UTC' | 'python3 /opt/n8n-copilot-shim/scripts/wee_qa_scheduled_gate.py'
```

Or manually run:
```bash
python3 /opt/n8n-copilot-shim/scripts/wee_qa_scheduled_gate.py
```
