# Canvas Feature Implementation Guide

This document provides comprehensive details about the Wee Orchestrator codebase structure and patterns to help implement the Canvas feature.

## 📋 Documentation Files

Three detailed documentation files have been created:

1. **`WEE_STRUCTURE_PATTERNS.md`** (1099 lines)
   - Complete breakdown of HTML, CSS, JavaScript, and Python code
   - Line numbers for all key functions
   - Detailed pattern explanations
   - **READ THIS** for deep understanding of existing implementations

2. **`CANVAS_QUICK_REFERENCE.md`** (Ready-to-use implementation guide)
   - Copy-paste code for HTML, CSS, JS, and Python
   - Exact line numbers where to insert code
   - Implementation checklist
   - Quick testing guide
   - **USE THIS** to quickly implement the feature

3. **`README_CANVAS_IMPLEMENTATION.md`** (This file)
   - Overview and architecture
   - Key patterns summary
   - Testing strategy

## 🎯 Architecture Overview

### Frontend Structure (WebUI)

```
webui/dist/
├── index.html          (365 lines - panel definitions)
├── app.css             (84.9 KB - all styles)
└── app.js              (138.3 KB - all logic)
```

**Design Pattern**: Glass morphism with dark theme
- **Colors**: Green accent (#3ecf8e), Gold secondary (#f5c542), Red danger (#ff5f6d)
- **Panels**: Each view (Chat, Scheduler, Background, Canvas) is a full-screen section
- **Sidebar**: Fixed left side (280px wide), toggles to hidden on mobile
- **Responsive**: Breakpoints at 768px (mobile), 1024px (tablet), 1400px (large)

### Backend Structure (API)

```
agent_manager.py       (7300 lines - FastAPI app factory)
├── Classes (before line 5012)
│   ├── RateLimiter
│   ├── AuthManager
│   ├── BackgroundTaskManager
│   ├── SessionManager
│   └── UsageTracker
├── create_api_app()    (line 5012 - app factory)
│   ├── FastAPI setup   (line 5165+)
│   ├── Endpoints       (line 5193+)
│   └── Static mounts   (end of function)
└── start_api_server()  (line ~6050)
```

**API Pattern**: RESTful with `/api/v1/` prefix
- All endpoints require Bearer token auth (session or shared key)
- Uses `Depends(authenticate)` dependency injection
- Streaming via `StreamingResponse` (not WebSocket)
- NDJSON format for streaming (newline-delimited JSON)

## 🔑 Key Patterns

### 1. Panel Toggle Pattern

**HTML**: Use `hidden` class to toggle visibility
```html
<section id="canvas-panel" class="canvas-panel hidden"></section>
```

**CSS**: Apply display:none via .hidden class
```css
.canvas-panel { flex: 1; display: flex; }
.canvas-panel.hidden { display: none; }
```

**JavaScript**: Use show/hide helpers
```javascript
function showCanvasPanel() {
  show($('canvas-panel'));     // Removes .hidden class
  hide($('chat-panel'));       // Adds .hidden class
}
```

### 2. Navigation Pattern

**Sidebar Button**:
```html
<button id="btn-nav-canvas" class="sidebar-nav-btn">🎨 Canvas</button>
```

**Active State**: Toggle `.active` class on button
```javascript
$('btn-nav-canvas').classList.add('active');
```

**All Panels**: Each panel hides notification panel and collapses sidebar on mobile
```javascript
hideNotificationPanel();
if (isMobileViewport()) toggleSidebar(false);
```

### 3. Badge Pattern

**HTML**: Use `.nav-badge` span with `hidden` class
```html
<span id="canvas-badge" class="nav-badge hidden">0</span>
```

**Show/Hide**: Toggle `.hidden` class
```javascript
function updateCanvasBadge(count) {
  const badge = $('canvas-badge');
  if (count > 0) {
    badge.textContent = count;
    show(badge);      // Removes .hidden
  } else {
    hide(badge);      // Adds .hidden
  }
}
```

### 4. Data Loading Pattern

**Fetch Data**:
```javascript
async function loadCanvasData() {
  try {
    const data = await apiRequest('GET', '/canvas');
    renderCanvasPanel(data.items || []);
  } catch (err) {
    console.error('Failed to load canvas:', err);
  }
}
```

**Render**:
```javascript
function renderCanvasPanel(items) {
  const container = $('canvas-container');
  if (!items.length) {
    container.innerHTML = '<p class="canvas-empty">No items</p>';
    return;
  }
  
  container.innerHTML = items.map(item => `
    <div class="canvas-element">
      <strong>${escHtml(item.title)}</strong>
    </div>
  `).join('');
}
```

### 5. API Endpoint Pattern

```python
@app.get("/api/v1/canvas")
async def get_canvas(auth: dict = Depends(authenticate)):
    """Get canvas items for authenticated user."""
    try:
        items = []  # Load from storage
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Requirements**:
- Use `@app.get()` or `@app.post()` decorator
- Include `auth: dict = Depends(authenticate)` parameter
- Return JSON dict
- Raise `HTTPException` for errors
- Add before line ~6450 (before notifications endpoints)

### 6. CSS Styling Pattern

**Glass Panel Base**:
```css
.canvas-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}
```

**Header**:
```css
.canvas-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  height: var(--header-height);  /* 56px */
  border-radius: 0;
  flex-shrink: 0;
  gap: 12px;
}
```

**Body Content**:
```css
.canvas-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 16px;
}

.canvas-container {
  flex: 1;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
```

**Item Card**:
```css
.canvas-element {
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-sm);
  padding: 14px 16px;
  cursor: pointer;
  transition: background var(--transition), border-color var(--transition);
  animation: fadeSlide 0.2s ease;
}

.canvas-element:hover {
  background: rgba(255,255,255,0.08);
  border-color: rgba(255,255,255,0.15);
}
```

## 📊 Code Statistics

| File | Size | Type | Key Lines |
|------|------|------|-----------|
| index.html | 365 lines | HTML | 67-88 (sidebar), 91-339 (panels) |
| app.css | 84.9 KB | CSS | 12-28 (vars), 119+ (glass), 1210+ (bg panel) |
| app.js | 138.3 KB | JS | 1683 (toggleSidebar), 1977+ (panel funcs) |
| agent_manager.py | 7300 lines | Python | 5012 (create_api_app), 5193+ (endpoints) |

## 🔌 Integration Points

### Frontend → Backend

**Data Flow**:
1. User clicks `#btn-nav-canvas` button
2. `showCanvasPanel()` runs (shows panel, hides others)
3. `loadCanvasData()` called
4. Fetches `GET /api/v1/canvas` with Bearer token
5. Response: `{"items": [...]}` (JSON)
6. `renderCanvasPanel()` renders items
7. `updateCanvasBadge()` shows count

**Session Management**:
- Token stored in `STATE.token` (set during auth)
- User identity in `STATE.identity`
- Channel in `STATE.channel`
- All API calls include: `Authorization: Bearer {STATE.token}`

### Backend → Database

Storage options (implement as needed):
- Filesystem JSON (like background tasks)
- SQLite database
- Cloud storage
- In-memory cache

Suggestion: Mirror BackgroundTaskManager pattern (JSON file in `.task-scheduler/` dir)

## 🧪 Testing Strategy

### Unit Tests
- Panel toggle visibility
- Badge show/hide
- Data rendering
- API endpoint responses

### Integration Tests
- Click nav button → panel shows
- Fetch data → renders items
- Clear button → reloads data
- Mobile sidebar toggle

### E2E Tests
- Full user flow: sidebar → canvas → navigate away
- Network requests via DevTools
- Responsive behavior on mobile

### Quick Manual Test

```bash
# 1. Hard refresh browser (Ctrl+Shift+R)
# 2. Open DevTools (F12)
# 3. Go to Network tab
# 4. Click Canvas nav button
# 5. Watch for GET /api/v1/canvas request
# 6. Check Response tab for JSON
# 7. Verify canvas panel displays with items
```

## 🚀 Implementation Order

1. **HTML** — Add panel markup and nav button (5 min)
2. **CSS** — Add styles for panel (10 min)
3. **JavaScript** — Add functions and listeners (15 min)
4. **Python API** — Add endpoints (10 min)
5. **Testing** — Verify all interactions (10 min)

**Total**: ~50 minutes for basic implementation

## 🎨 Design Decisions

### Why `.hidden` vs Other Methods?
- Used consistently throughout codebase
- Simple `display: none` toggle
- Works with show/hide helpers
- CSS variable support

### Why `.notif-hidden` is Different
- Notification panel slides in from right
- Uses `transform: translateX(100%)` for animation
- Needs separate class to preserve animation
- Right-side overlay panel (not full-width)

### Why Streaming Not WebSocket?
- Simpler to implement (no server push needed)
- Fetch API with streaming reader
- Better compatibility
- See line 1423 in app.js

### Why `/api/v1/` Prefix?
- Future-proof versioning
- Clear separation of concerns
- Standard REST API convention
- All endpoints follow this pattern

## 📚 Reference Materials

### CSS Variables Available
```css
--accent:         #3ecf8e     /* Green */
--gold:           #f5c542     /* Orange */
--danger:         #ff5f6d     /* Red */
--text-primary:   rgba(255,255,255,0.92)
--text-secondary: rgba(255,255,255,0.62)
--text-muted:     rgba(255,255,255,0.38)
--glass-bg:       rgba(18,28,25,0.58)
--glass-border:   rgba(255,255,255,0.12)
--glass-shadow:   [complex shadow]
--glass-blur:     blur(24px) saturate(180%)
--radius:         16px
--radius-sm:      10px
--sidebar-width:  280px
--header-height:  56px
```

### Common Functions in app.js
```javascript
const $ = id => document.getElementById(id)
const show = el => el.classList.remove('hidden')
const hide = el => el.classList.add('hidden')
const escHtml = str => new DOMParser().parseFromString(str, 'text/html').body.textContent
const apiRequest = async (method, path, body) => { /* ... */ }
const isMobileViewport = () => window.matchMedia('(max-width: 768px)').matches
```

### Common Buttons Pattern
```javascript
// Event listeners use arrow functions
$('btn-id').addEventListener('click', async () => {
  try {
    await apiRequest('POST', '/endpoint', payload);
    await reloadData();  // Refresh display
  } catch (err) {
    console.error('Failed:', err);
  }
});
```

## ⚠️ Common Pitfalls to Avoid

1. **Forgetting `.hidden` class** — Panel won't hide
2. **Wrong auth header** — API will return 401
3. **Missing `Depends(authenticate)`** — Endpoint will fail
4. **Not escaping HTML** — XSS vulnerability
5. **Forgetting mobile checks** — Sidebar won't toggle on phone
6. **Not adding event listeners** — Buttons won't work
7. **CSS outside media queries** — Mobile layout breaks
8. **Missing animation** — UI feels unresponsive

## 📞 Support & References

- **Line numbers**: See WEE_STRUCTURE_PATTERNS.md for exact locations
- **Code samples**: See CANVAS_QUICK_REFERENCE.md for copy-paste ready code
- **Test locally**: `npm run dev` or `python agent_manager.py`
- **View docs**: Open `/api/v1/docs` in browser (if not production)

---

**Last Updated**: Based on files in `/opt/n8n-copilot-shim-dev/`

