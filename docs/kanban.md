# Kanban TODO Board — Repository Configuration

The Kanban board reads GitHub issues from a single `owner/repo` and renders
them as cards across `todo`, `in-progress`, `ai-active`, `pending-review`,
and `done` columns (see `kanban.py`).

## Which repository is used

Resolution order (first match wins), implemented in `kanban._default_repo()`:

1. `KANBAN_GITHUB_REPO` environment variable — explicit override, settable
   via the API/Local Settings below.
2. `TODO_GITHUB_REPO` environment variable — legacy alias, still honored.
3. The Git remote (`origin`) of the local `n8n-copilot-shim` checkout —
   automatic fallback so a fresh install works with no configuration.

## Configuring it locally (no server file editing required)

### macOS app

Local environment → **Settings → TODO Kanban**. Enter a repository in
`owner/repository` format and press **Save Repository**. Leave the field
empty and save to clear the override and fall back to the checkout's Git
remote (shown as "Active repository" once loaded).

### WebUI

Local environment → **Settings** exposes the same control, backed by the
same API.

### API

```
GET  /api/v1/settings/kanban
PUT  /api/v1/settings/kanban   {"github_repo": "owner/repo"}
```

`GET` returns:

```json
{
  "github_repo": "",            // explicit override, empty if unset
  "effective_repo": "owner/repo", // what the board actually uses
  "fallback_repo": "owner/repo",  // inferred from git origin
  "path": "/opt/n8n-copilot-shim/.env"
}
```

`PUT` persists `KANBAN_GITHUB_REPO` to the local `.env` file (with a
`.env.bak` backup) and updates the running process's environment
immediately — no restart required. Sending an empty `github_repo` clears
the override.

## Validation

`github_repo` must match `owner/repo` (letters, digits, `_`, `.`, `-` on
each side of a single `/`). An empty string is valid and clears the
override. Anything else returns:

```
HTTP 400  {"detail": "github_repo must be in owner/repo format"}
```

Both the macOS app and WebUI surface this error message inline next to the
save control.

## Notes

- Only local (non-remote/production) settings are affected — this control
  does not exist for the Remote environment, and no credentials are
  exposed by these endpoints.
- Both `KANBAN_GITHUB_REPO` and `TODO_GITHUB_REPO` are read for backward
  compatibility, but the Settings UI only ever writes `KANBAN_GITHUB_REPO`.
