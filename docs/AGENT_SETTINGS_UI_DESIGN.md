# Agent Settings UI – Design Document

> **Component**: `AgentSettingsPanel`
> **Location**: `webui/src/components/AgentSettingsPanel.tsx`
> **Design System**: Leprachuan Glassmorphism (dark glass, emerald accents)

---

## 1. UI Layout (ASCII Wireframe)

```
┌────────────────────────────────────────────────────────┐
│ ☘ Agent Settings                          [dirty●] [✕] │  ← Header
├────────────────────────────────────────────────────────┤
│ SELECT AGENT                                           │
│ ┌──────────────────────────────────────────────┐ [+]   │  ← Selector + Add
│ │ fosterbot                              ▾     │       │
│ └──────────────────────────────────────────────┘       │
├────────────────────────────────────────────────────────┤
│                                                        │
│ ▸ BASIC INFORMATION ─────────────────────────── open   │
│   ┌──────────────────────────────────────────┐         │
│   │ Name *         [fosterbot              ] │         │
│   │ Description    [Main orchestrator...   ] │         │
│   │ Path *         [/opt/                  ] │         │
│   │ TODO Dir       [/opt/fosterbot.../TODOs] │         │
│   └──────────────────────────────────────────┘         │
│                                                        │
│ ▸ PERMISSIONS ───────────────────────────────── open   │
│   Mode: [🔒 Restricted ▾]  ┌──────────┐               │
│                             │restricted│ ← badge       │
│                             └──────────┘               │
│   ▸ 📁 DIRECTORIES                                    │
│     Allow Read:  [/opt/fosterbot] [/opt/foster-skills] │
│     Allow Write: [/opt/fosterbot]                      │
│     Deny:        [/root/.ssh] [/etc/shadow]            │
│                                                        │
│   ▸ 🔧 TOOLS                                          │
│     Allow: [*]  (gold wildcard tag)                    │
│     Deny:  [Bash(rm -rf /)] [Bash(rm -rf /*)]         │
│                                                        │
│   ▸ 🌐 NETWORK                                        │
│     Allow URLs: [*]                                    │
│     Deny URLs:  (empty)                                │
│                                                        │
│   ▸ 🔌 MCP SERVERS                                    │
│     Allow: [*]                                         │
│     Deny:  (empty)                                     │
│                                                        │
│ ▸ ⚠️  DANGER ZONE                                      │
│   [Delete Agent "fosterbot"]                           │
│                                                        │
├────────────────────────────────────────────────────────┤
│ [💾 Save]  [🔄 Reload Services]        [Discard]      │  ← Footer
└────────────────────────────────────────────────────────┘
```

---

## 2. Component Structure

```
AgentSettingsPanel (root)
├── Overlay (backdrop click → close)
├── Panel (fixed left, 480px, glassmorphism)
│   ├── Header (title + dirty indicator + close button)
│   ├── AgentSelector (dropdown + add button)
│   ├── ValidationErrors (conditional)
│   ├── Body (scrollable)
│   │   ├── Section: "Basic Information" (default open)
│   │   │   ├── Field: name (required)
│   │   │   ├── Field: description (textarea)
│   │   │   ├── Field: path (required, monospace)
│   │   │   └── Field: todo_dir (optional, monospace)
│   │   │
│   │   ├── Section: "Permissions" (default open)
│   │   │   ├── SelectField: mode (restricted/elevated/yolo)
│   │   │   ├── ModeBadge (color-coded)
│   │   │   │
│   │   │   ├── Section: "📁 Directories" (collapsible)
│   │   │   │   ├── TagArrayField: allow_read
│   │   │   │   ├── TagArrayField: allow_write
│   │   │   │   └── TagArrayField: deny (red tags)
│   │   │   │
│   │   │   ├── Section: "🔧 Tools" (collapsible)
│   │   │   │   ├── TagArrayField: allow
│   │   │   │   └── TagArrayField: deny (red tags)
│   │   │   │
│   │   │   ├── Section: "🌐 Network" (collapsible)
│   │   │   │   ├── TagArrayField: allow_urls
│   │   │   │   └── TagArrayField: deny_urls (red tags)
│   │   │   │
│   │   │   └── Section: "🔌 MCP Servers" (collapsible)
│   │   │       ├── TagArrayField: allow
│   │   │       └── TagArrayField: deny (red tags)
│   │   │
│   │   └── Section: "⚠️ Danger Zone"
│   │       └── Delete Agent button
│   │
│   └── Footer
│       ├── Save button (primary, always present)
│       ├── Reload Services button (gold, conditional)
│       └── Discard button (ghost, right-aligned)
│
└── Toast (fixed bottom-right, auto-dismiss)
```

---

## 3. Data Flow

```
┌──────────────┐     GET /api/v1/agents-config     ┌──────────────┐
│              │ ──────────────────────────────────▶ │              │
│   WebUI      │                                    │ agent_manager│
│   (React)    │     PUT /api/v1/agents-config      │   .py        │
│              │ ──────────────────────────────────▶ │              │
│              │                                    │  ┌─────────┐ │
│              │     { status, agent_count }         │  │agents   │ │
│              │ ◀────────────────────────────────── │  │.json    │ │
└──────────────┘                                    │  └─────────┘ │
                                                    └──────────────┘

Flow:
1. Panel opens → loadAgents() → GET /api/v1/agents-config
2. User edits agent fields in React state
3. Real-time validation (validateConfig) highlights errors
4. Save → validateConfig() → PUT /api/v1/agents-config
5. Backend creates .json.bak backup, writes new agents.json
6. If permissions changed → "Reload Services" button appears
7. Reload → POST /api/v1/reload-agents (manual trigger)
```

---

## 4. Service Restart Requirements

### When is a restart needed?

`agents.json` is **cached at startup** by `agent_manager.py`:

```python
# agent_manager.py line 1037
self.AGENTS = self._load_agents_config(config_file)
```

The `PUT /api/v1/agents-config` endpoint saves to disk but **does NOT reload** the in-memory cache.

### Decision matrix:

| Change | Restart needed? | Why |
|--------|:---:|-----|
| Description text | **YES** | `self.AGENTS` is stale |
| Agent path | **YES** | Working directory cached at load |
| Permission mode change | **YES** | Parsed permissions cached |
| Directory allow/deny | **YES** | Permission checks use cache |
| Tools allow/deny | **YES** | Tool validation uses cache |
| Network/MCP rules | **YES** | Network checks use cache |
| Add/remove agent | **YES** | Agent list cached |

### UI behavior:

- **Save button**: Always visible. Saves to disk immediately.
- **Reload Services button**: Appears when *any* field has changed (since everything is cached). Uses gold/Celtic-gold color to draw attention.
- The Reload button calls `POST /api/v1/reload-agents` which triggers a hot-reload of `session_mgr.AGENTS` if that endpoint exists, or instructs the user to restart the systemd service.

### Future improvement:

Add a `reload_agents_config()` method to `SessionManager` that re-reads `agents.json` from disk and updates `self.AGENTS` in place. Wire it to the `POST /api/v1/reload-agents` endpoint. This would eliminate the need for full service restarts.

---

## 5. File Manifest

| File | Purpose |
|------|---------|
| `src/types/agents.ts` | TypeScript interfaces matching agents.json schema |
| `src/api/agentConfig.ts` | API client (load, save, validate) |
| `src/styles/AgentSettingsPanel.css` | Leprachuan Glassmorphism styling |
| `src/components/AgentSettingsPanel.tsx` | Main React component |

---

## 6. agents.json Schema Reference

```jsonc
{
  "agents": [
    {
      "name": "string (required, unique)",
      "description": "string",
      "path": "string (required, absolute path)",
      "todo_dir": "string (optional)",
      "permissions": {
        "mode": "restricted | elevated | yolo",
        "directories": {
          "allow_read": ["string[]"],
          "allow_write": ["string[]"],
          "deny": ["string[]"]
        },
        "tools": {
          "allow": ["string[] or ['*']"],
          "deny": ["string[]"]
        },
        "network": {
          "allow_urls": ["string[] or ['*']"],
          "deny_urls": ["string[]"]
        },
        "mcp": {
          "allow": ["string[] or ['*']"],
          "deny": ["string[]"]
        },
        "runtime_overrides": {
          "<runtime_name>": { "...runtime-specific config..." }
        }
      }
    }
  ]
}
```

---

## 7. Design Tokens (Leprachuan Glassmorphism)

| Token | Value | Usage |
|-------|-------|-------|
| `--accent` | `#3ecf8e` | Primary buttons, focus rings, allow tags |
| `--gold` | `#f5c542` | Reload button, wildcard tags, dirty indicator |
| `--danger` | `#ff5f6d` | Delete, deny tags, error states |
| `--glass-bg` | `rgba(255,255,255,0.06)` | Panel backgrounds |
| `--glass-blur` | `blur(24px) saturate(180%)` | Frosted glass effect |
| `--glass-border` | `rgba(255,255,255,0.12)` | Subtle borders |
| `--text-primary` | `rgba(255,255,255,0.92)` | Main text |
| `--text-secondary` | `rgba(255,255,255,0.55)` | Labels, secondary |
| `--text-muted` | `rgba(255,255,255,0.35)` | Placeholders, disabled |

---

## 8. Validation Rules

| Field | Rule |
|-------|------|
| `name` | Required, non-empty, unique across agents |
| `path` | Required, non-empty |
| `permissions.mode` | Must be `restricted`, `elevated`, or `yolo` |
| `permissions.*` array fields | Must be arrays (if present) |
| Duplicate agent names | Rejected with clear error message |
