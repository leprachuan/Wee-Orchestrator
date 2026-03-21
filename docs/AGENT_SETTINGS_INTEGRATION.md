# Agent Settings Panel – Integration Guide

> Complete implementation of the `AgentSettingsPanel` for Wee-Orchestrator WebUI.
>
> **Status:** ✅ Fully functional in dev environment (`/opt/n8n-copilot-shim-dev`).
> The vanilla JS implementation is live in `webui/dist/`. TypeScript/React source files
> live in `webui/src/` for future typed builds.

---

## 1. Files Overview

| File | Location | Purpose |
|------|----------|---------|
| `agents.ts` | `webui/src/types/agents.ts` | TypeScript interfaces — exact mirror of agents.json schema |
| `agentConfig.ts` | `webui/src/api/agentConfig.ts` | API client: loadAgents, saveAgentConfig, validateConfig, detectPermissionsChange |
| `AgentSettingsPanel.tsx` | `webui/src/components/AgentSettingsPanel.tsx` | React component (reference implementation) |
| `AgentSettingsPanel.css` | `webui/src/styles/AgentSettingsPanel.css` | Leprachuan Glassmorphism CSS (slide-in panel variant) |

### Live implementation files (already deployed to dev)

| File | Change |
|------|--------|
| `webui/dist/index.html` | Settings modal replaced with rich form UI |
| `webui/dist/app.js` | `initSettingsAndLogs()` settings section rewritten as full form handler |
| `webui/dist/app.css` | Agent Settings Form Panel CSS appended |

---

## 2. How It Works (Vanilla JS — Already Live)

The settings panel is wired into the existing WebUI without a build step.
Click **⚙️ Settings** in the sidebar to open it.

### Features

- **Agent selector** — dropdown to switch between any agent in `agents.json`
- **Basic Info** — name, working path, description, todo_dir (editable)
- **Runtime Config** — runtime backend and model overrides
- **Permissions** — collapsible subsections for directories, tools, network, MCP
- **Tag list editors** — add/remove entries with Enter key or Add button
- **Permissions changed indicator** — ⚠ badge + "Reload Services" button appears when permissions differ from saved state
- **Save** — validates, writes to `PUT /api/v1/agents-config` (auto-creates .bak)
- **Discard** — closes without saving

### API endpoints used

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v1/agents-config` | Read full agents.json |
| PUT | `/api/v1/agents-config` | Write agents.json (creates .bak backup) |
| POST | `/api/v1/reload-services` | Request service reload (gracefully degrades if unavailable) |

---

## 3. React/TypeScript Component (Future Build Integration)

The `AgentSettingsPanel.tsx` component is a full React implementation
ready to be compiled into a future Vite build.

```tsx
// App.tsx
import React, { useState } from 'react';
import { AgentSettingsPanel } from './components/AgentSettingsPanel';

function App() {
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <div className="app">
      {/* Sidebar settings button */}
      <button onClick={() => setSettingsOpen(true)}>⚙️ Settings</button>

      {/* Panel renders as centered modal overlay */}
      {settingsOpen && (
        <AgentSettingsPanel onClose={() => setSettingsOpen(false)} />
      )}
    </div>
  );
}
```

### Bottom-left sidebar integration

```tsx
// Inside ThreadSidebar.tsx or sidebar component:
<div className="sidebar-toolbar">
  <button
    className="btn btn-ghost btn-sm sidebar-tool-btn"
    onClick={() => setSettingsOpen(true)}
    title="Agent Settings"
  >
    ⚙️ Settings
  </button>
</div>
```

---

## 4. Auth Token Access

The vanilla JS panel calls `apiRequest()` which already handles auth
through the existing WebUI session. No additional token plumbing needed.

For the React component, pass an optional `authToken` prop:

```tsx
<AgentSettingsPanel
  onClose={() => setOpen(false)}
  authToken={STATE.token}
/>
```

---

## 5. Required API Endpoints

All required endpoints are already in `agent_manager.py`:

| Method | Endpoint | Status |
|--------|----------|--------|
| GET | `/api/v1/agents-config` | ✅ Exists |
| PUT | `/api/v1/agents-config` | ✅ Exists (creates .bak) |
| POST | `/api/v1/reload-services` | ⚠ Not yet implemented (degrades gracefully) |

### Adding the reload endpoint (optional)

Add to `agent_manager.py` to enable the "Reload Services" button:

```python
@app.post("/api/v1/reload-services")
async def reload_services(request: Request):
    """Hot-reload agents.json and restart connectors if needed."""
    auth = await authenticate(
        request,
        authorization=request.headers.get("authorization"),
        x_user_identity=request.headers.get("x-user-identity"),
        x_auth_channel=request.headers.get("x-auth-channel"),
    )
    try:
        # Reload agent config in memory
        session_manager.AGENTS = session_manager._load_agents_config()
        count = len(session_manager.AGENTS)
        logger.info("Agents reloaded by %s: %d agents", auth.get("identity"), count)
        return {"status": "reloaded", "agent_count": count}
    except Exception as e:
        logger.error("Agent reload failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
```

---

## 6. Dependencies

No new npm packages required. The vanilla JS implementation uses:
- Browser Fetch API (native)
- Standard DOM APIs

The React TypeScript component uses:
- React 18 (not yet installed in webui — add if building)
- Standard CSS

To set up React build:
```bash
cd /opt/n8n-copilot-shim-dev/webui
npm install react react-dom @types/react @types/react-dom
# Then run: npm run build
```

---

## 7. Styling Customization

### Override design tokens

All colors use CSS custom properties. Override in `app.css` or global CSS:

```css
/* Example: change accent color to blue */
:root {
  --accent: #4f8cff;
  --accent-hover: #3a6fd4;
  --accent-glow: rgba(79,140,255,0.35);
}
```

### Modal width

```css
.modal-box-agent {
  width: min(1000px, 96vw); /* default: min(860px, 94vw) */
}
```

### Tag color overrides

```css
.asf-tag-allow { background: rgba(79,140,255,0.12); color: #4f8cff; }
```

---

## 8. Testing Checklist

### Functional tests

- [ ] Panel opens when ⚙️ Settings button is clicked in sidebar
- [ ] Panel closes on overlay click and ✕ button
- [ ] Panel closes on Discard button
- [ ] Agent selector dropdown loads all agents from agents.json
- [ ] Agent selector switches between agents (form re-populates)
- [ ] Name field is pre-filled and editable
- [ ] Path field is pre-filled and editable
- [ ] Description textarea is editable
- [ ] TODO directory field is editable
- [ ] Runtime selector works
- [ ] Model input is editable
- [ ] Permission mode dropdown updates mode badge
- [ ] Directory allow_read tags render, add (Enter key), remove (×)
- [ ] Directory allow_write tags work
- [ ] Directory deny tags render in red, add/remove
- [ ] Tools allow/deny tags work
- [ ] Network allow_urls/deny_urls tags work
- [ ] MCP allow/deny tags work
- [ ] Wildcard `*` tags render in gold color
- [ ] "⚠ changed" badge appears when permissions are modified
- [ ] "🔄 Reload Services" button appears when permissions changed
- [ ] Save button validates: empty name shows error
- [ ] Save button validates: path not starting with / shows error
- [ ] Save sends PUT to /api/v1/agents-config
- [ ] Success message appears after save
- [ ] Error banner appears if save fails
- [ ] .bak file created on server after save

### Visual tests (Leprachuan Glassmorphism)

- [ ] Dark modal with frosted glass background renders
- [ ] Emerald (#3ecf8e) accents on section titles, tags, inputs
- [ ] Inputs have emerald glow on focus
- [ ] Error banner shows in red
- [ ] Success banner shows in emerald
- [ ] Mode badges: elevated=gold, restricted=emerald, sandboxed=purple
- [ ] Collapsible details open/close with ▶ chevron animation
- [ ] Responsive layout: grid collapses to 1 column on mobile (<600px)
- [ ] Scrollable body with thin custom scrollbar

---

## 9. Deploy to Production

**Do not edit prod directly.** Follow the standard deploy flow:

```bash
# Test in dev first
# Then when ready to deploy:
# cp -r /opt/n8n-copilot-shim-dev/webui/dist/* /opt/n8n-copilot-shim/webui/dist/
# systemctl restart agent-manager-api
```

---

## 10. File Structure

```
webui/
├── src/                            ← TypeScript reference implementation
│   ├── api/
│   │   └── agentConfig.ts          ← API client: loadAgents, saveAgentConfig, validateConfig
│   ├── components/
│   │   └── AgentSettingsPanel.tsx  ← React component
│   ├── styles/
│   │   └── AgentSettingsPanel.css  ← Glassmorphism CSS (slide-in panel)
│   └── types/
│       └── agents.ts               ← TypeScript interfaces for agents.json
└── dist/                           ← Live webui (vanilla JS, no build step)
    ├── index.html                  ← Settings modal HTML (rich form)
    ├── app.js                      ← initSettingsAndLogs() with full form handler
    └── app.css                     ← Agent settings CSS appended at end
```
