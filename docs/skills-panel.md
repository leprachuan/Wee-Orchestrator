# Skills Panel — Developer Documentation

## Overview

The Skills Panel is a slide-over UI panel in the Wee Orchestrator WebUI that lists
all installed skills, tracks their origin metadata, checks for updates, and can
trigger background update tasks.

It mirrors the Wee Canvas pushover pattern (🎨 tab on right edge) with a 🧩 tab
positioned slightly lower.

## Architecture

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  Frontend    │────▶│ API Routes      │────▶│ skill_manager.py │
│  (app.js)   │     │ /api/v1/skills  │     │ (scan, update)   │
│  pushover   │     │ agent_manager   │     │                  │
└─────────────┘     └─────────────────┘     └──────────────────┘
                                                    │
                                            ┌───────▼──────────┐
                                            │ skill_origins.json│
                                            │ (origin metadata) │
                                            └──────────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `skill_manager.py` | Backend: scanning, origin CRUD, update checking, update applying |
| `skill_origins.json` | Persistent store of origin metadata for all skills |
| `webui/dist/index.html` | Skills panel HTML (aside element) |
| `webui/dist/app.css` | Skills panel CSS (pushover styling) |
| `webui/dist/app.js` | Skills panel JS (list, detail, origin form, update actions) |
| `agent_manager.py` | API routes at `/api/v1/skills/*` |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/skills` | List all installed skills with metadata and origin info |
| `GET` | `/api/v1/skills/{skill_key}` | Get detail for a single skill |
| `PUT` | `/api/v1/skills/{skill_key}/origin` | Set or update origin metadata |
| `DELETE` | `/api/v1/skills/{skill_key}/origin` | Remove origin metadata |
| `POST` | `/api/v1/skills/{skill_key}/check-update` | Check if updates are available |
| `POST` | `/api/v1/skills/{skill_key}/update` | Dispatch a background task to apply updates |

### Skill Key Format

Skills are identified by a `skill_key` which is typically `{source_label}/{dir_name}`:
- `foster-skills/todo-management`
- `skills/telegram-notify`
- `skills/algorithmic-art` (Anthropic official, nested under anthropic-skills/skills/)

### Origin Metadata Schema

```json
{
  "origin_type": "git_repo | website | local | unknown",
  "origin_url": "https://github.com/org/repo.git",
  "origin_path": "skills/my-skill",
  "notes": "Any notes about local modifications",
  "recorded_at": 1711584000.0,
  "updated_at": 1711584000.0,
  "last_checked": 1711584000.0,
  "last_updated": 1711584000.0,
  "update_available": false,
  "remote_checksum": "abc123...",
  "diff_summary": ["M README.md", "+ new-file.py"]
}
```

**origin_type values:**
- `git_repo` — Cloned from a git repository. Full update support (check + apply).
- `website` — Copied from a website like skills.sh. Manual update only.
- `local` — Authored locally. No upstream to check.
- `unknown` — Origin not recorded yet.

## How It Works

### Skill Discovery

`skill_manager.scan_skills()` scans these directories:
1. `/opt/foster-skills/` — Private experimental skills
2. `/opt/skills/` — Public production-ready skills
3. `/opt/.claude/skills/` — Claude runtime skills
4. `/opt/.github/skills/` — GitHub-loaded skills (includes Anthropic)
5. `/opt/pot-o-skills/` — Additional skills collection

Each directory is scanned for subdirectories containing `skill_metadata.json`,
`SKILL.md`, or runtime subdirectories (`claude/`, `copilot/`, `gemini/`).

Nested skill repos (e.g., `anthropic-skills/skills/*`) are handled automatically.

### Origin Tracking

Origin metadata is stored centrally in `skill_origins.json` (not per-skill files)
so it persists independently of skill directory contents. This file is automatically
created and managed by the API.

### Update Checking (git_repo origins)

1. Shallow-clones the origin repo to a temp directory
2. Navigates to the `origin_path` within the cloned repo
3. Computes directory checksum of both local and remote
4. If checksums differ, returns a file-level diff summary
5. Updates `last_checked` and `update_available` in origin metadata

### Update Applying (git_repo origins)

1. Clones origin repo (shallow, temp dir)
2. Backs up the local skill directory
3. Copies changed files from remote over local (merge, not replace)
4. Preserves local-only files (won't delete them)
5. Updates origin metadata with `last_updated` timestamp
6. Runs as a background task visible in ⚡ Tasks tab

### Website Origins

Website-sourced skills (origin_type: "website") cannot be auto-updated.
The UI shows the origin URL and directs users to visit it manually.

## How to Record Origin for a Skill

### Via the UI

1. Click the 🧩 tab on the right edge to open the Skills panel
2. Click any skill card to view its detail
3. If no origin is recorded, click "📝 Record Origin"
4. Fill in origin type, URL, path, and notes
5. Click "💾 Save Origin"

### Via the API

```bash
curl -sk -X PUT https://HOST:8001/api/v1/skills/SKILL_KEY/origin \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "origin_type": "git_repo",
    "origin_url": "https://github.com/anthropics/skills.git",
    "origin_path": "skills/my-skill",
    "notes": "Installed from Anthropic official on 2026-03-27"
  }'
```

### Via skill_manager.py directly

```python
from skill_manager import set_origin
set_origin("skills/my-skill", {
    "origin_type": "git_repo",
    "origin_url": "https://github.com/anthropics/skills.git",
    "origin_path": "skills/my-skill",
})
```

## Testing

### API Tests

```bash
# List all skills
curl -sk https://192.168.1.100:8001/api/v1/skills \
  -H "Authorization: Bearer shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"

# Set origin for a skill
curl -sk -X PUT https://192.168.1.100:8001/api/v1/skills/foster-skills%2Fmac-control/origin \
  -H "Authorization: Bearer shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU" \
  -H "Content-Type: application/json" \
  -d '{"origin_type": "local", "notes": "Custom skill"}'

# Check for updates
curl -sk -X POST https://192.168.1.100:8001/api/v1/skills/skills%2Falgorithmic-art/check-update \
  -H "Authorization: Bearer shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"

# Trigger update
curl -sk -X POST https://192.168.1.100:8001/api/v1/skills/skills%2Falgorithmic-art/update \
  -H "Authorization: Bearer shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"
```

### UI Test

1. Navigate to `https://192.168.1.100:8001/ui/`
2. Look for the 🧩 tab on the right edge (below the 🎨 Canvas tab)
3. Click to open the Skills panel
4. Verify skill cards appear with origin badges
5. Click a skill to see detail view
6. Test search and filter controls
7. Test "Record Origin" for a skill without origin
8. Test "Check for Updates" on an Anthropic skill
