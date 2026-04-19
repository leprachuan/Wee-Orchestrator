"""Extracted components from SessionManager for improved testability.

This module provides three focused helper classes that were previously
embedded inside the monolithic SessionManager:

- CliCommandHandler:  slash-command registry and dispatcher
- RuntimeExecutor:    strategy registry for per-runtime execution handlers
- StreamingManager:   per-session streaming queues and replay buffers
"""

import threading
import time
from typing import Any, Callable, Dict, List, Optional


class CliCommandHandler:
    """Registry and dispatcher for slash commands.

    Slash commands are pure-server operations that bypass the LLM.
    Each command maps to a handler callable and a human-readable
    description.
    """

    def __init__(self) -> None:
        self._registry: Dict[str, dict] = {}

    def register(self, command: str, handler: Callable, description: str) -> None:
        """Register a slash command with a handler callable."""
        self._registry[command] = {
            "handler": handler,
            "description": description,
        }

    def dispatch(
        self,
        command: str,
        argument: Optional[str],
        session_data: dict,
        session_id: str,
    ) -> Optional[str]:
        """Invoke the registered handler and return its result.

        Returns *None* when the command is not registered so the caller
        can fall through to legacy handling.
        """
        entry = self._registry.get(command)
        if entry is None:
            return None
        return entry["handler"](argument, session_data, session_id)

    def list_commands(self) -> Dict[str, str]:
        """Return a mapping of command -> description."""
        return {cmd: e["description"] for cmd, e in self._registry.items()}

    def has_command(self, command: str) -> bool:
        """True if *command* has a registered handler."""
        return command in self._registry

    def __len__(self) -> int:
        return len(self._registry)


class RuntimeExecutor:
    """Strategy registry for per-runtime execution handlers.

    Callers register a named executor (any callable) for each runtime.
    SessionManager delegates runtime-specific dispatch through this
    registry instead of a long if/elif chain.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, Any] = {}

    def register(self, runtime: str, handler: Any) -> None:
        """Register an executor callable for *runtime*."""
        self._handlers[runtime] = handler

    def get(self, runtime: str) -> Optional[Any]:
        """Return the executor for *runtime*, or *None* if not registered."""
        return self._handlers.get(runtime)

    def is_registered(self, runtime: str) -> bool:
        """True if an executor is registered for *runtime*."""
        return runtime in self._handlers

    def supported_runtimes(self) -> List[str]:
        """Return all registered runtime names."""
        return list(self._handlers.keys())

    def __len__(self) -> int:
        return len(self._handlers)


class StreamBuffer:
    """Thread-safe buffer that stores stream chunks and broadcasts to consumers.

    Supports multiple concurrent SSE consumers per session.  When a client
    disconnects and reconnects, the reconnect endpoint replays buffered
    chunks and then subscribes to live updates.
    """

    def __init__(self) -> None:
        self.chunks: list = []
        self.finished: bool = False
        self.done_result: Optional[str] = None
        self.created_at: float = time.time()
        self._consumers: list = []
        self._lock = threading.Lock()

    def push(self, kind: str, data: Any) -> None:
        """Push a chunk from the subprocess thread.

        Appends to buffer and forwards to all registered consumer queues.
        """
        with self._lock:
            idx = len(self.chunks)
            self.chunks.append((kind, data))
            if kind == "done":
                self.finished = True
                self.done_result = data
            for q, lp, start_idx in self._consumers:
                if idx >= start_idx:
                    try:
                        lp.call_soon_threadsafe(q.put_nowait, (kind, data))
                    except Exception:
                        pass

    def add_consumer(self, queue: Any, loop: Any) -> int:
        """Register a new SSE consumer.

        Returns the replay index — the caller should replay
        ``self.chunks[:replay_index]`` before draining the queue.
        """
        with self._lock:
            replay_index = len(self.chunks)
            self._consumers.append((queue, loop, replay_index))
            return replay_index

    def remove_consumer(self, queue: Any) -> None:
        """Remove a consumer queue (e.g. on SSE disconnect)."""
        with self._lock:
            self._consumers = [
                (q, lp, si) for q, lp, si in self._consumers if q is not queue
            ]

    def get_replay_chunks(self, up_to: int) -> list:
        """Return a copy of buffered chunks up to *up_to* index."""
        with self._lock:
            return list(self.chunks[:up_to])

    def has_consumers(self) -> bool:
        """True if at least one SSE consumer is connected."""
        with self._lock:
            return len(self._consumers) > 0


class StreamingManager:
    """Manages per-session streaming queues and replay buffers.

    Previously this logic was embedded directly in SessionManager.  By
    extracting it here the streaming subsystem can be tested and replaced
    independently.
    """

    def __init__(self) -> None:
        self._queues: Dict[str, tuple] = {}
        self._buffers: Dict[str, StreamBuffer] = {}

    def get_or_create_buffer(self, session_id: str) -> StreamBuffer:
        """Return the existing buffer for *session_id* or create a new one."""
        buf = self._buffers.get(session_id)
        if buf is None:
            buf = StreamBuffer()
            self._buffers[session_id] = buf
        return buf

    def get_buffer(self, session_id: str) -> Optional[StreamBuffer]:
        """Return the buffer for *session_id* without creating one."""
        return self._buffers.get(session_id)

    def get_queue(self, session_id: str) -> Optional[tuple]:
        """Return the (queue, loop) tuple for *session_id*, or *None*."""
        return self._queues.get(session_id)

    def register_stream(self, session_id: str, queue: Any, loop: Any) -> None:
        """Register an asyncio queue for the /stream endpoint."""
        self._queues[session_id] = (queue, loop)
        buf = self.get_or_create_buffer(session_id)
        buf.add_consumer(queue, loop)

    def unregister_stream(self, session_id: str, queue: Optional[Any] = None) -> None:
        """Remove the streaming queue for a session.

        If *queue* is provided, only remove that consumer from the buffer
        (the buffer itself stays alive for reconnection).  The legacy
        ``_queues`` entry is removed regardless so that new streams can
        register without conflict.
        """
        self._queues.pop(session_id, None)
        buf = self._buffers.get(session_id)
        if buf is not None and queue is not None:
            buf.remove_consumer(queue)

    def cleanup_buffer(self, session_id: str) -> None:
        """Remove the stream buffer entirely (called after query completes)."""
        self._buffers.pop(session_id, None)

    def cleanup_stale_buffers(self, max_age: float = 600.0) -> None:
        """Remove finished buffers older than *max_age* seconds."""
        now = time.time()
        stale = [
            sid
            for sid, buf in self._buffers.items()
            if buf.finished and (now - buf.created_at) > max_age
        ]
        for sid in stale:
            self._buffers.pop(sid, None)
