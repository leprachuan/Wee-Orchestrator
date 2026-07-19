"""Session-scoped browser control for Wee native clients and WebUI.

Native clients long-poll a small authenticated command queue. When no native
browser is attached to a chat session, the same tool contract falls back to a
Playwright page if Playwright is installed on the API host.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4


@dataclass
class BrowserRegistration:
    identity: str
    channel: str
    client_id: str
    last_seen: float = field(default_factory=time.monotonic)


class NativeBrowserBroker:
    def __init__(self, active_ttl: float = 45.0):
        self.active_ttl = active_ttl
        self._condition = threading.Condition()
        self._registrations: dict[str, BrowserRegistration] = {}
        self._commands: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._results: dict[str, dict[str, Any]] = {}

    def register(
        self, session_id: str, identity: str, channel: str, client_id: str
    ) -> None:
        with self._condition:
            current = self._registrations.get(session_id)
            if current and (current.identity, current.channel) != (identity, channel):
                raise PermissionError("Browser session belongs to another user")
            self._registrations[session_id] = BrowserRegistration(
                identity=identity, channel=channel, client_id=client_id
            )
            self._condition.notify_all()

    def _registration(
        self, session_id: str, identity: str, channel: str, client_id: str
    ) -> BrowserRegistration:
        registration = self._registrations.get(session_id)
        if not registration or (
            registration.identity,
            registration.channel,
            registration.client_id,
        ) != (identity, channel, client_id):
            raise PermissionError("Native browser is not registered for this session")
        return registration

    def poll(
        self,
        session_id: str,
        identity: str,
        channel: str,
        client_id: str,
        timeout: float = 25.0,
    ) -> Optional[dict[str, Any]]:
        deadline = time.monotonic() + max(0.0, min(timeout, 30.0))
        with self._condition:
            registration = self._registration(
                session_id, identity, channel, client_id
            )
            registration.last_seen = time.monotonic()
            while not self._commands[session_id]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    registration.last_seen = time.monotonic()
                    return None
                self._condition.wait(remaining)
                registration = self._registration(
                    session_id, identity, channel, client_id
                )
                registration.last_seen = time.monotonic()
            return self._commands[session_id].popleft()

    def submit_result(
        self,
        session_id: str,
        identity: str,
        channel: str,
        client_id: str,
        command_id: str,
        result: Any = None,
        error: Optional[str] = None,
        url: Optional[str] = None,
        title: Optional[str] = None,
    ) -> None:
        with self._condition:
            registration = self._registration(
                session_id, identity, channel, client_id
            )
            registration.last_seen = time.monotonic()
            self._results[command_id] = {
                "result": result,
                "error": error,
                "url": url,
                "title": title,
            }
            self._condition.notify_all()

    def is_active(self, session_id: str) -> bool:
        with self._condition:
            registration = self._registrations.get(session_id)
            return bool(
                registration
                and time.monotonic() - registration.last_seen <= self.active_ttl
            )

    def execute(
        self, session_id: str, command: dict[str, Any], timeout: float = 45.0
    ) -> dict[str, Any]:
        command_id = str(uuid4())
        payload = {"id": command_id, **command}
        deadline = time.monotonic() + max(1.0, min(timeout, 90.0))
        with self._condition:
            if not self.is_active(session_id):
                raise RuntimeError("No native browser is connected to this session")
            self._commands[session_id].append(payload)
            self._condition.notify_all()
            while command_id not in self._results:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Native browser command timed out")
                self._condition.wait(remaining)
            result = self._results.pop(command_id)
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        return result


class PlaywrightBrowserManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._playwright = None
        self._browser = None
        self._pages: dict[str, Any] = {}

    def _page(self, session_id: str):
        with self._lock:
            if session_id in self._pages:
                return self._pages[session_id]
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "Browser control requires a connected macOS browser or "
                    "Playwright (`pip install playwright && playwright install chromium`)"
                ) from exc
            if self._playwright is None:
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(headless=True)
            context = self._browser.new_context()
            page = context.new_page()
            self._pages[session_id] = page
            return page

    def execute(self, session_id: str, command: dict[str, Any]) -> dict[str, Any]:
        page = self._page(session_id)
        action = str(command.get("action") or "snapshot").lower()
        if action == "navigate":
            url = str(command.get("url") or "").strip()
            if not url:
                raise ValueError("browser navigate requires url")
            if "://" not in url:
                url = "https://" + url
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        elif action == "click":
            selector = str(command.get("selector") or "").strip()
            text = str(command.get("text") or "").strip()
            if selector:
                page.locator(selector).first.click(timeout=15_000)
            elif text:
                page.get_by_text(text, exact=False).first.click(timeout=15_000)
            else:
                raise ValueError("browser click requires selector or text")
        elif action == "type":
            selector = str(command.get("selector") or "").strip()
            text = str(command.get("text") or "")
            if not selector:
                raise ValueError("browser type requires selector")
            page.locator(selector).first.fill(text, timeout=15_000)
            if command.get("submit"):
                page.locator(selector).first.press("Enter")
        elif action == "evaluate":
            script = str(command.get("script") or "").strip()
            if not script:
                raise ValueError("browser evaluate requires script")
            value = page.evaluate(script)
            return self._state(page, value)
        elif action == "back":
            page.go_back(wait_until="domcontentloaded")
        elif action == "forward":
            page.go_forward(wait_until="domcontentloaded")
        elif action == "reload":
            page.reload(wait_until="domcontentloaded")
        elif action != "snapshot":
            raise ValueError(f"Unknown browser action: {action}")
        return self._state(page)

    @staticmethod
    def _state(page, value: Any = None) -> dict[str, Any]:
        text = page.locator("body").inner_text(timeout=10_000)[:12_000]
        links = page.locator("a").evaluate_all(
            "els => els.slice(0, 50).map(a => ({text:(a.innerText||'').trim(), href:a.href}))"
        )
        return {
            "result": value,
            "url": page.url,
            "title": page.title(),
            "text": text,
            "links": links,
        }


native_browser_broker = NativeBrowserBroker()
playwright_browser_manager = PlaywrightBrowserManager()


def execute_browser_command(
    session_id: str, command: dict[str, Any], timeout: float = 45.0
) -> str:
    if native_browser_broker.is_active(session_id):
        response = native_browser_broker.execute(session_id, command, timeout=timeout)
        source = "native-macos"
    else:
        response = playwright_browser_manager.execute(session_id, command)
        source = "playwright"
    return json.dumps({"source": source, **response}, ensure_ascii=False)[:16_000]
