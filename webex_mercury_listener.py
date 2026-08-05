"""Webex inbound listener using the Mercury real-time transport (issue #487).

Feeds the SAME `WebEXConnector.handle_message()` used by the existing
RabbitMQ-queue path -- this module only replaces how a message *arrives*,
not how it's processed. That keeps behavior identical between the two
paths and means there is nothing new to validate downstream of enrichment.

Protocol shape (Webex's internal device-registration + Mercury pattern,
also used by community Webex bot SDKs):

1. POST to the WDM (Webex Device Management) service registers a "device"
   for this bot token and returns a `webSocketUrl`.
2. Open a secure WebSocket to that URL and send an `authorization` frame
   with the bot token as the first message.
3. Webex pushes `conversation.activity` events over that same socket for
   every room the bot is a member of. A `post` activity carries only a
   lightweight reference (ids), so the authoritative message content is
   fetched via `GET /v1/messages/{id}` -- the same endpoint the existing
   connector already calls for edit/pin, and the same message shape
   `handle_message()` already expects.

Disabled by default (`WEBEX_MERCURY_ENABLED` unset or falsy): the existing
message-queue listener remains the active path with zero behavior change.
Per issue #487's own phased-migration criteria, promoting this from "code
exists, flag off" to "serving live traffic" requires live validation
against a real Webex bot account and staged production monitoring over
real time -- neither of which a coding session can perform. This module is
the code-complete, feature-flagged, disabled-by-default deliverable that
phase asks for; enabling and monitoring it is a separate, human-in-the-loop
operational step.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import sys
import time
import uuid
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional

import requests
import websockets
from websockets.exceptions import ConnectionClosed


def _log(msg: str) -> None:
    """print(..., file=sys.stderr), matching this codebase's convention.

    Not the `logging` module: agent_manager.py and webex_connector.py both
    use plain stderr prints (the structured-logging migration in issue #31
    was reverted -- see #471), and a bare `logging.getLogger()` with no
    handler configured only surfaces WARNING+ via Python's last-resort
    handler, silently swallowing INFO-level connection/event logs here.
    """
    print(f"[webex_mercury] {msg}", file=sys.stderr, flush=True)

WDM_DEVICES_URL = "https://wdm-a.wbx2.com/wdm/api/v1/devices"
MESSAGES_API_URL = "https://webexapis.com/v1/messages/{message_id}"

DEVICE_DESCRIPTOR = {
    "deviceName": "wee-orchestrator-mercury",
    "deviceType": "DESKTOP",
    "localizedModel": "wee-orchestrator",
    "model": "wee-orchestrator",
    "name": "wee-orchestrator-mercury-listener",
    "systemName": "wee-orchestrator",
    "systemVersion": "1.0",
}


def is_enabled() -> bool:
    """Whether the Mercury listener should run at all.

    Default OFF: the existing message-queue listener is authoritative until
    this is explicitly turned on and validated. See module docstring.
    """
    return os.environ.get("WEBEX_MERCURY_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def decode_webex_resource_id(encoded_id: str) -> str:
    """Reverse a Webex API resource ID back to its raw UUID.

    REST endpoints like GET /v1/people/me return base64(ciscospark://us/
    PEOPLE/{uuid}) -- confirmed live: decoding the bot's own `id` from
    /v1/people/me yields exactly this shape. But Mercury's activity.actor.id
    is the bare UUID with no encoding at all. Comparing the two forms
    directly always mismatches, which is exactly how a first live test found
    this: the bot's own reply was never recognized as its own message and
    got re-ingested as a new query, in a loop.

    Falls back to returning `encoded_id` unchanged if it isn't valid base64
    or doesn't look like a Webex URN -- callers compare against Mercury's
    raw actor id either way, so an unrecognized shape just fails the
    equality check safely rather than raising.
    """
    try:
        padded = encoded_id + "=" * (-len(encoded_id) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return encoded_id
    if "/" not in decoded:
        return encoded_id
    return decoded.rsplit("/", 1)[-1]


def activity_id_to_message_id(activity_id: str) -> str:
    """Convert a Mercury activity's raw UUID into a Messages-API message ID.

    A `post` activity's `object.id` is NOT the message ID (`object` is the
    encrypted comment body, with no `id` field) -- confirmed by comparing a
    real Mercury frame's `activity.id` against the `id` the Messages API
    returned for that same message when it was sent: the API ID is the
    unpadded base64 encoding of `ciscospark://us/MESSAGE/{activity.id}`.
    This is the same scheme Webex uses for all its resource IDs (rooms,
    people, etc.), just with a different URN segment.
    """
    urn = f"ciscospark://us/MESSAGE/{activity_id}"
    return base64.b64encode(urn.encode()).decode()


def reconnect_delay(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """Exponential backoff with full jitter, capped.

    `attempt` is 0-indexed (first retry is attempt=0). Jitter is applied
    across the *entire* range [0, computed) rather than added on top, so
    many simultaneously-reconnecting listeners don't all retry in lockstep.
    """
    computed = min(cap, base * (2**attempt))
    return random.uniform(0, computed)


class BoundedSeenSet:
    """Bounded, insertion-ordered set for message-ID dedup.

    A plain set() would grow forever across a long-lived connection. This
    caps memory by evicting the oldest entries once the bound is exceeded,
    which is sufficient for dedup: a message redelivered long after the
    bound has rolled past it is vanishingly unlikely in practice, and the
    alternative (unbounded growth) is a guaranteed slow leak.
    """

    def __init__(self, max_size: int = 10_000):
        self._max_size = max_size
        self._seen: "OrderedDict[str, None]" = OrderedDict()

    def add_if_new(self, key: str) -> bool:
        """Return True if `key` was not already present (and record it)."""
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = None
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return True

    def __len__(self) -> int:
        return len(self._seen)


class WebexMercuryListener:
    """Owns one persistent Mercury WebSocket connection for a single bot token."""

    def __init__(
        self,
        token: str,
        on_message: Callable[[dict], Awaitable[bool] | bool],
        bot_person_id: Optional[str] = None,
        max_queue_size: int = 500,
    ):
        self.token = token
        self._on_message = on_message
        self._bot_person_id = bot_person_id
        self._seen = BoundedSeenSet()
        self._queue: "asyncio.Queue[dict]" = asyncio.Queue(maxsize=max_queue_size)
        self._stopping = asyncio.Event()
        self._connected = False
        self._last_event_at: Optional[float] = None
        self._reconnect_count = 0
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    # ---- health/readiness -------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    def health(self) -> dict:
        return {
            "connected": self._connected,
            "reconnect_count": self._reconnect_count,
            "last_event_at": self._last_event_at,
            "seen_cache_size": len(self._seen),
            "queue_depth": self._queue.qsize(),
        }

    # ---- device registration + socket lifecycle ---------------------------

    def _register_device(self) -> str:
        """Return this bot's `webSocketUrl`, registering a device if needed."""
        resp = self._session.post(WDM_DEVICES_URL, json=DEVICE_DESCRIPTOR, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        ws_url = data.get("webSocketUrl")
        if not ws_url:
            raise RuntimeError("WDM device registration did not return a webSocketUrl")
        return ws_url

    async def _authenticate_socket(self, ws) -> None:
        await ws.send(
            json.dumps(
                {
                    "id": str(uuid.uuid4()),
                    "type": "authorization",
                    "data": {"token": f"Bearer {self.token}"},
                }
            )
        )

    async def run_forever(self) -> None:
        """Connect, reconnect on failure, and process events until stop()."""
        attempt = 0
        while not self._stopping.is_set():
            try:
                ws_url = await asyncio.to_thread(self._register_device)
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                    await self._authenticate_socket(ws)
                    self._connected = True
                    attempt = 0
                    _log("Mercury socket connected")
                    async for raw in ws:
                        if self._stopping.is_set():
                            break
                        await self._handle_raw_event(raw)
            except (ConnectionClosed, OSError, requests.RequestException) as exc:
                _log(f"Mercury socket disconnected: {exc}")
            except Exception as exc:
                _log(f"Unexpected Mercury listener error: {type(exc).__name__}: {exc}")
            finally:
                self._connected = False

            if self._stopping.is_set():
                break
            self._reconnect_count += 1
            delay = reconnect_delay(attempt)
            attempt += 1
            _log(f"Reconnecting to Mercury in {delay:.1f}s (attempt {attempt})")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass  # normal path: delay elapsed, loop back and reconnect

    async def stop(self) -> None:
        """Signal the run loop to exit after the current iteration."""
        self._stopping.set()

    # ---- event handling -----------------------------------------------------

    async def _handle_raw_event(self, raw: str) -> None:
        self._last_event_at = time.time()
        _log(f"raw frame received (len={len(raw)})")
        try:
            envelope = json.loads(raw)
        except (TypeError, ValueError):
            _log("Discarding non-JSON Mercury frame")
            return

        activity = (envelope.get("data") or {}).get("activity") or {}
        verb = activity.get("verb")
        if verb != "post":
            _log(f"Ignoring non-post activity (verb={verb!r})")
            return  # not a new message (typing indicators, reactions, etc.)

        actor_id = (activity.get("actor") or {}).get("id")
        if self._bot_person_id and actor_id == self._bot_person_id:
            _log("Ignoring bot's own message")
            return  # ignore the bot's own messages

        activity_id = activity.get("id")
        if not activity_id:
            _log("Post activity had no id — cannot enrich, dropping")
            return
        message_id = activity_id_to_message_id(activity_id)

        if not self._seen.add_if_new(message_id):
            _log(f"Duplicate message_id={message_id}, skipping")
            return  # duplicate delivery of an event we already processed

        _log(f"Queuing message_id={message_id} for enrichment + dispatch")
        # Bounded backpressure: if downstream processing can't keep up, drop
        # the oldest queued item rather than growing without limit or
        # blocking the socket read loop (which would stall keepalive pings).
        if self._queue.full():
            try:
                self._queue.get_nowait()
                _log("Mercury processing queue full — dropped oldest event")
            except asyncio.QueueEmpty:
                pass
        await self._queue.put({"message_id": message_id})

    async def process_queue_forever(self) -> None:
        """Drain the event queue: enrich each message and dispatch it."""
        while not self._stopping.is_set() or not self._queue.empty():
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                message = await asyncio.to_thread(self._fetch_message, item["message_id"])
                if message is None:
                    _log(f"message_id={item['message_id']} fetch returned None (404?)")
                    continue
                _log(f"Dispatching message_id={item['message_id']} to handle_message()")
                result = self._on_message(message)
                if asyncio.iscoroutine(result):
                    result = await result
                _log(f"handle_message() returned {result!r} for message_id={item['message_id']}")
            except Exception as exc:
                _log(f"Failed processing Mercury message {item.get('message_id')}: {type(exc).__name__}: {exc}")

    def _fetch_message(self, message_id: str) -> Optional[dict]:
        """Enrich a Mercury activity reference via the Messages API.

        Mirrors what upstream webhook-based ingestion already does before a
        message reaches handle_message() -- full content, not just the
        notification-only reference Mercury delivers inline.
        """
        resp = self._session.get(MESSAGES_API_URL.format(message_id=message_id), timeout=15)
        if resp.status_code == 404:
            return None  # message deleted between event and fetch
        resp.raise_for_status()
        return resp.json()


async def run(
    token: str,
    on_message: Callable[[dict], Awaitable[bool] | bool],
    bot_person_id: Optional[str] = None,
) -> WebexMercuryListener:
    """Start a listener's socket-read and queue-processing loops as tasks.

    Returns the listener immediately (callers await/cancel the returned
    tasks themselves) so a caller can run this alongside other work, e.g.
    the existing queue listener during a migration window.
    """
    listener = WebexMercuryListener(token, on_message, bot_person_id=bot_person_id)
    asyncio.create_task(listener.run_forever())
    asyncio.create_task(listener.process_queue_forever())
    return listener
