"""Valkey-backed task queue for Always-On Agents.

Provides per-agent named queues backed by Valkey (Redis-compatible).
Falls back gracefully to None operations when Valkey is unavailable,
allowing the existing SQLite polling mechanism to continue working.
"""

import json
import logging
import os
import time
from typing import Optional

log = logging.getLogger("aoa-queue")

# Lazy import — redis package may not be installed everywhere
_redis_mod = None


def _get_redis():
    global _redis_mod
    if _redis_mod is None:
        try:
            import redis as _r
            _redis_mod = _r
        except ImportError:
            log.warning("Python 'redis' package not installed — Valkey queue disabled")
            return None
    return _redis_mod


class TaskQueue:
    """Per-agent task queue backed by Valkey.

    Each agent has a Redis list named ``{agent}:tasks`` that holds task IDs.
    A counter key ``{agent}:running`` tracks how many tasks are currently
    executing.  A hash ``task:{task_id}`` stores lightweight task metadata
    for fast access without hitting SQLite.

    If Valkey is unreachable, every method returns a sensible default so
    the caller can fall back to SQLite polling transparently.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
    ):
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._client: Optional[object] = None
        self._available: Optional[bool] = None

    # ── Connection management ───────────────────────────────────────────

    def _connect(self):
        """Lazily connect to Valkey.  Returns the client or None."""
        if self._client is not None:
            return self._client
        redis = _get_redis()
        if redis is None:
            self._available = False
            return None
        try:
            self._client = redis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                password=self._password,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            self._client.ping()
            self._available = True
            log.info("Connected to Valkey at %s:%d", self._host, self._port)
            return self._client
        except Exception as exc:
            log.warning("Valkey connection failed (%s) — falling back to SQLite polling", exc)
            self._client = None
            self._available = False
            return None

    @property
    def available(self) -> bool:
        """True if Valkey is reachable."""
        if self._available is None:
            self._connect()
        return bool(self._available)

    def health_check(self) -> dict:
        """Return connection health status."""
        client = self._connect()
        if client is None:
            return {"status": "unavailable", "host": self._host, "port": self._port}
        try:
            info = client.info("server")
            return {
                "status": "healthy",
                "host": self._host,
                "port": self._port,
                "valkey_version": info.get("redis_version", info.get("valkey_version", "unknown")),
                "used_memory_human": info.get("used_memory_human", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
            }
        except Exception as exc:
            self._available = False
            self._client = None
            return {"status": "error", "error": str(exc)}

    # ── Queue operations ────────────────────────────────────────────────

    def push_task(self, agent: str, task_id: str, metadata: Optional[dict] = None) -> bool:
        """Push a task ID onto the agent's queue.  Returns True on success."""
        client = self._connect()
        if client is None:
            return False
        try:
            pipe = client.pipeline(transaction=True)
            # Store lightweight metadata for quick access
            if metadata:
                pipe.hset(f"task:{task_id}", mapping={
                    k: str(v) if v is not None else ""
                    for k, v in metadata.items()
                })
                pipe.expire(f"task:{task_id}", 86400)  # TTL 24h
            # Push to the agent's task queue (left-push, right-pop = FIFO)
            pipe.lpush(f"{agent}:tasks", task_id)
            pipe.execute()
            log.info("Pushed task %s to queue %s:tasks", task_id, agent)
            return True
        except Exception as exc:
            log.warning("Failed to push task %s to Valkey: %s", task_id, exc)
            self._reconnect_on_error()
            return False

    def pop_task(self, agents: list[str], timeout: int = 2) -> Optional[tuple[str, str]]:
        """Blocking pop from multiple agent queues.

        Returns ``(agent_name, task_id)`` or ``None`` after timeout.
        Uses ``BRPOP`` across all agent queues for efficient multiplexed
        waiting.
        """
        client = self._connect()
        if client is None:
            return None
        keys = [f"{a}:tasks" for a in agents]
        if not keys:
            return None
        try:
            result = client.brpop(keys, timeout=timeout)
            if result is None:
                return None
            queue_key, task_id = result
            # Extract agent name from key (format: "agent:tasks")
            agent_name = queue_key.rsplit(":tasks", 1)[0]
            return (agent_name, task_id)
        except Exception as exc:
            log.warning("BRPOP failed: %s", exc)
            self._reconnect_on_error()
            return None

    def get_task_metadata(self, task_id: str) -> Optional[dict]:
        """Retrieve cached task metadata from Valkey."""
        client = self._connect()
        if client is None:
            return None
        try:
            data = client.hgetall(f"task:{task_id}")
            return data if data else None
        except Exception:
            return None

    # ── Concurrency control ─────────────────────────────────────────────

    def get_running_count(self, agent: str) -> int:
        """Get current running task count for an agent."""
        client = self._connect()
        if client is None:
            return -1  # Sentinel: caller should fall back to SQLite
        try:
            val = client.get(f"{agent}:running")
            return int(val) if val else 0
        except Exception:
            return -1

    def increment_running(self, agent: str) -> int:
        """Atomically increment running count.  Returns new value."""
        client = self._connect()
        if client is None:
            return -1
        try:
            return client.incr(f"{agent}:running")
        except Exception:
            return -1

    def decrement_running(self, agent: str) -> int:
        """Atomically decrement running count (floor 0).  Returns new value."""
        client = self._connect()
        if client is None:
            return -1
        try:
            new_val = client.decr(f"{agent}:running")
            if new_val < 0:
                client.set(f"{agent}:running", 0)
                return 0
            return new_val
        except Exception:
            return -1

    def set_running_count(self, agent: str, count: int) -> bool:
        """Set running count directly (used for reconciliation)."""
        client = self._connect()
        if client is None:
            return False
        try:
            client.set(f"{agent}:running", max(0, count))
            return True
        except Exception:
            return False

    # ── Queue introspection ─────────────────────────────────────────────

    def queue_length(self, agent: str) -> int:
        """Number of pending tasks in the agent's queue."""
        client = self._connect()
        if client is None:
            return -1
        try:
            return client.llen(f"{agent}:tasks")
        except Exception:
            return -1

    def queue_stats(self) -> dict:
        """Get stats for all agent queues."""
        client = self._connect()
        if client is None:
            return {}
        try:
            stats = {}
            # Scan for agent queue keys
            for key in client.scan_iter(match="*:tasks", count=100):
                agent = key.rsplit(":tasks", 1)[0]
                pending = client.llen(key)
                running_val = client.get(f"{agent}:running")
                running = int(running_val) if running_val else 0
                stats[agent] = {"pending": pending, "running": running}
            return stats
        except Exception as exc:
            log.warning("Failed to get queue stats: %s", exc)
            return {}

    # ── Notification / pub-sub ──────────────────────────────────────────

    def notify_new_task(self, agent: str, task_id: str) -> bool:
        """Publish a notification that a new task is available.

        Used as a wake-up signal for daemons using pub/sub instead of
        BRPOP (e.g., when they need to handle other events too).
        """
        client = self._connect()
        if client is None:
            return False
        try:
            client.publish(f"aoa:new_task:{agent}", task_id)
            return True
        except Exception:
            return False

    # ── Cleanup ─────────────────────────────────────────────────────────

    def remove_task(self, task_id: str) -> bool:
        """Remove task metadata from Valkey after completion."""
        client = self._connect()
        if client is None:
            return False
        try:
            client.delete(f"task:{task_id}")
            return True
        except Exception:
            return False

    def flush_agent_queue(self, agent: str) -> bool:
        """Clear an agent's pending queue (admin operation)."""
        client = self._connect()
        if client is None:
            return False
        try:
            client.delete(f"{agent}:tasks")
            client.set(f"{agent}:running", 0)
            return True
        except Exception:
            return False

    # ── Internal helpers ────────────────────────────────────────────────

    def _reconnect_on_error(self):
        """Reset connection state so next call attempts reconnect."""
        self._client = None
        self._available = None


# ── Module-level singleton ──────────────────────────────────────────────

_default_queue: Optional[TaskQueue] = None


def get_task_queue() -> TaskQueue:
    """Get or create the module-level TaskQueue singleton."""
    global _default_queue
    if _default_queue is None:
        _default_queue = TaskQueue(
            host=os.environ.get("VALKEY_HOST", "127.0.0.1"),
            port=int(os.environ.get("VALKEY_PORT", "6379")),
            db=int(os.environ.get("VALKEY_DB", "0")),
            password=os.environ.get("VALKEY_PASSWORD"),
        )
    return _default_queue
