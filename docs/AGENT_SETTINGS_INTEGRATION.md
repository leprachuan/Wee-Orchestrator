# Agent Settings Panel – Integration Guide

> How to integrate the `AgentSettingsPanel` component into the Wee-Orchestrator WebUI.

---

## 1. Files Overview

| File | Location | Purpose |
|------|----------|---------|
| `agents.ts` | `src/types/agents.ts` | TypeScript interfaces for agent config |
| `agentConfig.ts` | `src/api/agentConfig.ts` | API client (load, save, validate) |
| `AgentSettingsPanel.css` | `src/styles/AgentSettingsPanel.css` | Glassmorphism styling |
| `AgentSettingsPanel.tsx` | `src/components/AgentSettingsPanel.tsx` | Main panel component |

---

## 2. Integration into App.tsx

Add a settings button to the sidebar/header and wire up the panel:

```tsx
// App.tsx
import React, { useState } from 'react';
import { AgentSettingsPanel } from './components/AgentSettingsPanel';

function App() {
  const [settingsOpen, setSettingsOpen] = useState(false);

  // ... existing state and hooks ...

  return (
    <div className="flex h-screen bg-gray-100">
      {/* Existing sidebar */}
      <ThreadSidebar ... />

      {/* Main content */}
      <div className="flex-1 flex flex-col">
        <header className="bg-white border-b border-gray-200 px-6 py-4">
          {/* Add settings button */}
          <button
            onClick={() => setSettingsOpen(true)}
            className="text-gray-500 hover:text-gray-700"
            title="Agent Settings"
          >
            ⚙️
          </button>
        </header>

        <main className="flex-1 bg-white">
          <ChatView ... />
        </main>
      </div>

      {/* Agent Settings Panel */}
      <AgentSettingsPanel
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
}
```

### Bottom-left sidebar integration (alternative):

```tsx
// Inside ThreadSidebar.tsx, add at the bottom:
<div className="border-t border-gray-200 p-3">
  <button
    onClick={onOpenSettings}
    className="flex items-center gap-2 w-full px-3 py-2 rounded-lg
               text-sm text-gray-600 hover:bg-gray-100 transition-colors"
  >
    ⚙️ Agent Settings
  </button>
</div>
```

---

## 3. Integration into dist/app.js (Production WebUI)

For the compiled production WebUI, add this to the existing `app.js`:

### 3a. Add the settings button

Find the sidebar render code and add a settings trigger:

```javascript
// In the sidebar section, add a settings button at the bottom
const settingsBtn = document.createElement('button');
settingsBtn.innerHTML = '⚙️ Agent Settings';
settingsBtn.className = 'sidebar-settings-btn';
settingsBtn.onclick = () => openAgentSettings();
sidebar.appendChild(settingsBtn);
```

### 3b. Load the CSS

Add a `<link>` tag in `index.html`:

```html
<link rel="stylesheet" href="AgentSettingsPanel.css">
```

Or inject dynamically in `app.js`:

```javascript
const link = document.createElement('link');
link.rel = 'stylesheet';
link.href = 'AgentSettingsPanel.css';
document.head.appendChild(link);
```

---

## 4. Auth Token Access

The panel reads the auth token from two sources (in priority order):

1. `window.__WEE_STATE__.token` – shared state from the main app
2. `sessionStorage.getItem('wee_token')` – fallback

Ensure the main app stores the token in at least one of these locations. The production `app.js` already uses `STATE.token`, so you can expose it:

```javascript
// In app.js, after auth:
window.__WEE_STATE__ = STATE;
```

---

## 5. Required API Endpoints

The panel uses these endpoints (already in `agent_manager.py`):

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v1/agents-config` | Read full agents.json |
| PUT | `/api/v1/agents-config` | Write agents.json (creates .bak) |
| POST | `/api/v1/reload-agents` | Hot-reload agent config (**NEW**) |

### Adding the reload endpoint

Add to `agent_manager.py`:

```python
@app.post("/api/v1/reload-agents")
async def reload_agents_config(request: Request):
    """Hot-reload agents.json without full service restart."""
    auth = await _check_auth(request)
    if not auth.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        session_mgr.AGENTS = session_mgr._load_agents_config()
        agent_count = len(session_mgr.AGENTS)
        logger.info("Agents reloaded by %s: %d agents", auth.get("identity"), agent_count)
        return {"status": "reloaded", "agent_count": agent_count}
    except Exception as e:
        logger.error("Agent reload failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
```

---

## 6. Dependencies

No new npm packages required. The component uses only:
- React 18 (already installed)
- Standard CSS (no CSS-in-JS library)
- Fetch API (browser native)

---

## 7. Styling Customization

### Override design tokens

All colors and dimensions use CSS custom properties. Override in your global CSS:

```css
/* Example: change accent color to blue */
:root {
  --accent: #4f8cff;
  --accent-hover: #3a6fd4;
  --accent-glow: rgba(79,140,255,0.35);
}
```

### Panel width

```css
.asp-panel {
  width: 560px; /* default: 480px */
}
```

### Panel position (right side instead of left)

```css
.asp-panel {
  left: auto;
  right: 0;
  border-right: none;
  border-left: 1px solid var(--glass-border);
  box-shadow: -24px 0 64px rgba(0,0,0,0.6);
  animation: asp-slideInRight 0.25s ease;
}
@keyframes asp-slideInRight {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}
```

---

## 8. Testing Checklist

### Functional tests

- [ ] Panel opens when settings button is clicked
- [ ] Panel closes on overlay click and ✕ button
- [ ] Agent list loads from API on open
- [ ] Agent selector switches between agents
- [ ] Basic fields (name, description, path, todo_dir) are editable
- [ ] Permission mode dropdown works (restricted/elevated/yolo)
- [ ] Tag array fields: add via Enter key
- [ ] Tag array fields: add via comma key
- [ ] Tag array fields: remove via × button
- [ ] Tag array fields: backspace removes last tag when input empty
- [ ] Wildcard `*` tags display in gold
- [ ] Deny tags display in red
- [ ] Dirty indicator (yellow dot) appears when changes are made
- [ ] Save button is disabled when no changes
- [ ] Save validates config before sending
- [ ] Validation errors display above form
- [ ] Save sends PUT to /api/v1/agents-config
- [ ] Success toast appears after save
- [ ] Error toast appears on save failure
- [ ] Discard button reverts to last saved state
- [ ] "Reload Services" button appears when permissions change
- [ ] Add agent (+) creates new agent with defaults
- [ ] Delete agent works with confirmation dialog
- [ ] Cannot delete the last remaining agent

### Visual tests

- [ ] Glassmorphism backdrop blur works
- [ ] Dark panel with emerald accents renders correctly
- [ ] All form elements have proper focus states (emerald glow)
- [ ] Error states show red border/glow
- [ ] Responsive: panel fills viewport on mobile (<520px)
- [ ] Scrollable body with custom scrollbar
- [ ] Animations: slide-in, fade-in, toast slide-up

### Auth tests

- [ ] Unauthenticated request shows "Unauthorized" error
- [ ] Token is read from window.__WEE_STATE__.token
- [ ] Fallback to sessionStorage works

---

## 9. Build & Deploy

```bash
# From the WebUI source directory
cd /opt/MyHomeDevops/n8n-webui

# Install deps (if not already)
npm install

# Dev server
npm run dev

# Production build
npm run build

# Copy dist to dev environment
cp -r dist/* /opt/n8n-copilot-shim-dev/webui/dist/

# Copy dist to prod (when deploying)
cp -r dist/* /opt/n8n-copilot-shim/webui/dist/
```

---

## 10. File Structure After Integration

```
n8n-webui/src/
├── api/
│   └── agentConfig.ts          ← NEW: Agent config API client
├── components/
│   ├── AgentSettingsPanel.tsx   ← NEW: Settings panel component
│   ├── ChatInput.tsx
│   ├── ChatView.tsx
│   ├── MessageBubble.tsx
│   └── ThreadSidebar.tsx
├── hooks/
│   ├── useChat.ts
│   └── useThreads.ts
├── services/
│   └── api.ts
├── styles/
│   └── AgentSettingsPanel.css   ← NEW: Glassmorphism styles
├── types/
│   ├── agents.ts                ← NEW: Agent config types
│   └── api.ts
├── App.tsx                      ← MODIFY: Add settings panel
├── index.css
└── main.tsx
```
