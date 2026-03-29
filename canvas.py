"""
Canvas — agent-facing API for the native Wee-Orchestrator Live Canvas.

Usage:
    from canvas import Canvas

    c = Canvas()                              # auto-generates session ID
    c.render_template("progress_board", {...})
    c.open()                                  # opens canvas panel in WebUI
    action = c.wait_for_action(timeout=60)    # blocks until user clicks
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

# Auto-load .env from the project root so SSL_CERTFILE, API_PORT etc. are available.
# Use override=True so the local project config wins over inherited env vars
# (e.g. when invoked from prod, the dev .env port must take precedence).
try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).resolve().parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
except ImportError:
    pass

SERVER_PORT = int(os.environ.get("CANVAS_PORT", os.environ.get("API_PORT", 8001)))
SERVER_HOST = os.environ.get("CANVAS_HOST", "127.0.0.1")


def _tls_enabled() -> bool:
    """Detect TLS: explicit CANVAS_HTTPS flag, or infer from SSL_CERTFILE."""
    env = os.environ.get("CANVAS_HTTPS", "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    # Auto-detect: if SSL cert is configured, assume HTTPS
    cert = os.environ.get("SSL_CERTFILE", "")
    return bool(cert and os.path.isfile(cert))


class Canvas:
    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or str(uuid.uuid4())[:8]

    def _ws_url(self) -> str:
        scheme = "wss" if _tls_enabled() else "ws"
        return f"{scheme}://{SERVER_HOST}:{SERVER_PORT}/canvas/ws?session={self.session_id}"

    def viewer_url(self) -> str:
        """Return browser URL for the WebUI with this canvas session."""
        scheme = "https" if _tls_enabled() else "http"
        return f"{scheme}://{SERVER_HOST}:{SERVER_PORT}/ui/?canvas={self.session_id}"

    def _send(self, message: dict):
        """Send a single message to the canvas WebSocket."""
        asyncio.run(self._async_send(message))

    async def _async_send(self, message: dict):
        import ssl as _ssl

        import websockets

        ssl_ctx = None
        if self._ws_url().startswith("wss://"):
            ssl_ctx = _ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = _ssl.CERT_NONE
        async with websockets.connect(self._ws_url(), ssl=ssl_ctx) as ws:
            await ws.send(json.dumps(message))
            # Wait for server to process the message before the connection closes.
            # Without this, the close frame can arrive before the server reads the data.
            await asyncio.sleep(0.15)

    # ── Public API ────────────────────────────────────────────────────────────

    def push_component(self, component: dict):
        """Push a single component to the canvas."""
        self.render([component])

    def push_html(
        self, html_content: str, height: int = 400, component_id: Optional[str] = None
    ) -> str:
        """Push arbitrary HTML/JS into the canvas, rendered in a sandboxed iframe.

        Args:
            html_content: Full HTML string (can include <script> tags).
            height: Default iframe height in pixels (default 400).
            component_id: Optional component ID; auto-generated if omitted.

        Returns:
            The component_id used.
        """
        cid = component_id or f"html-{uuid.uuid4().hex[:8]}"
        self.push_component(
            {"id": cid, "type": "html", "content": html_content, "height": height}
        )
        return cid

    def render(self, components: list):
        """Push a full component tree to the canvas."""
        self._send(
            {"type": "render", "components": components, "session_id": self.session_id}
        )

    def render_template(self, name: str, data: dict):
        """Render a built-in template. Templates: progress_board, data_dashboard, config_form, plan_view."""
        components = _build_template(name, data)
        self.render(components)

    def update(self, node_id: str, changes: dict):
        """Partially update a component node by its id."""
        self._send({"type": "update", "node_id": node_id, "changes": changes})

    def clear(self):
        """Clear all components from the canvas."""
        self._send({"type": "clear", "session_id": self.session_id})

    def open(self):
        """Open the canvas viewer in the default browser."""
        import subprocess

        url = self.viewer_url()
        try:
            subprocess.Popen(
                ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            try:
                subprocess.Popen(
                    ["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except FileNotFoundError:
                print(f"Open in browser: {url}")

    def wait_for_action(self, timeout: int = 60) -> dict:
        """Block until the user clicks a button or submits a form. Returns the action dict."""
        return asyncio.run(self._async_wait_for_action(timeout))

    async def _async_wait_for_action(self, timeout: int) -> dict:
        import ssl as _ssl

        import websockets

        ws_url = self._ws_url()
        deadline = time.time() + timeout

        ssl_ctx = None
        if ws_url.startswith("wss://"):
            ssl_ctx = _ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = _ssl.CERT_NONE

        async with websockets.connect(ws_url, ssl=ssl_ctx) as ws:
            await ws.send(
                json.dumps({"type": "subscribe_actions", "session_id": self.session_id})
            )

            while time.time() < deadline:
                remaining = max(1.0, deadline - time.time())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 5.0))
                    data = json.loads(raw)
                    if data.get("type") == "action":
                        return data
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break

        return {"type": "timeout"}


# ── Template builders ────────────────────────────────────────────────────────


def _status_icon(status: str) -> str:
    icons = {
        "done": "✅",
        "running": "🔄",
        "pending": "⏳",
        "error": "❌",
        "skip": "⏭️",
    }
    return icons.get(status, "•")


def _build_template(name: str, data: dict) -> list:
    builders = {
        "progress_board": _tpl_progress_board,
        "data_dashboard": _tpl_data_dashboard,
        "config_form": _tpl_config_form,
        "plan_view": _tpl_plan_view,
    }
    builder = builders.get(name)
    if not builder:
        raise ValueError(f"Unknown template: {name!r}. Available: {list(builders)}")
    return builder(data)


def _tpl_progress_board(data: dict) -> list:
    steps = data.get("steps", [])
    done_count = sum(1 for s in steps if s.get("status") == "done")
    pct = int(done_count / max(len(steps), 1) * 100)

    cols = {"done": [], "running": [], "pending": []}
    for i, step in enumerate(steps):
        status = step.get("status", "pending")
        col_key = status if status in cols else "pending"
        cols[col_key].append(
            {
                "type": "card",
                "id": f"step-{i}",
                "title": f"{_status_icon(status)} {step['name']}",
                "status": status,
            }
        )

    header = [
        {
            "type": "heading",
            "level": 2,
            "text": data.get("title", "Task Progress"),
            "id": "board-title",
        },
        {
            "type": "progress",
            "label": f"Overall — {done_count}/{len(steps)} steps",
            "pct": pct,
            "id": "overall-progress",
        },
    ]
    if data.get("elapsed"):
        header.append(
            {"type": "text", "text": f"⏱ Elapsed: {data['elapsed']}", "muted": True}
        )

    board = {
        "type": "board",
        "id": "task-board",
        "columns": [
            {"id": "col-done", "title": "✅ Done", "items": cols["done"]},
            {"id": "col-running", "title": "🔄 Running", "items": cols["running"]},
            {"id": "col-pending", "title": "⏳ Pending", "items": cols["pending"]},
        ],
    }
    return header + [board]


def _tpl_data_dashboard(data: dict) -> list:
    components: list = [
        {"type": "heading", "level": 2, "text": data.get("title", "Dashboard")}
    ]
    metrics = data.get("metrics", [])
    if metrics:
        components.append(
            {
                "type": "row",
                "id": "metrics-row",
                "children": [
                    {
                        "type": "metric",
                        "id": f"metric-{i}",
                        "label": m["label"],
                        "value": m["value"],
                        "trend": m.get("trend"),
                    }
                    for i, m in enumerate(metrics)
                ],
            }
        )
    chart = data.get("chart")
    if chart:
        components.append(
            {
                "type": "chart_line",
                "id": "dashboard-chart",
                "label": chart.get("label", "Chart"),
                "labels": chart.get("labels", []),
                "datasets": chart.get("datasets", []),
                "vertical_lines": chart.get("vertical_lines", []),
            }
        )
    table = data.get("table")
    if table:
        components.append(
            {
                "type": "table",
                "id": "dashboard-table",
                "headers": table.get("headers", []),
                "rows": table.get("rows", []),
            }
        )
    return components


def _tpl_config_form(data: dict) -> list:
    return [
        {"type": "heading", "level": 2, "text": data.get("title", "Configuration")},
        *(
            [{"type": "text", "text": data["description"]}]
            if data.get("description")
            else []
        ),
        {
            "type": "form",
            "id": "config-form",
            "fields": data.get("fields", []),
            "actions": [
                {
                    "type": "button",
                    "label": data.get("cancel_label", "Cancel"),
                    "action_id": "cancel",
                    "variant": "ghost",
                },
                {
                    "type": "button",
                    "label": data.get("submit_label", "Submit"),
                    "action_id": "submit",
                    "variant": "primary",
                },
            ],
        },
    ]


def _tpl_plan_view(data: dict) -> list:
    components: list = [
        {"type": "heading", "level": 2, "text": data.get("title", "Plan Review")}
    ]
    if data.get("description"):
        components.append({"type": "text", "text": data["description"]})
    components.append(
        {
            "type": "flowchart",
            "id": "plan-diagram",
            "content": data.get("mermaid", "flowchart TD\n  A[No diagram provided]"),
        }
    )
    components.append(
        {
            "type": "row",
            "children": [
                {
                    "type": "button",
                    "label": data.get("cancel_label", "Cancel"),
                    "action_id": "cancel",
                    "variant": "danger",
                },
                {
                    "type": "button",
                    "label": data.get("approve_label", "Approve & Execute"),
                    "action_id": "approve",
                    "variant": "primary",
                },
            ],
        }
    )
    return components
