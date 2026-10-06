# Repository-backed Always-On goals

Always-On goals can be GitHub issues in configured work repositories. GitHub owns the title, description and markdown task checklist; Wee owns the recurring schedule, run lease, checkpoints, reports, approvals and budgets. This extends the existing report-only Always-On worker; it does not grant shell or arbitrary tool access.

## Setup

Open an agent's Always-On panel in WebUI, macOS or iOS. Enter enabled work repositories as `owner/repository`, one per line, and choose that agent's default repository. Repositories are shared across the API account; defaults belong to individual agents. Removing a repository immediately pauses its linked goals. Work repositories are separate from skill repositories.

The API service reads `WEE_GITHUB_TOKEN`, then `GH_TOKEN` or `GITHUB_TOKEN`, or reuses its service account's existing `gh auth login` for github.com. Use a token with issue read/write access to the selected repositories. Credentials remain outside repository files and are never included in approval previews. GitHub Enterprise is not supported in this version.

## Goal lifecycle

- An open issue must have `always-on` and exactly one `agent:<configured-agent-name>` label. A normal queue label such as `wee-dev`, `queued`, or `in-progress` prevents Always-On dispatch. This keeps issue-queue work and recurring work separate. Remove those queue labels before linking a goal.
- Use **Request create / link issue** to create an issue or label/link an existing open issue. External writes require the shared action approval or an existing permitted exact scope. The request itself opts into the Always-On action gate, but the resulting goal starts paused.
- Choose recurring or finite and a cadence of 5 minutes through 7 days. GitHub discovery defaults to recurring, hourly. Resume a goal explicitly after reviewing it.
- Every run revalidates the linked issue. Edits to title/body/labels pause the goal, discard stale pending plans and increment its run version. Closed/unflagged issues, invalid assignments, disabled repositories and failed verification stop new runs. Reopening or restoring flags never resumes automatically.
- A successful recurring report leaves its issue open. **Request completion (closes issue)** is available only for finite linked goals and requires the existing shared approval gate. Reports stay in Wee's isolated goal workspace and are visible across clients; no automatic GitHub comment is added per poll/run.
- Revise linked intent in GitHub, then sync and explicitly resume. Local free-text revision is available only for legacy unlinked goals.
- Legacy goals remain visible. **Link to GitHub issue** preserves the goal ID, stored report and action history while pausing and invalidating the pending plan. No automatic bulk migration occurs.
- Failed/partial GitHub snapshots keep cached records and show sync errors. An uncertain external write is not retried automatically; inspect GitHub and the shared approval history before submitting a new operation. Keep the same request UUID when retrying an HTTP request whose response was lost.

The agent panel includes issue links, checklist content, scheduling/status and operation progress, plus a read-only view across all agents. The central API listing omits the agent query to return all goals.

## API

All routes require existing API authentication and use the `/api/v1/autonomy` prefix.

| Method | Path | Purpose |
| --- | --- | --- |
| GET / PUT | `/repositories?agent=wee-dev` | Shared allowed repositories and this agent's default |
| POST | `/repositories/sync` | Fetch current GitHub goal state |
| GET | `/repository-operations?agent=wee-dev` | Durable issue-write requests and their outcomes |
| POST | `/repository-operations?agent=wee-dev` | Request create, link/migrate or finite completion |
| GET | `/responsibilities` | All goals, including optional `source` and `tracking` fields |

Settings input:

```json
{"repositories":[{"repository":"owner/work","enabled":true}],"default_repository":"owner/work"}
```

Create input (generate a unique UUID client-side for each intended operation):

```json
{"agent":"wee-dev","repository":"owner/work","kind":"create","request_id":"00000000-0000-4000-8000-000000000001","title":"Maintain service health","body":"- [ ] Check health\n- [ ] Record findings","mode":"recurring","interval_seconds":3600}
```

For linking use `kind: "link"` and `number`; add `responsibility` to migrate an existing unlinked goal. For completion use `kind: "complete"` and `responsibility`; only finite linked goals qualify. A pending operation is processed by the exclusive coordinator after its shared approval is resolved. Labels never override action permissions or model budgets.

## Validation

Run the issue-538 tests and existing Always-On tests on dev with `PYTHONPATH=.:tests python3 -m pytest tests/test_issue_538_repository_goals.py tests/test_issue_*autonomy*.py tests/test_issue_536_delete_goals.py`. Native builds must target the dev API for behavior checks. Production promotion uses the versioned release workflow after QA.
