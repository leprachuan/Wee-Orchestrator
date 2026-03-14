# Live Canvas

A native real-time visual panel built into the WebUI. Slides in from the right side of the screen. Multiple sessions appear as tabs. Users can interact via buttons and forms.

## When to use
- **Multi-step tasks**: show a live progress board as you work
- **Plan approval**: render a flowchart or card list with Approve/Reject buttons
- **Data results**: charts, tables, dashboards
- **Config gathering**: render a form and wait for user input
- **Long deploys or batch jobs**: live status updates

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

## WebSocket endpoint
`ws://127.0.0.1:<API_PORT>/canvas/ws?session=SESSION_ID`

## Sessions API
`GET /api/v1/canvas/sessions` — list active sessions and their component state
