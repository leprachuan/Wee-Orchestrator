# Background Tasks

Use the orchestrator API to run long-running work in the background. Background tasks appear in the ⚡ Tasks tab of the WebUI and can be monitored or cancelled by the user.

## When to use
- Any task that will take more than ~30 seconds
- Work that should not block the current conversation
- Tasks the user explicitly asks to run "in the background"

**Do NOT** use your runtime's internal background/async mechanism (e.g. `task tool with mode:"background"`) — those are invisible to the user.

## API

```
POST /api/v1/background-tasks
Headers:
  Authorization: Bearer shared_<API_SHARED_KEY>
  X-User-Identity: <identity>
  X-Auth-Channel: <channel>
Body:
  { "prompt": "...", "agent": "<agent>", "timeout": <seconds> }
```

The exact curl command with correct credentials is injected at session start — check the `[Background Tasks]` hint in your context for the ready-to-run command.

## Timeout guidance
| Task type | Timeout |
|-----------|---------|
| Quick status check | 120s |
| Standard task (summarize, analyze, write) | 600s |
| Multi-step or research task | 900s |
| Complex build / deploy / batch job | 1200–1800s |

Default if uncertain: 900s. Always set `timeout` — never omit it.

## After starting
- Returns `task_id`
- Tell the user to monitor via ⚡ Tasks tab or `/background status <task_id>`
- Do NOT run the work yourself — the API spawns a separate agent
