# Release Workflow Architecture

## Visual Flowchart

```
╔════════════════════════════════════════════════════════════════════════════╗
║                    WEEKLY RELEASE ORCHESTRATION                           ║
║                    (Every Thursday 7:00 PM UTC)                           ║
╚════════════════════════════════════════════════════════════════════════════╝

                              START
                                ↓
                    ┌───────────────────────┐
                    │ Verify Git State      │
                    │ (on dev, no changes)  │
                    └───────────────────────┘
                                ↓ ✓
                    ┌───────────────────────┐
                    │ Merge dev → main      │
                    │ (git merge origin/dev)│
                    └───────────────────────┘
                                ↓ ✓
                    ┌───────────────────────┐
                    │  RUN TEST SUITE       │
                    │  bash run_tests.sh    │
                    └───────────────────────┘
                                ↓
                    ┌─────────────┬─────────────┐
                    │             │             │
                  ✅ PASS       ❌ FAIL      ERROR
                    │             │             │
                    ↓             ↓             ↓
        ┌───────────────┐ ┌────────────────┐ ERROR
        │ DEPLOY STAGE  │ │ BUG FIX STAGE  │ HANDLING
        └───────────────┘ └────────────────┘
             │                    │
             ├─ Push main         ├─ Rollback main
             │  to GitHub         │ (git reset --hard)
             │                    │
             ├─ Trigger prod      ├─ Extract failing tests
             │  /update           │ (parse test output)
             │                    │
             ├─ Restart services  ├─ Dispatch wee-dev
             │  (on lepbuntu)     │ (POST /background-tasks)
             │                    │
             └─ ✅ NOTIFY:        ├─ ⚠️ NOTIFY:
                "Release           │  "Test failure detected.
                 successful!"      │   wee-dev dispatched."
                                   │
                                   ├─ wee-dev receives:
                                   │  • Failing test names
                                   │  • Full test output
                                   │  • Fix instructions
                                   │
                                   ├─ wee-dev:
                                   │  1. Analyze failures
                                   │  2. Fix bugs on dev
                                   │  3. Run tests locally
                                   │  4. Commit to dev
                                   │  5. Push to GitHub
                                   │
                                   └─ Notify Foster when done
                                      (waiting for retry)

                            ┌──────────────────┐
                            │ MANUAL RETRY     │
                            │ /release command │
                            └──────────────────┘
                                      ↑
                                      │
                        (OR next Thursday 7pm)
                                      │
                            ┌──────────────────┐
                            │ Re-run entire    │
                            │ orchestration    │
                            │ (should pass now)│
                            └──────────────────┘
```

---

## State Machine Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                   DEV BRANCH STATE                           │
│           (Where all development happens)                    │
└──────────────────────────────────────────────────────────────┘

    DEVELOPMENT CYCLE (Monday-Thursday)
    ═════════════════════════════════════
    
    Dev State: Accumulating commits
    ├─ Issue #301 → Feature X committed
    ├─ Issue #302 → Bug fix committed
    ├─ Issue #303 → Enhancement committed
    └─ Ready for release
    
    Git state: Multiple commits ahead of main
    git log: Shows 10+ new commits on dev since last release


┌──────────────────────────────────────────────────────────────┐
│              MAIN BRANCH STATE                               │
│        (Production code, only updated on release)            │
└──────────────────────────────────────────────────────────────┘

    PRODUCTION STATE (Stable)
    ════════════════════════
    
    Main State: Last week's release code
    git log: Points to last successful merge commit
    
    Running in production: All services stable
    

┌──────────────────────────────────────────────────────────────┐
│         THURSDAY 7:00 PM RELEASE WINDOW                      │
└──────────────────────────────────────────────────────────────┘

    ┌─ ATTEMPT 1 ────────────────────────────────────┐
    │                                                 │
    │ main ← dev (MERGE COMMITS)                     │
    │ └─ main now ahead of production                │
    │                                                 │
    │ TEST SUITE RUNS                                │
    │ └─ All new code tested                         │
    │                                                 │
    │ ✅ TESTS PASS?                                 │
    │ └─ main → production                           │
    │ └─ Services restart                            │
    │ └─ ✅ Release complete                         │
    │                                                 │
    │ ❌ TESTS FAIL?                                 │
    │ └─ main ← origin/main (ROLLBACK)               │
    │ └─ main reverts to pre-merge state             │
    │ └─ wee-dev dispatched                          │
    └─────────────────────────────────────────────────┘
                      ↓
    ┌─ wee-dev FIX LOOP ─────────────────────────────┐
    │                                                 │
    │ wee-dev works on dev branch (1-2 hours)        │
    │ ├─ Analyze failing tests                       │
    │ ├─ Find bugs in code                           │
    │ ├─ Fix and commit to dev                       │
    │ ├─ Verify with local tests                     │
    │ └─ Push to origin/dev                          │
    │                                                 │
    │ Result: dev now has bug fixes                  │
    │ main still at pre-merge state (SAFE)           │
    └─────────────────────────────────────────────────┘
                      ↓
    ┌─ RETRY LOOP (Manual or Next Thursday) ────────┐
    │                                                 │
    │ foster /release                                │
    │ OR                                              │
    │ Next Thursday 7:00 PM                          │
    │                                                 │
    │ ATTEMPT 2+                                     │
    │ main ← dev (now with fixes)                    │
    │ TEST SUITE RUNS                                │
    │ ✅ Tests should pass now                       │
    │ → Deploy to production                         │
    │ → ✅ Release complete                          │
    └─────────────────────────────────────────────────┘
```

---

## Component Interaction Diagram

```
┌─────────────────────────────────────────────────────────┐
│         ORCHESTRATOR INTERACTIONS                       │
└─────────────────────────────────────────────────────────┘

DEVELOPER/wee-dev
     │
     │ (1) Commits to dev
     ↓
┌──────────────────────────────────┐
│   GitHub: Wee-Orchestrator       │
│   ├─ main (production)           │
│   └─ dev (development)           │
└──────────────────────────────────┘
     ↑                  ↓
     │ (5) Pull latest  │ (2) Clone/fetch
     │                  │
     │              ┌────────────────────────┐
     │              │ Dev Host (192.168.1.100)
     │              │ /opt/n8n-copilot-shim-dev/
     │              │                        │
     │              │ (3) Release            │
     │              │ orchestrator.py        │
     │              │ runs                   │
     │              │                        │
     │              │ MERGE → TEST → DEPLOY │
     │              │        or              │
     │              │ ROLLBACK → DISPATCH    │
     │              └────────────────────────┘
     │                  ↓
     ↓                  │
     │                  ├─ (4a) If PASS:
     │                  │  └─ Push main
     │                  │  └─ Trigger prod
     │                  │     /update
     │                  │
     │                  ├─ (4b) If FAIL:
     │                  │  └─ Rollback main
     │                  │  └─ POST to orchestrator
     │                  │     API
     │                  ↓
     │              ┌────────────────────────┐
     │              │ Prod Host (lepbuntu)   │
     │              │ 100.124.186.75         │
     │              │ /opt/n8n-copilot-shim/ │
     │              │                        │
     │              │ (5a) If DEPLOY:        │
     │              │ git pull origin main   │
     │              │ systemctl restart *    │
     │              │                        │
     │              │ (5b) If DISPATCH:      │
     │              │ API receives task      │
     │              │ Queues to wee-dev      │
     │              └────────────────────────┘
     │                  ↓
     │              ┌────────────────────────┐
     │              │ wee-dev Agent (Local)  │
     │              │ Receives bug fix task  │
     │              │                        │
     │              │ 1. SSH to dev host     │
     │              │ 2. Analyze failures    │
     │              │ 3. Fix bugs            │
     │              │ 4. Test locally        │
     │              │ 5. Commit to dev       │
     │              │ 6. Push origin/dev     │
     │              └────────────────────────┘
     │                  ↓
     └──────────────────┘
        Fixes merged into dev,
        ready for retry
```

---

## Deployment Timeline Example

```
MONDAY 9:00 AM
├─ Issue #401: Feature X started
├─ wee-dev works on dev branch
└─ Commit: "feat: issue #401 - Feature X"

TUESDAY 2:00 PM
├─ Issue #402: Bug fix Y
├─ wee-dev commits to dev
└─ Commit: "fix: issue #402 - Bug Y"

WEDNESDAY 11:00 AM
├─ Issue #403: Enhancement Z
├─ wee-dev commits to dev
└─ Commit: "feat: issue #403 - Enhancement Z"

THURSDAY 6:50 PM
├─ release_orchestrator.py scheduled to run
└─ dev has 3 new commits ready

THURSDAY 7:00 PM ⏰ RELEASE TIME
├─ release_orchestrator.py starts
│
├─ Stage 1: MERGE
│  ├─ git checkout main
│  ├─ git merge origin/dev
│  └─ ✓ main has all 3 commits
│
├─ Stage 2: TEST
│  ├─ bash run_tests.sh
│  ├─ Test suite: 247 tests
│  │
│  ├─ Path A (PASS): 247/247 passed ✅
│  │  ├─ Stage 3: DEPLOY
│  │  ├─ git push origin main
│  │  ├─ Trigger prod /update
│  │  ├─ Services restart
│  │  └─ ✅ Notify: "Release successful"
│  │     All 3 issues now live in production
│  │
│  └─ Path B (FAIL): 247/247 → 242/247 passed ❌
│     ├─ 5 tests failed
│     ├─ Stage 3: BUG FIX
│     ├─ Extract failures
│     ├─ Dispatch wee-dev
│     ├─ ⚠️ Notify: "Test failure, wee-dev dispatched"
│     └─ Waiting for wee-dev...

THURSDAY 7:30 PM
├─ wee-dev receives task
├─ Analyzes failing tests
├─ Finds bug in issue #403 code
└─ Starts fixing on dev branch

THURSDAY 8:00 PM
├─ wee-dev local tests pass
├─ Commits fix: "fix: resolve test failures"
├─ Pushes origin/dev
└─ Notifies Foster: "Fixes complete, ready for retry"

THURSDAY 8:15 PM (Foster's choice)
├─ foster: /release
├─ OR waits for next Thursday 7pm
│
├─ Retry orchestration runs
├─ main ← dev (with fixes)
├─ bash run_tests.sh
├─ All 247 tests PASS ✅
├─ Deploy to production
└─ ✅ Notify: "Release successful (retry)"
   All 3 issues now live in production

FRIDAY-WEDNESDAY
├─ Development continues
├─ More issues filed & worked on
└─ Cycle repeats next Thursday
```

---

## Error Handling Matrix

| Scenario | Detection | Action | Recovery |
|----------|-----------|--------|----------|
| Merge conflict | `git merge` fails | Exit with error | Foster manually resolves on dev |
| Test timeout | `run_tests.sh` timeout | Treat as failure | Dispatch wee-dev to optimize tests |
| Test failure | Exit code non-zero | Extract failures | Dispatch wee-dev |
| Rollback fails | `git reset` fails | Log error | Manual rollback needed |
| Dispatch fails | API call error | Log error | Foster files GitHub issue manually |
| Deploy fails | `curl` to /update fails | Log error | Manual deploy or fix services |
| Network down | Connection error | Retry 3x | Escalate to ops |

---

## Security & Safety Guarantees

```
✓ Main branch ONLY updated on successful test pass
✓ On failure: Automatic rollback (main stays safe)
✓ Production deployment always after verified tests
✓ No untested code reaches production
✓ Automatic retry mechanism for bugs
✓ Clear audit trail (all commits logged)
✓ Manual override available (/release)
✓ Notifications on all status changes
```

---

## Performance Characteristics

```
Typical Release Cycle Timing:

Stage 1: Merge dev→main
  └─ ~10 seconds (small diff, fast merge)

Stage 2: Test Suite
  ├─ Setup: ~30 seconds
  ├─ Run: ~3-5 minutes (247 tests)
  └─ Total: ~4 minutes

Stage 3a: Deploy (if pass)
  ├─ Push to GitHub: ~20 seconds
  ├─ Trigger prod: ~5 seconds
  ├─ Prod pulls: ~15 seconds
  ├─ Services restart: ~30 seconds
  └─ Total: ~70 seconds

Stage 3b: Bug Fix (if fail)
  ├─ Rollback: ~10 seconds
  ├─ Dispatch: ~5 seconds
  └─ Waiting for wee-dev: 1-2 hours (typical)

Total Time to Production (Success):
  ~5.5 minutes

Total Time Before Retry (Failure):
  ~4.5 minutes to failure detection
  + 1-2 hours for wee-dev
  + 5.5 minutes for retry
  = ~2 hours total
```

