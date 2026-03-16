# Wee Orchestrator — Code Structure & Patterns Summary

## 1. HTML STRUCTURE (webui/dist/index.html)

### Overall Layout
- **Root**: `<div id="app" class="app-layout hidden">` (hidden until auth succeeds)
- **Auth Overlay**: `<div id="auth-overlay">` (shown when not authenticated)
- **Sidebar**: `<aside id="sidebar" class="sidebar glass-panel">` (left side, collapsible)
- **Main Views**: Three main sections that toggle visibility

### Sidebar Structure (lines 67-88)
```html
<aside id="sidebar" class="sidebar glass-panel">
  <div class="sidebar-header">
    <span class="sidebar-logo"><img src="/static/icon-192.png" ... /></span>
    <span class="sidebar-title">Wee-Orchestrator</span>
    <span id="dev-badge" class="dev-badge" style="display: none;">🔧 DEV</span>
    <button id="btn-sidebar-toggle" class="btn-icon sidebar-toggle-btn">‹</button>
  </div>

  <!-- Nav buttons with badges -->
  <div class="sidebar-nav">
    <button id="btn-nav-chat" class="sidebar-nav-btn active">💬 Chat</button>
    <button id="btn-nav-background" class="sidebar-nav-btn">
      ⚡ Tasks <span id="bg-task-badge" class="nav-badge hidden">0</span>
    </button>
    <button id="btn-nav-scheduler" class="sidebar-nav-btn">📅 Scheduler</button>
  </div>

  <button id="btn-new-chat" class="btn btn-primary new-chat-btn">+ New Chat</button>
  <div id="sessions-list" class="sessions-list"></div>
  
  <div class="sidebar-footer">
    <span id="sidebar-identity" class="sidebar-user"></span>
    <button id="btn-logout" class="btn btn-ghost btn-sm">Logout</button>
  </div>
</aside>
```

**Key Badge Patterns**:
- `.nav-badge` class with `hidden` class to hide/show
- Siblings to nav button text (not wrapped)
- Badge content is text count or status
- Toggle visibility with `.hidden` class

### Main Panels — Visibility Toggle Patterns

#### Chat Panel (lines 91-157)
```html
<main id="chat-panel" class="main-panel">
  <!-- Visible by default, toggled via show/hide -->
</main>
```
- Toggled with `show()` / `hide()` (adds/removes `.hidden` class)
- Function: `showChatPanel()` at line 1977

#### Background Tasks Panel (lines 282-308)
```html
<section id="background-panel" class="background-panel hidden">
  <header class="bg-header glass-panel">
    <div class="bg-header-left">
      <button id="btn-bg-open-sidebar" class="btn-icon sidebar-open-btn hidden">☰</button>
      <h2 class="bg-title">Background Tasks</h2>
    </div>
    <div class="bg-header-actions">
      <button id="btn-bg-refresh" class="btn btn-ghost btn-sm">↺ Refresh</button>
    </div>
  </header>
  <div class="bg-body">
    <div id="bg-tasks-list" class="bg-tasks-list"></div>
    <aside id="bg-detail" class="bg-detail glass-panel hidden"></aside>
  </div>
</section>
```
- Toggled with `.hidden` class
- Contains nested detail panel
- Function: `showBackgroundPanel()` at line 2009

#### Scheduler Panel (lines 311-339)
```html
<section id="scheduler-panel" class="scheduler-panel hidden">
  <header class="sched-header glass-panel">
    <div class="sched-header-left">
      <button id="btn-sched-open-sidebar" class="btn-icon sidebar-open-btn hidden">☰</button>
      <h2 class="sched-title">Scheduled Tasks</h2>
      <span id="sched-daemon-badge" class="sched-daemon-badge">status</span>
    </div>
    <div class="sched-header-actions">
      <button id="btn-sched-refresh" class="btn btn-ghost btn-sm">↺ Refresh</button>
      <button id="btn-sched-new" class="btn btn-primary btn-sm">+ New Job</button>
    </div>
  </header>
  <div class="sched-body">
    <div id="sched-jobs-list" class="sched-jobs-list"></div>
    <aside id="sched-detail" class="sched-detail glass-panel hidden"></aside>
  </div>
</section>
```
- Similar toggle pattern to background panel
- Function: `showSchedulerPanel()` at line 1992

#### Notification Panel (lines 160-185) — RIGHT SIDE SLIDE-IN
```html
<aside id="notification-panel" class="notification-panel notif-hidden">
  <div class="notif-header">
    <div class="notif-header-left">
      <h2 class="notif-title">🔔 Notifications</h2>
    </div>
    <div class="notif-header-actions">
      <button id="btn-notif-mark-all-read" class="btn btn-ghost btn-sm">✓ All Read</button>
      <button id="btn-notif-clear-read" class="btn btn-ghost btn-sm">🗑 Clear Read</button>
      <button id="btn-notif-settings" class="btn btn-ghost btn-sm">⚙</button>
    </div>
  </div>
  <div id="notif-settings-bar" class="notif-settings-bar hidden glass-panel">
    <label class="notif-toggle-label">
      <input type="checkbox" id="notif-enabled-toggle" checked />
      <span>Enable browser alerts for tasks</span>
    </label>
  </div>
  <div class="notif-body">
    <div id="notif-list" class="notif-list"></div>
  </div>
</aside>
```
- **Different toggle pattern**: Uses `.notif-hidden` class (not `.hidden`)
- Positioned absolutely on right side
- Slides in from right
- Function: `toggleNotificationPanel()` at line 2965
- Toggle button: `id="btn-nav-notifications"` with class `.meta-pill` in header

#### Request Queue Panel (lines 229-279) — RIGHT SIDE MINIMIZED
```html
<aside id="request-queue-panel" class="request-queue-panel queue-minimized">
  <div id="todo-section" class="todo-section">
    <div class="todo-section-header">
      <span class="todo-section-icon">✅</span>
      <span class="todo-section-label">Upcoming TODOs</span>
      <span id="todo-count" class="todo-count">0</span>
      <button id="btn-toggle-queue" class="queue-toggle-btn">›</button>
    </div>
    <div id="todo-items-list" class="todo-items-list"></div>
  </div>

  <div id="queue-section" class="queue-section queue-section-collapsed">
    <div class="queue-header">
      <div class="queue-header-title">
        <button id="btn-toggle-queue-section" class="queue-section-toggle">▼</button>
        <span class="queue-icon">📋</span>
        <span class="queue-label">Queued</span>
        <span id="queue-count" class="queue-count">0</span>
      </div>
    </div>
    <div id="queue-items-list" class="queue-items-list"></div>
    <div class="queue-footer"><small id="queue-status-msg"></small></div>
  </div>

  <div id="scratch-section" class="scratch-section">
    <div class="scratch-header">
      <span class="scratch-icon">📝</span>
      <span class="scratch-label">Scratch Notes</span>
    </div>
    <textarea id="scratch-textarea" class="scratch-textarea"></textarea>
  </div>
</aside>
```
- Toggle with `.queue-minimized` class (not `.hidden`)
- Can also have `.queue-section-collapsed` on inner section
- Function: `toggleQueuePanel()` at line 965

### Sidebar Toggle Button
- Toggle button: `id="btn-sidebar-toggle"` (close icon "‹")
- Open button: `id="btn-open-sidebar"` (hamburger "☰")
- Pattern: `.collapsed` class added to sidebar when closed
- Function: `toggleSidebar(open)` at line 1683

---

## 2. CSS STRUCTURE (webui/dist/app.css)

### CSS Variables (Root) — lines 12-28
```css
:root {
  --accent:         #3ecf8e;      /* Green accent */
  --accent-hover:   #34b87d;
  --accent-glow:    rgba(62,207,142,0.32);
  --gold:           #f5c542;      /* Orange/gold for secondary */
  --gold-hover:     #e0b23a;
  --gold-glow:      rgba(245,197,66,0.34);
  --danger:         #ff5f6d;      /* Red for errors */
  --text-primary:   rgba(255,255,255,0.92);
  --text-secondary: rgba(255,255,255,0.62);
  --text-muted:     rgba(255,255,255,0.38);
  --glass-bg:       rgba(18,28,25,0.58);    /* Glass panel base */
  --glass-border:   rgba(255,255,255,0.12);
  --glass-shadow:   0 18px 48px rgba(0,0,0,0.42), 0 0 0 1px rgba(62,207,142,0.05), inset 0 1px 0 rgba(255,255,255,0.08);
  --glass-blur:     blur(24px) saturate(180%);
  --radius:         16px;         /* Border radius */
  --radius-sm:      10px;
  --sidebar-width:  280px;
  --header-height:  56px;
  --input-height:   72px;
  --transition:     0.15s ease;
}
```

### Glass Panel Base Styles (lines 119-127)
```css
.glass-panel {
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
  border-radius: var(--radius);
}
```
- Used for: sidebar, headers, detail panels, input bar, etc.

### Sidebar Styles (lines 289-450+)
```css
.sidebar {
  position: fixed;
  left: 0;
  top: 0;
  width: var(--sidebar-width);
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  border-right: 1px solid var(--glass-border);
  box-shadow: 18px 0 48px rgba(0,0,0,0.32);
  z-index: 40;
  transition: transform var(--transition), width var(--transition);
}

.sidebar.collapsed {
  transform: translateX(-100%);
  width: var(--sidebar-width);  /* Keep width for smooth slide */
}

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 12px;
  border-bottom: 1px solid var(--glass-border);
  flex-shrink: 0;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  flex-shrink: 0;
}

.sidebar-nav-btn {
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  transition: background var(--transition), border-color var(--transition);
  color: var(--text-secondary);
}

.sidebar-nav-btn:hover {
  background: rgba(255,255,255,0.06);
  border-color: var(--glass-border);
}

.sidebar-nav-btn.active {
  background: var(--accent-glow);
  border-color: var(--accent);
  color: var(--accent);
}
```

### Animation — fadeSlide (lines 613-618)
```css
@keyframes fadeSlide {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

.message-row {
  animation: fadeSlide 0.2s ease;
}
```

### Badge Styles
```css
.nav-badge {
  display: inline-block;
  background: var(--danger);
  color: white;
  border-radius: 10px;
  padding: 2px 6px;
  font-size: 11px;
  font-weight: 600;
  min-width: 20px;
  text-align: center;
}

.nav-badge.hidden {
  display: none;
}
```

### Notification Panel — Right Side Slide-in (lines 3545-3563)
```css
.notification-panel {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(380px, 45vw);
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, rgba(20,30,27,0.9), rgba(8,13,18,0.86));
  border-left: 1px solid var(--glass-border);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  z-index: 30;
  transition: transform 0.28s cubic-bezier(.4,0,.2,1), opacity 0.22s ease;
  overflow: hidden;
  box-shadow: -18px 0 42px rgba(0,0,0,0.32), inset 0 1px 0 rgba(255,255,255,0.05);
}

.notification-panel.notif-hidden {
  transform: translateX(100%);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
}
```
- **Key Pattern**: Uses `.notif-hidden` class with `translateX(100%)` for slide-out
- Positioned absolutely, positioned on right side
- Smooth transition with cubic-bezier easing

### Background Panel (lines 1210-1280)
```css
.background-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}

.background-panel.hidden {
  display: none;
}
```
- Uses `.hidden` class (simple `display: none`)

### Scheduler Panel (lines 1440-1520)
```css
.scheduler-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}

.scheduler-panel.hidden {
  display: none;
}
```
- Same pattern as background panel

### Request Queue Panel — Minimized State (lines 2441-2511)
```css
.request-queue-panel.queue-minimized {
  width: 42px;
}

.request-queue-panel.queue-minimized .queue-header-title,
.request-queue-panel.queue-minimized .queue-pause-btn,
.request-queue-panel.queue-minimized .queue-items-list,
.request-queue-panel.queue-minimized .queue-footer,
.request-queue-panel.queue-minimized .scratch-section {
  display: none;
}

.request-queue-panel.queue-minimized .queue-header {
  writing-mode: vertical-rl;
  text-orientation: mixed;
}
```
- Collapses to 42px width
- Hides internal content

### Responsive Media Queries
- **768px and below**: Mobile layout, sidebar becomes overlay
  ```css
  @media (max-width: 768px) {
    .sidebar { position: absolute; height: 100%; }
    .sidebar.collapsed { transform: translateX(-100%); }
    .btn-open-sidebar { display: flex !important; }
    .btn-sidebar-toggle { display: flex; }
  }
  ```
- **1024px and below**: Adjusted spacing
- **480px and below**: Smaller fonts, touch-friendly buttons (44px min height)

---

## 3. JAVASCRIPT STRUCTURE (webui/dist/app.js)

### Overall Structure
- **Size**: 138.3 KB
- **Type**: ES2020 vanilla module (no build step)
- **Pattern**: Vanilla JavaScript with async/await and fetch API

### Initial State (lines 1-30)
```javascript
const STATE = {
  token:           null,
  identity:        null,
  channel:         null,
  username:        null,
  currentSessionId: null,
  isProcessing:    false,
  isTyping:        false,
  pendingFiles:    [],
  sessions:        [],
  activeSessionId: null,
  schedulerEnabled: true,
  bgTasksEnabled:  true,
  requestQueue:    [],
  queuePaused:     false,
  currentProcessingQueueId: null,
  currentAbortController: null,  // For streaming fetch
  fileViewerOpen:  false,
  fileViewerRaw:   false,
  fileViewerPath:  null,
  fileViewerData:  null,
};
```

### DOM Helpers (lines ~90-110)
```javascript
const $ = id => document.getElementById(id);
const show = el => el.classList.remove('hidden');
const hide = el => el.classList.add('hidden');

function isMobileViewport() {
  return window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
}
```

### API Layer Pattern (lines ~120-150)
```javascript
async function apiRequest(method, path, body = null) {
  const headers = { 'Content-Type': 'application/json' };
  if (STATE.token) headers['Authorization'] = `Bearer ${STATE.token}`;

  const opts = { method, headers };
  if (body !== null) opts.body = JSON.stringify(body);

  const res = await fetch(`${API_BASE}${path}`, opts);

  if (res.status === 401) {
    clearAuth();
    showAuthView();
    throw new Error('Session expired. Please log in again.');
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}
```

### Streaming (NOT WebSocket) — Line 1423+
```javascript
const res = await fetch(`${API_BASE}/sessions/${sessionId}/stream`, {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${STATE.token}` },
  body: JSON.stringify({ prompt, /* ... */ }),
  signal: STATE.currentAbortController.signal,
});

const reader = res.body.getReader();
const decoder = new TextDecoder();

let streamBubble = null;
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  
  const chunk = decoder.decode(value);
  // Process chunk...
}
```
- **Uses fetch with response streaming**, not WebSocket
- `currentAbortController` is used to cancel mid-stream
- `stream` endpoint returns Server-Sent Events or streaming JSON

### Sidebar Toggle Function (lines 1683-1700)
```javascript
function toggleSidebar(open) {
  const sidebar = document.querySelector('.sidebar');
  const openBtn  = $('btn-open-sidebar');
  const closeBtn = $('btn-sidebar-toggle');
  if (open === undefined) open = sidebar.classList.contains('collapsed');

  if (open) {
    sidebar.classList.remove('collapsed');
    hide(openBtn);
    show(closeBtn);
  } else {
    sidebar.classList.add('collapsed');
    show(openBtn);
    hide(closeBtn);
  }
}
```
- **Pattern**: Toggle `.collapsed` class on sidebar
- Show/hide close/open buttons accordingly
- Sidebar slides in/out via CSS `transform: translateX(-100%)`

### View Panel Toggle Functions

#### showChatPanel() — lines 1977-1990
```javascript
function showChatPanel() {
  show($('chat-panel'));
  hide($('scheduler-panel'));
  hide($('background-panel'));
  show($('btn-new-chat'));
  show($('sessions-list'));
  show($('request-queue-panel'));
  $('btn-nav-chat').classList.add('active');
  $('btn-nav-scheduler').classList.remove('active');
  $('btn-nav-background').classList.remove('active');
  $('btn-nav-notifications').classList.remove('active');
  hideNotificationPanel();
  if (isMobileViewport()) toggleSidebar(false);
}
```

#### showSchedulerPanel() — lines 1992-2008
```javascript
function showSchedulerPanel() {
  hide($('chat-panel'));
  show($('scheduler-panel'));
  hide($('background-panel'));
  hide($('btn-new-chat'));
  hide($('sessions-list'));
  hide($('request-queue-panel'));
  $('btn-nav-scheduler').classList.add('active');
  $('btn-nav-chat').classList.remove('active');
  $('btn-nav-background').classList.remove('active');
  $('btn-nav-notifications').classList.remove('active');
  hideNotificationPanel();
  loadSchedulerJobs();
  loadSchedulerStatus();
  if (isMobileViewport()) toggleSidebar(false);
}
```

#### showBackgroundPanel() — lines 2009-2028
```javascript
function showBackgroundPanel() {
  hide($('chat-panel'));
  hide($('scheduler-panel'));
  show($('background-panel'));
  hide($('btn-new-chat'));
  hide($('sessions-list'));
  hide($('request-queue-panel'));
  $('btn-nav-background').classList.add('active');
  $('btn-nav-chat').classList.remove('active');
  $('btn-nav-scheduler').classList.remove('active');
  $('btn-nav-notifications').classList.remove('active');
  hideNotificationPanel();
  loadBackgroundTasks();
  if (isMobileViewport()) toggleSidebar(false);
}
```

### Notification Panel Toggle (lines 2965-2990)
```javascript
function toggleNotificationPanel() {
  const panel = $('notification-panel');
  const isHidden = panel.classList.contains('notif-hidden');
  if (isHidden) {
    panel.classList.remove('notif-hidden');
    $('btn-nav-notifications').classList.add('active');
    renderNotifications();
  } else {
    panel.classList.add('notif-hidden');
    $('btn-nav-notifications').classList.remove('active');
  }
}

function hideNotificationPanel() {
  $('notification-panel').classList.add('notif-hidden');
  $('btn-nav-notifications').classList.remove('active');
}
```
- **Key Pattern**: Uses `.notif-hidden` class (not `.hidden`)
- Slide-in/out is CSS-controlled via `transform: translateX(100%)`
- Maintains button active state

### Queue Panel Toggle (lines 965-990)
```javascript
function toggleQueuePanel() {
  const panel = $('request-queue-panel');
  panel.classList.toggle('queue-minimized');
}

function showQueuePanel() {
  $('request-queue-panel').classList.remove('queue-minimized');
}

function hideQueuePanel() {
  $('request-queue-panel').classList.add('queue-minimized');
}
```
- **Pattern**: Toggle `.queue-minimized` class
- Not hidden, just collapsed to 42px width

### Badge Update Pattern (lines 2623-2632, 2955-2961)
```javascript
function updateBgTaskBadge() {
  const badge = $('bg-task-badge');
  if (!badge) return;
  
  const running = bg_tasks.filter(t => t.status === 'running').length;
  const queued  = bg_tasks.filter(t => t.status === 'queued').length;
  
  if (running > 0 || queued > 0) {
    badge.textContent = queued > 0 ? `${running}+${queued}` : running;
    show(badge);
  } else {
    hide(badge);
  }
}
```

### Event Listeners — Init Section (lines 1773-1810+)
```javascript
document.addEventListener('DOMContentLoaded', () => {
  // Sidebar
  $('btn-new-chat').addEventListener('click', () => {
    startNewSession();
    if (isMobileViewport()) toggleSidebar(false);
  });
  $('btn-logout').addEventListener('click', () => { clearAuth(); showAuthView(); });
  $('btn-sidebar-toggle').addEventListener('click', () => toggleSidebar(false));
  $('btn-open-sidebar').addEventListener('click',  () => toggleSidebar(true));
  $('btn-sched-open-sidebar').addEventListener('click', () => toggleSidebar(true));
  if ($('btn-bg-open-sidebar')) $('btn-bg-open-sidebar').addEventListener('click', () => toggleSidebar(true));

  // View nav
  $('btn-nav-chat').addEventListener('click', showChatPanel);
  $('btn-nav-background').addEventListener('click', showBackgroundPanel);
  $('btn-nav-scheduler').addEventListener('click', showSchedulerPanel);
  $('btn-nav-notifications').addEventListener('click', toggleNotificationPanel);

  // Notification actions
  $('btn-notif-mark-all-read').addEventListener('click', async () => {
    try {
      await apiRequest('POST', '/notifications/read-all');
      await pollNotifications();
    } catch { /* ignore */ }
  });
  // ... more listeners
});
```

### Key Functions & Line Numbers
| Function | Line |
|----------|------|
| `toggleSidebar(open)` | 1683 |
| `showChatPanel()` | 1977 |
| `showSchedulerPanel()` | 1992 |
| `showBackgroundPanel()` | 2009 |
| `toggleNotificationPanel()` | 2965 |
| `hideNotificationPanel()` | 2978 |
| `toggleQueuePanel()` | 965 |
| `showQueuePanel()` | 871 |
| `hideQueuePanel()` | 878 |
| `updateBgTaskBadge()` | 2623 |
| `updateNotifBadge()` | 2955 |
| `startStreamingMessage()` | 1423 |
| `apiRequest()` | ~120 |

---

## 4. PYTHON FASTAPI STRUCTURE (agent_manager.py)

### File Size & Location
- **Size**: 7300 lines
- **Location**: `/opt/n8n-copilot-shim-dev/agent_manager.py`

### Imports (lines 1-22)
```python
import sys
import os
import json
import subprocess
import re
import signal
import time
import argparse
import shutil
import logging
from pathlib import Path
from uuid import uuid4
from typing import Optional, Tuple, Dict, List
import secrets as _secrets
import threading
```

### Key Classes (before create_api_app)
- **RateLimiter** (lines 32-47): Per-IP rate limiting with sliding window
- **AuthManager** (lines 61-180): Manages pairing codes, session tokens
- **BackgroundTaskManager** (lines 206-402): Manages async background tasks
- **SessionManager** (lines 542-691): Manages chat sessions
- **UsageTracker** (lines 695-776): Tracks Copilot runtime usage
- **SessionManager** (main agent/skill manager, lines 953+)

### FastAPI App Factory (line 5012)
```python
def create_api_app():  # noqa: C901 – factory kept in one place intentionally
    """Factory that builds and returns the FastAPI application."""
    import asyncio
    import concurrent.futures
    from enum import Enum

    from fastapi import FastAPI, Header, HTTPException, Request, UploadFile, File
    from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, field_validator
    import mimetypes

    # ---- configuration from environment ----
    APP_ENV = os.environ.get("APP_ENV", "PROD").upper()
    IS_PRODUCTION = APP_ENV != "DEV"
    SHARED_KEY = os.environ.get("API_SHARED_KEY", "")

    # Create managers (lines 5030+)
    auth_mgr = AuthManager(...)
    rate_limiter = RateLimiter()
    bg_task_mgr = BackgroundTaskManager()
    usage_tracker = UsageTracker()
    session_mgr = SessionManager(...)

    # FastAPI app creation (lines 5165+)
    app = FastAPI(
        title="Wee-Orchestrator API",
        version="1.0.0",
        docs_url="/api/v1/docs" if not IS_PRODUCTION else None,
        redoc_url="/api/v1/redoc" if not IS_PRODUCTION else None,
        lifespan=_lifespan,
    )

    # CORS middleware (lines 5177+)
    cors_origins = [o.strip() for o in os.environ.get("API_CORS_ORIGINS", "").split(",") if o.strip()]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-User-Identity", "X-Auth-Channel"],
        )

    # Global exception handler (lines 5185+)
    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        if IS_PRODUCTION:
            return JSONResponse(status_code=500, content={"detail": "Internal server error"})
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    # ---- endpoints ----
```

### Endpoint Structure — All @app Decorators (from line 5193 onwards)
**Auth Endpoints**:
- `@app.post("/api/v1/auth/request-pairing")` — line 5278
- `@app.post("/api/v1/auth/verify-pairing")` — line 5305

**Session Endpoints**:
- `@app.post("/api/v1/sessions/create")` — line 5324
- `@app.post("/api/v1/sessions/{session_id}/execute")` — line 5363
- `@app.post("/api/v1/sessions/{session_id}/stream")` — line 5439 ⭐ **STREAMING ENDPOINT**
- `@app.get("/api/v1/sessions/{session_id}/status")` — line 5619
- `@app.post("/api/v1/sessions/{session_id}/cancel")` — line 5649

**File/Upload Endpoints**:
- `@app.post("/api/v1/sessions/{session_id}/upload")` — line 5748
- `@app.get("/api/v1/uploads/{session_id}/{filename}")` — line 5776
- `@app.get("/api/v1/files/view")` — line 5813
- `@app.get("/api/v1/files/view/raw")` — line 5886

**Transcription Endpoints**:
- `@app.post("/api/v1/sessions/{session_id}/transcribe")` — line 5931
- `@app.get("/api/v1/transcription/status")` — line 5975

**Scratch Notes**:
- `@app.get("/api/v1/sessions/{session_id}/scratch")` — line 5989
- `@app.post("/api/v1/sessions/{session_id}/scratch")` — line 6016

**Background Tasks**:
- `@app.post("/api/v1/background-tasks")` — line 6174
- `@app.get("/api/v1/background-tasks")` — line 6330
- `@app.get("/api/v1/background-tasks/{task_id}")` — line 6363
- `@app.get("/api/v1/background-tasks/{task_id}/transcript")` — line 6392
- `@app.delete("/api/v1/background-tasks/{task_id}")` — line 6413

**Notifications**:
- `@app.get("/api/v1/notifications")` — line 6454
- `@app.post("/api/v1/notifications/{notification_id}/read")` — line 6470
- `@app.post("/api/v1/notifications/read-all")` — line 6487
- `@app.delete("/api/v1/notifications/{notification_id}")` — line 6502
- `@app.delete("/api/v1/notifications")` — line 6519

**Scheduler Endpoints** (if enabled):
- `@app.get("/api/v1/scheduler/status")` — line 6625
- `@app.get("/api/v1/scheduler/jobs")` — line 6630
- `@app.post("/api/v1/scheduler/jobs")` — line 6635
- `@app.get("/api/v1/scheduler/jobs/{job_id}")` — line 6666
- `@app.put("/api/v1/scheduler/jobs/{job_id}")` — line 6674
- `@app.delete("/api/v1/scheduler/jobs/{job_id}")` — line 6688
- `@app.post("/api/v1/scheduler/jobs/{job_id}/pause")` — line 6699
- `@app.post("/api/v1/scheduler/jobs/{job_id}/resume")` — line 6710
- `@app.get("/api/v1/scheduler/jobs/{job_id}/results")` — line 6721
- `@app.get("/api/v1/scheduler/jobs/{job_id}/logs")` — line 6727

**TODOs**:
- `@app.get("/api/v1/todos")` — line 6928
- `@app.post("/api/v1/todos/{todo_title}/complete")` — line 6941
- `@app.patch("/api/v1/todos/{todo_title}")` — line 6978

### Streaming Response Example (line 5439 area)
The `/sessions/{session_id}/stream` endpoint uses `StreamingResponse` to send chunks as they arrive:
```python
@app.post("/api/v1/sessions/{session_id}/stream")
async def stream_message(session_id: str, payload: ExecutePayload, auth: dict = Depends(authenticate)):
    # ... processing ...
    
    async def event_generator():
        while True:
            chunk = ... # Get next chunk
            if not chunk:
                yield json.dumps({"type": "done"}) + "\n"
                break
            yield json.dumps({"type": "text", "content": chunk}) + "\n"
    
    return StreamingResponse(event_generator(), media_type="application/x-ndjson")
```

### Authentication Dependency (lines 5125-5160)
```python
async def authenticate(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_identity: Optional[str] = Header(None),
    x_auth_channel: Optional[str] = Header(None),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

    token = authorization[7:]

    if token.startswith("shared_"):
        if not auth_mgr.validate_shared_key(token):
            raise HTTPException(status_code=401, detail="Invalid shared key")
        return {
            "identity": x_user_identity if is_local else "shared_key_user",
            "channel": x_auth_channel or "api",
            "auth_type": "shared_key",
        }

    if token.startswith("session_"):
        token_data = auth_mgr.validate_session_token(token)
        if not token_data:
            raise HTTPException(status_code=401, detail="Invalid or expired session token")
        return {
            "identity": token_data["identity"],
            "channel": token_data["channel"],
            "auth_type": "session_token",
        }

    raise HTTPException(status_code=401, detail="Unrecognized token type")
```

### Static File Mounting (end of create_api_app function)
```python
# --- AI Media ─────────────────────────────────────────────────────────────
_ai_media_dir = Path("/tmp/webui_ai_media")
_ai_media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/ai-media", StaticFiles(directory=str(_ai_media_dir)), name="ai_media")

# --- Static WebUI — MUST BE LAST ---
_webui_dist = Path(__file__).parent / "webui" / "dist"
if _webui_dist.exists():
    app.mount("/ui", StaticFiles(directory=str(_webui_dist), html=True), name="webui")

_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

return app
```

### Server Startup (start_api_server function, around line 6050)
```python
def start_api_server():
    """Load dotenv, create the FastAPI app, and run uvicorn."""
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        load_dotenv(env_path)
    except ImportError:
        pass

    import uvicorn

    app = create_api_app()
    port = int(os.environ.get("API_PORT", "8001"))
    host = os.environ.get("API_HOST", "127.0.0.1")
    
    # SSL support
    ssl_certfile = os.environ.get("SSL_CERTFILE")
    ssl_keyfile = os.environ.get("SSL_KEYFILE")
    
    uvicorn.run(app, host=host, port=port, **ssl_kwargs)
```

---

## SUMMARY OF PATTERNS FOR CANVAS FEATURE

### To Add Canvas Functionality, Follow These Patterns:

#### **HTML Pattern**
1. Create a new panel section like:
   ```html
   <section id="canvas-panel" class="canvas-panel hidden">
     <header class="canvas-header glass-panel">
       <button id="btn-canvas-open-sidebar" class="btn-icon sidebar-open-btn hidden">☰</button>
       <h2 class="canvas-title">Canvas</h2>
     </header>
     <div class="canvas-body">
       <!-- Content -->
     </div>
   </section>
   ```

2. Add nav button in sidebar:
   ```html
   <button id="btn-nav-canvas" class="sidebar-nav-btn">🎨 Canvas</button>
   ```

3. For badges (if needed):
   ```html
   <span id="canvas-badge" class="nav-badge hidden">0</span>
   ```

#### **CSS Pattern**
1. Glass panel styling:
   ```css
   .canvas-panel {
     flex: 1;
     display: flex;
     flex-direction: column;
     overflow: hidden;
     position: relative;
   }
   
   .canvas-panel.hidden {
     display: none;
   }
   ```

2. Header styling:
   ```css
   .canvas-header {
     display: flex;
     align-items: center;
     justify-content: space-between;
     padding: 0 20px;
     height: var(--header-height);
     border-radius: 0;
     flex-shrink: 0;
     gap: 12px;
   }
   ```

3. For animations, use:
   ```css
   animation: fadeSlide 0.2s ease;
   ```

#### **JavaScript Pattern**
1. Create toggle function:
   ```javascript
   function showCanvasPanel() {
     show($('canvas-panel'));
     hide($('chat-panel'));
     hide($('scheduler-panel'));
     hide($('background-panel'));
     $('btn-nav-canvas').classList.add('active');
     // Remove active from other nav buttons
     hideNotificationPanel();
     loadCanvasData();
     if (isMobileViewport()) toggleSidebar(false);
   }
   ```

2. Add event listener:
   ```javascript
   $('btn-nav-canvas').addEventListener('click', showCanvasPanel);
   ```

3. For badges:
   ```javascript
   function updateCanvasBadge(count) {
     const badge = $('canvas-badge');
     if (count > 0) {
       badge.textContent = count;
       show(badge);
     } else {
       hide(badge);
     }
   }
   ```

4. For fetching data:
   ```javascript
   async function loadCanvasData() {
     try {
       const data = await apiRequest('GET', '/canvas');
       // Render data
     } catch (err) {
       console.error(err);
     }
   }
   ```

#### **Python API Pattern**
1. Create endpoint in `create_api_app()` around line 5193+:
   ```python
   @app.get("/api/v1/canvas")
   async def get_canvas(auth: dict = Depends(authenticate)):
       """Get canvas data."""
       try:
           # Implementation
           return {"items": []}
       except Exception as e:
           raise HTTPException(status_code=500, detail=str(e))
   ```

2. For streaming (if needed):
   ```python
   @app.post("/api/v1/canvas/stream")
   async def stream_canvas(payload: dict, auth: dict = Depends(authenticate)):
       async def event_generator():
           # Yield chunks
           while True:
               chunk = ...
               if not chunk:
                   yield json.dumps({"type": "done"}) + "\n"
                   break
               yield json.dumps({"type": "text", "content": chunk}) + "\n"
       
       return StreamingResponse(event_generator(), media_type="application/x-ndjson")
   ```

3. Add before the static file mounts (end of function):
   ```python
   # --- Static WebUI — MUST BE LAST ---
   ```

---

## KEY DESIGN PRINCIPLES

1. **Glass Morphism**: All panels use `.glass-panel` with blur and transparency
2. **Color Scheme**:
   - Accent (Green): `#3ecf8e` for active states
   - Gold (Secondary): `#f5c542` for alternative actions
   - Danger (Red): `#ff5f6d` for errors
3. **Animation**: Use `fadeSlide` 0.2s ease for content, cubic-bezier for panel slides
4. **Mobile-First**: Always hide sidebar on mobile, use hamburger menu
5. **State Management**: Use `STATE` object in JS for tracking
6. **API Pattern**: All endpoints follow `/api/v1/` prefix with auth dependency
7. **Streaming**: Use `StreamingResponse` with newline-delimited JSON (NDJSON)
8. **Badges**: Use `.nav-badge` with `.hidden` class for visibility control
