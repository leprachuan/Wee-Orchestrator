# Wee Canvas

A native real-time visual panel built into the WebUI. Slides in from the right side of the screen. Multiple sessions appear as tabs. Users can interact via buttons and forms.

## When to use
- **Multi-step tasks**: show a live progress board as you work
- **Plan approval**: render a flowchart or card list with Approve/Reject buttons
- **Data results**: charts, tables, dashboards
- **Config gathering**: render a form and wait for user input
- **Long deploys or batch jobs**: live status updates

## Session Lifecycle

### Auto-Expiry
Sessions automatically expire after **30 minutes** of inactivity (no active WebSocket connections). When a session expires:
- Its state (components, name) is **persisted to disk** at `.canvas-sessions/{session_id}.json`
- It appears in the **closed sessions** list in the WebUI
- The timeout is configurable via `CANVAS_SESSION_TIMEOUT_MINUTES` env var (default: 30)

### Session Naming
- **Double-click** a tab label to rename a session
- Names are saved immediately via `PATCH /api/v1/canvas/sessions/{session_id}/name`
- Named sessions are easier to identify in the closed sessions list

### Restoring Closed Sessions
Closed sessions can be restored in two ways:

1. **WebUI**: Click the **Restore** button in the closed sessions list below the tab bar
2. **API**: `POST /api/v1/canvas/sessions/{session_id}/restore`

When restored, the session's components are loaded back into memory and it becomes active again.

## Quick start

```python
import sys; sys.path.insert(0, '/opt/n8n-copilot-shim-dev')
from canvas import Canvas

c = Canvas()    # auto-generates a session ID
c.open()        # opens the panel in the WebUI

# Push a progress board
c.render_template("progress_board", {
    "title": "Deploy Progress",
    "items": [
        {"label": "Build", "status": "done"},
        {"label": "Test",  "status": "in_progress"},
        {"label": "Push",  "status": "pending"},
    ]
})

# Wait for a user action (e.g. an Approve button click)
action = c.wait_for_action(timeout=120)
print(action)  # {"type": "button_click", "id": "approve", ...}

# Update a single component
c.update_component("step-test", {"status": "done"})

# Clear the canvas
c.clear()
```

## Canvas Python API (`/opt/n8n-copilot-shim-dev/canvas.py`)
| Method | Description |
|--------|-------------|
| `Canvas(session_id=None)` | Create canvas. Auto-generates session ID if not provided. |
| `c.open()` | Open the panel in the WebUI |
| `c.render_template(name, data)` | Render a named template with data |
| `c.push_component(component_dict)` | Push a raw component |
| `c.push_html(html, height, id)` | Push HTML/JS in a sandboxed iframe |
| `c.update_component(id, changes)` | Update a rendered component by ID |
| `c.clear()` | Clear all components |
| `c.wait_for_action(timeout=60)` | Block until user interaction; returns action dict |

## Available templates
- `progress_board` — list of steps with status (done/in_progress/pending/error)
- `data_dashboard` — metrics grid + optional chart
- `approval_flow` — description + Approve/Reject buttons
- `form` — labelled input fields + submit button
- `table` — headed rows of data
- `card_grid` — grid of cards with title/body/optional button

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/canvas/sessions` | List active and closed sessions |
| `GET` | `/api/v1/canvas` | Summary of active sessions with components |
| `PATCH` | `/api/v1/canvas/sessions/{id}/name` | Set session name (`{"name": "My Dashboard"}`) |
| `POST` | `/api/v1/canvas/sessions/{id}/restore` | Restore a closed session |
| `POST` | `/api/v1/canvas/sessions/{id}/close` | Explicitly close and persist a session |

## WebSocket endpoint
`ws://127.0.0.1:<API_PORT>/canvas/ws?session=SESSION_ID`

## HTML Component (Sandboxed Iframe)

The `html` component type renders arbitrary HTML and JavaScript inside a **sandboxed iframe** (`sandbox="allow-scripts"`, no `allow-same-origin`). This is ideal for interactive visualisations, embedded charts, or any self-contained HTML snippet.

### Component schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"html"` | ✅ | Component type |
| `content` | `str` | ✅ | Full HTML string (may include `<script>` tags) |
| `height` | `int` | ❌ | Iframe height in pixels (default `400`) |
| `id` | `str` | ❌ | Component ID for later updates |

### Python convenience method — `push_html()`

```python
c = Canvas()
c.push_html(html_content, height=500, component_id="my-chart")
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `html_content` | `str` | — | Full HTML string |
| `height` | `int` | `400` | Iframe height in pixels |
| `component_id` | `str\|None` | auto | Optional ID; auto-generated if omitted |

**Returns:** the `component_id` used.

### Iframe sandbox notes

- The iframe uses `sandbox="allow-scripts"` — scripts can run but **cannot** access the parent page (no `allow-same-origin`).
- To resize the iframe dynamically from inside the content, post a message:
  ```js
  parent.postMessage({ type: "resize", height: document.body.scrollHeight }, "*");
  ```
  The canvas listener will update the iframe height accordingly.

### Example — Chart.js chart via `push_html()`

```python
import sys; sys.path.insert(0, '/opt/n8n-copilot-shim-dev')
from canvas import Canvas

chart_html = """
<!DOCTYPE html>
<html><head>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>body{margin:0;background:transparent;}</style>
</head><body>
<canvas id="c"></canvas>
<script>
new Chart(document.getElementById('c'), {
  type: 'bar',
  data: {
    labels: ['Jan','Feb','Mar','Apr','May'],
    datasets: [{
      label: 'Revenue ($k)',
      data: [12, 19, 8, 15, 22],
      backgroundColor: 'rgba(62,207,142,0.6)'
    }]
  },
  options: { responsive: true }
});
// Auto-resize iframe to fit content
setTimeout(() => {
  parent.postMessage({ type: 'resize', height: document.body.scrollHeight }, '*');
}, 500);
</script>
</body></html>
"""

c = Canvas()
c.push_html(chart_html, height=350, component_id="revenue-chart")
c.open()
```

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `CANVAS_SESSION_TIMEOUT_MINUTES` | `30` | Minutes of inactivity before auto-persist |
